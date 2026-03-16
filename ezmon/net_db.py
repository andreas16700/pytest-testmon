"""
NetDB mode - Download/upload SQLite DB from/to remote server.

With DepStore, all mid-session operations are in-memory dict lookups.
The DB is only needed at session start (preload) and session end (flush).
For CI/CD, we download the per-job SQLite file, run locally with db.DB + DepStore,
and upload at session end.
"""
import os
from typing import Optional, Dict

import requests

from ezmon.common import get_logger

logger = get_logger(__name__)


def get_net_db_config() -> Optional[Dict[str, Optional[str]]]:
    """
    Check env vars for NetDB mode configuration.

    Returns config dict if enabled, None otherwise.

    Required env vars:
        TESTMON_NET_ENABLED: Set to 'true' to enable
        TESTMON_SERVER: Server URL (e.g., 'https://your-server.com')
        REPO_ID or GITHUB_REPOSITORY: Repository identifier
        JOB_ID: Job/variant identifier

    Optional:
        TESTMON_AUTH_TOKEN: Authentication token
        RUN_ID or GITHUB_RUN_ID: CI run ID
    """
    if os.environ.get("TESTMON_NET_ENABLED", "").lower() != "true":
        return None

    server_url = os.environ.get("TESTMON_SERVER")
    if not server_url:
        logger.warning("TESTMON_NET_ENABLED is true but TESTMON_SERVER is not set")
        return None

    repo_id = os.environ.get("REPO_ID") or os.environ.get("GITHUB_REPOSITORY")
    if not repo_id:
        logger.warning("TESTMON_NET_ENABLED is true but REPO_ID/GITHUB_REPOSITORY is not set")
        return None

    job_id = os.environ.get("JOB_ID")
    if not job_id:
        logger.warning("TESTMON_NET_ENABLED is true but JOB_ID is not set")
        return None

    auth_token = os.environ.get("TESTMON_AUTH_TOKEN")
    run_id = os.environ.get("RUN_ID") or os.environ.get("GITHUB_RUN_ID")

    return {
        "server_url": server_url.rstrip("/"),
        "repo_id": repo_id,
        "job_id": job_id,
        "auth_token": auth_token,
        "run_id": run_id,
    }


def download_db_from_server(
    server_url: str,
    repo_id: str,
    job_id: str,
    auth_token: Optional[str],
    dest_path: str,
) -> bool:
    """
    Download per-job SQLite DB from the server.

    Args:
        server_url: Base URL of the server
        repo_id: Repository identifier
        job_id: Job/variant identifier
        auth_token: Bearer token (or None)
        dest_path: Local path to write the downloaded DB

    Returns:
        True if download succeeded, False otherwise (fresh DB will be created)
    """
    url = f"{server_url}/api/client/download"
    params = {"repo_id": repo_id, "job_id": job_id}
    headers = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    logger.info(f"Downloading DB from server: repo={repo_id}, job={job_id}")

    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
        if response.status_code == 404:
            logger.info("No existing DB on server (first run) - will create fresh")
            return False
        response.raise_for_status()

        with open(dest_path, "wb") as f:
            f.write(response.content)

        logger.info(f"Downloaded DB ({len(response.content):,} bytes)")
        return True

    except requests.exceptions.RequestException as e:
        logger.warning(f"Download failed: {e} - will create fresh DB")
        return False


def upload_db_to_server(
    server_url: str,
    repo_id: str,
    job_id: str,
    auth_token: Optional[str],
    run_id: Optional[str],
    source_path: str,
) -> bool:
    """
    Upload modified SQLite DB to the server after test run.

    Args:
        server_url: Base URL of the server
        repo_id: Repository identifier
        job_id: Job/variant identifier
        auth_token: Bearer token (or None)
        run_id: CI run ID for linkage
        source_path: Local path of the DB to upload

    Returns:
        True if upload succeeded, False otherwise
    """
    url = f"{server_url}/api/client/upload"
    headers = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    logger.info(f"Uploading DB to server: repo={repo_id}, job={job_id}")

    try:
        with open(source_path, "rb") as f:
            files = {"file": (".testmondata", f, "application/octet-stream")}
            data = {
                "repo_id": repo_id,
                "job_id": job_id,
                "run_id": run_id or "",
                "repo_name": repo_id,
            }
            response = requests.post(
                url, files=files, data=data, headers=headers, timeout=30
            )
            response.raise_for_status()

        logger.info("Upload successful")
        return True

    except requests.exceptions.RequestException as e:
        logger.warning(f"Upload failed: {e} - local file preserved at {source_path}")
        return False
