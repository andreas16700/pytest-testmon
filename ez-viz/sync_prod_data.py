#!/usr/bin/env python3
"""
Sync production testmon data (repo → job → run)
"""
import requests
import json
from pathlib import Path
import hashlib

PROD_URL = "https://ezmon.aloiz.ch"
LOCAL_DATA_DIR = Path("./testmon_data")
LOCAL_METADATA_FILE = LOCAL_DATA_DIR / "metadata.json"


def sync_production_data():
    print("🔄 Syncing production data to local...")

    LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)

    try:
        # Fetch repository metadata from server
        print(f"📡 Fetching repos from {PROD_URL}...")
        resp = requests.get(f"{PROD_URL}/api/repos", timeout=10)
        resp.raise_for_status()

        data = resp.json()                     # THIS IS LIST FORMAT
        repos_list = data.get("repos", [])

        print(f"✅ Found {len(repos_list)} repositories")

        # =====================================================
        # Convert metadata → expected dict format for server
        # =====================================================
        metadata = {"repos": {}}

        for repo in repos_list:
            repo_id = repo["id"]
            repo_name = repo["name"]

            metadata["repos"][repo_id] = {
                "created": repo.get("created"),
                "name": repo_name,
                "jobs": {}
            }

            for job in repo.get("jobs", []):
                job_id = job["id"]

                metadata["repos"][repo_id]["jobs"][job_id] = {
                    "created": job.get("created"),
                    "name": job.get("name", job_id),
                    "runs": {}
                }

                for run in job.get("runs", []):
                    run_id = run["id"]
                    metadata["repos"][repo_id]["jobs"][job_id]["runs"][run_id] = {
                        "id": run_id,
                        "created": run["created"],
                        "last_updated": run["last_updated"],
                        "upload_count": run["upload_count"],
                    }

        # Save normalized metadata
        with open(LOCAL_METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        print(f"💾 Metadata saved → {LOCAL_METADATA_FILE}")

        # =====================================================
        # Download all runs
        # =====================================================
        downloaded = 0

        for repo_id, repo_data in metadata["repos"].items():
            safe_repo_id = hashlib.sha256(repo_id.encode()).hexdigest()[:16]
            print(f"\n📦 Repository: {repo_id}")

            for job_id, job_data in repo_data["jobs"].items():
                safe_job_id = "".join(c for c in job_id if c.isalnum() or c in ("-", "_"))
                print(f"  📁 Job: {job_id}")

                for run_id in job_data["runs"].keys():
                    print(f"    ⏳ Run: {run_id} ...", end=" ")

                    dl_url = f"{PROD_URL}/api/client/download"
                    params = {
                        "repo_id": repo_id,
                        "job_id": job_id,
                        "run_id": run_id,
                    }

                    try:
                        db_resp = requests.get(dl_url, params=params, timeout=30)

                        if db_resp.status_code == 200:
                            # Save in repo/job/run folder
                            run_dir = LOCAL_DATA_DIR / safe_repo_id / safe_job_id / run_id
                            run_dir.mkdir(parents=True, exist_ok=True)

                            db_path = run_dir / ".testmondata"
                            db_path.write_bytes(db_resp.content)

                            size_mb = len(db_resp.content) / 1024 / 1024
                            print(f"✅ ({size_mb:.2f} MB)")

                            downloaded += 1

                        elif db_resp.status_code == 404:
                            print("⚠ No data found")

                        else:
                            print(f"❌ Error {db_resp.status_code}")

                    except Exception as e:
                        print(f"❌ Failed: {e}")

        print(f"\n✨ Sync complete → {downloaded} run databases downloaded")
        print(f"📁 Saved to: {LOCAL_DATA_DIR.resolve()}")
        print("🚀 Ready to run local server")

    except Exception as e:
        print(f"❌ Unexpected error: {e}")


if __name__ == "__main__":
    sync_production_data()
