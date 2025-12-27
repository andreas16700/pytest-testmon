"""
Testmon Multi-Project/Job Visualization Server with Extensive Logging
"""
import secrets
from flask import (
    Flask,
    request,
    session,
    redirect,
    jsonify,
    send_file,
    send_from_directory,
    g,
    has_request_context,
)
import requests
from pathlib import Path
from flask_cors import CORS
from dotenv import load_dotenv
import sqlite3
import json
import os
from typing import Optional, Dict
from datetime import datetime
import hashlib
import logging
import sys
import time
import uuid
from functools import wraps
import traceback
from urllib.parse import urlencode
import array
from openai import OpenAI

EZMON_FP_DIR = Path(os.getenv("EZMON_FP_DIR", "./.ezmon-fp")).resolve()
CURRENT_MODEL = "gpt-4o-mini"
# -----------------------------------------------------------------------------
# Logging helpers
# -----------------------------------------------------------------------------
def human_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    f = float(n)
    while f >= 1024 and i < len(units) - 1:
        f /= 1024.0
        i += 1
    return f"{f:.2f}{units[i]}"

def now_iso() -> str:
    return datetime.utcnow().isoformat()

def log_exception(context: str, **extra):
    exc_type, exc, _ = sys.exc_info()
    logging.getLogger("testmon").error(
        f"{context} error={getattr(exc_type, '__name__', 'Exception')} detail={exc} extra={extra}"
    )

# -----------------------------------------------------------------------------
# Logging setup (safe for Werkzeug/gunicorn records)
# -----------------------------------------------------------------------------
class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Safe defaults for all records (startup, gunicorn, werkzeug, etc.)
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        if not hasattr(record, "repo_id"):
            record.repo_id = "-"
        if not hasattr(record, "job_id"):
            record.job_id = "-"

        # If we’re inside a Flask request, enrich from g
        try:
            if has_request_context():
                record.request_id = getattr(g, "request_id", record.request_id)
                record.repo_id = getattr(g, "repo_id", record.repo_id)
                record.job_id = getattr(g, "job_id", record.job_id)
        except Exception:
            # Never let logging crash the app
            pass
        return True

def setup_logging(level=logging.INFO):
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt=(
            "ts=%(asctime)s level=%(levelname)s req_id=%(request_id)s "
            "repo=%(repo_id)s job=%(job_id)s event=%(message)s"
        ),
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    handler.setFormatter(formatter)
    handler.addFilter(ContextFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

setup_logging()
log = logging.getLogger("testmon")

# -----------------------------------------------------------------------------
# Flask app + config
# -----------------------------------------------------------------------------
app = Flask(__name__)
load_dotenv()
app.secret_key = os.environ.get("FLASK_SECRET_KEY")
allowed_origin = os.environ.get("ORIGIN")
CORS(app,
     supports_credentials=True,
     origins=allowed_origin,
     allow_headers=["Content-Type"],
     methods=["GET", "POST", "OPTIONS"])

BASE_DATA_DIR = Path(os.getenv("TESTMON_DATA_DIR", "./testmon_data"))
BASE_DATA_DIR.mkdir(parents=True, exist_ok=True)
METADATA_FILE = BASE_DATA_DIR / "metadata.json"

# -----------------------------------------------------------------------------
# Request lifecycle logging
# -----------------------------------------------------------------------------
@app.before_request
def seed_request_context():
    # Correlation + defaults (repo/job filled by endpoints when known)
    g.request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    g.repo_id = "-"
    g.job_id = "-"
    g.t_start = time.perf_counter()
    log.info(
        "request_started method=%s path=%s remote_addr=%s ua=%s",
        request.method,
        request.path,
        request.remote_addr,
        request.user_agent,
    )

@app.after_request
def after(resp):
    latency_ms = int(
        (time.perf_counter() - getattr(g, "t_start", time.perf_counter())) * 1000
    )
    log.info(
        "request_finished method=%s path=%s status=%s latency_ms=%s",
        request.method,
        request.path,
        resp.status_code,
        latency_ms,
    )
    resp.headers["X-Request-ID"] = g.request_id
    return resp

@app.teardown_request
def teardown_request(exc):
    if exc:
        # Log uncaught exceptions
        log.error(
            "unhandled_exception path=%s exc=%s trace=%s",
            request.path,
            exc,
            traceback.format_exc(),
        )

# -----------------------------------------------------------------------------
# Metadata storage with logging
# -----------------------------------------------------------------------------
def get_metadata() -> Dict:
    """Load metadata about all repos and jobs"""
    try:
        if METADATA_FILE.exists():
            log.info("metadata_read_attempt path=%s", METADATA_FILE)
            with open(METADATA_FILE, "r") as f:
                data = json.load(f)
            size = METADATA_FILE.stat().st_size
            log.info(
                "metadata_read_success path=%s size=%s (%s)",
                METADATA_FILE,
                size,
                human_bytes(size),
            )
            return data
        else:
            log.info("metadata_missing path=%s", METADATA_FILE)
            return {"repos": {}}
    except Exception:
        log_exception("metadata_read", path=str(METADATA_FILE))
        return {"repos": {}}

def save_metadata(metadata: Dict):
    """Save metadata about all repos and jobs"""
    try:
        tmp = METADATA_FILE.with_suffix(".json.tmp")
        log.info("metadata_write_attempt path=%s tmp=%s", METADATA_FILE, tmp)
        with open(tmp, "w") as f:
            json.dump(metadata, f, indent=2)
        os.replace(tmp, METADATA_FILE)  # atomic on POSIX
        size = METADATA_FILE.stat().st_size
        log.info(
            "metadata_write_success path=%s size=%s (%s)",
            METADATA_FILE,
            size,
            human_bytes(size),
        )
    except Exception:
        log_exception("metadata_write", path=str(METADATA_FILE))

# -----------------------------------------------------------------------------
# Path helpers with logging
# -----------------------------------------------------------------------------
def get_repo_path(repo_id: str) -> Path:
    """Get path for a repository's data directory"""
    safe_repo_id = hashlib.sha256(repo_id.encode()).hexdigest()[:16]
    repo_path = BASE_DATA_DIR / safe_repo_id
    if not repo_path.exists():
        log.info(
            "repo_dir_create_attempt repo_id=%s safe_repo=%s path=%s",
            repo_id,
            safe_repo_id,
            repo_path,
        )
        repo_path.mkdir(parents=True, exist_ok=True)
        log.info("repo_dir_create_success path=%s", repo_path)
    return repo_path

def get_job_db_path(repo_id: str, job_id: str) -> Path:
    """Get path for a specific job's testmon database"""
    repo_path = get_repo_path(repo_id)
    safe_job_id = "".join(c for c in job_id if c.isalnum() or c in ("-", "_"))
    job_path = repo_path / safe_job_id
    if not job_path.exists():
        log.info(
            "job_dir_create_attempt repo_id=%s job_id=%s safe_job_id=%s path=%s",
            repo_id,
            job_id,
            safe_job_id,
            job_path,
        )
        job_path.mkdir(parents=True, exist_ok=True)
        log.info("job_dir_create_success path=%s", job_path)
    db_path = job_path / ".testmondata"
    log.info("job_db_resolve repo_id=%s job_id=%s db_path=%s", repo_id, job_id, db_path)
    return db_path

def register_repo_job(repo_id: str, job_id: str, repo_name: Optional[str] = None):
    """Register a new repo/job combination in metadata"""
    try:
        log.info(
            "register_repo_job repo_id=%s job_id=%s repo_name=%s",
            repo_id,
            job_id,
            repo_name,
        )
        metadata = get_metadata()

        if repo_id not in metadata["repos"]:
            metadata["repos"][repo_id] = {
                "name": repo_name or repo_id,
                "created": now_iso(),
                "jobs": {},
            }
            log.info("metadata_add_repo repo_id=%s", repo_id)

        if job_id not in metadata["repos"][repo_id]["jobs"]:
            metadata["repos"][repo_id]["jobs"][job_id] = {
                "created": now_iso(),
                "last_updated": now_iso(),
                "upload_count": 0,
            }
            log.info("metadata_add_job repo_id=%s job_id=%s", repo_id, job_id)

        save_metadata(metadata)
    except Exception:
        log_exception("register_repo_job", repo_id=repo_id, job_id=job_id)

@app.route("/api/ask_ai", methods=["POST"])
def leverage_ai_model():
    data = request.get_json()
    content = data.get("content")
    if not content:
        return jsonify({"error": "No content provided"}), 400
    api_key = os.getenv("AI_GITHUB_TOKEN")
    if not api_key:
        print("}Error: GITHUB_TOKEN environment variable not set.")
        return

    client = OpenAI(
        base_url="https://models.inference.ai.azure.com",
        api_key=api_key,
    )

    print(f"--- Using {CURRENT_MODEL}")

    user_prompt = (
        "You are an expert GitHub Actions engineer. Update the following workflow file "
        "to include a step that runs the 'testmon' plugin using the command: 'pytest --ezmon'. "
        "Return ONLY the updated YAML content. Do not include markdown formatting (```yaml) or explanations.\n\n"
        f"{content}"
    )
    print(f"\nConnecting to {CURRENT_MODEL}... \n")

    try:
        response = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": user_prompt}
            ],
            model=CURRENT_MODEL,
            temperature=0.1,
            max_tokens=4096,
            stream=False
        )

        updated_content = response.choices[0].message.content
        return jsonify({"content": updated_content})

    except Exception as e:
        print(f"AI Error: {e}")
        return jsonify({"error": str(e)}), 500

# -----------------------------------------------------------------------------
# SQLite with logging
# -----------------------------------------------------------------------------
def get_db_connection(db_path: Path, readonly: bool = True):
    mode = "ro" if readonly else "rwc"
    abs_path = os.path.abspath(str(db_path))
    log.info("db_connect_attempt path=%s abs_path=%s readonly=%s", db_path, abs_path, readonly)
    try:
        conn = sqlite3.connect(f"file:{abs_path}?mode={mode}", uri=True, timeout=60)
        log.info("db_connect_success path=%s", db_path)
        return conn
    except Exception:
        log_exception("db_connect", path=abs_path, readonly=readonly, mode=mode)
        raise

# -----------------------------------------------------------------------------
# API ENDPOINTS - Client Operations (GitHub Actions)
# -----------------------------------------------------------------------------
CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID")
CALLBACK = os.environ.get("GITHUB_OAUTH_CALLBACK")
FRONTEND_URL = os.environ.get("FRONTEND_URL")
CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET")

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "github_token" not in session:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

@app.route("/auth/github/login")
def github_login():
    state = secrets.token_urlsafe(16)
    session["oauth_state"] = state
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": CALLBACK,
        "scope": "repo read:user user:email",
        "state": state,
        "allow_signup": "false"
    }
    return redirect(f"https://github.com/login/oauth/authorize?{urlencode(params)}")

@app.route("/auth/github/callback")
def github_callback():
    if request.args.get("state") != session.get("oauth_state"):
        return jsonify({"error": "State mismatch"}), 403

    code = request.args.get("code")
    if not code:
        return jsonify({"error": "No code provided"}), 400

    token_resp = requests.post(
        "https://github.com/login/oauth/access_token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "redirect_uri": CALLBACK
        },
        headers={"Accept": "application/json"}
    )
    token_data = token_resp.json()

    if "access_token" not in token_data:
        return jsonify({"error": "Failed to get access token"}), 400

    session["github_token"] = token_data["access_token"]

    user_resp = requests.get(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {session['github_token']}",
            "Accept": "application/vnd.github+json"
        }
    )
    session["github_user"] = user_resp.json()

    session.pop("oauth_state", None)

    return redirect(f"{FRONTEND_URL}")

@app.route("/auth/user")
@login_required
def get_current_user():
    return jsonify(session.get("github_user"))

@app.route("/auth/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"message": "Logged out"})

def update_testmon_run_id(db_path, run_id):
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        # Insert run id if not existing
        cursor.execute(
            "INSERT OR IGNORE INTO run_uid (id) VALUES (?)",
            (run_id,)
        )

        # Update only NULL values
        cursor.execute(
            "UPDATE run_infos SET run_uid = ? WHERE run_uid IS NULL",
            (run_id,)
        )
        cursor.execute(
            "UPDATE test_infos SET run_uid = ? WHERE run_uid IS NULL",
            (run_id,)
        )
        cursor.execute(
            "UPDATE file_fp_infos SET run_uid = ? WHERE run_uid IS NULL",
            (run_id,)
        )
        cursor.execute(
            "UPDATE test_execution_file_fp_infos SET run_uid = ? WHERE run_uid IS NULL",
            (run_id,)
        )

        affected = cursor.rowcount
        conn.commit()
        log.info(f"Successfully updated run_uid for {affected} rows in file: {db_path}")
        return affected

    except Exception as e:
        log.error(f"Error updating testmon run_id for file {db_path}: {e}")
        return None

    finally:
        if 'conn' in locals():
            conn.close()

def add_run_id_to_testmon_data(db_path, run_id):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE run_uid SET repo_run_id=? WHERE repo_run_id IS NULL",
            (run_id,)
        )
        conn.commit()
       
    except Exception as e:
        log.error("Error updating run_ids for file %s: %s", db_path, e)
        return []
    finally:
        conn.close()
                
def get_run_infos(db_path):
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Adjust table/column names if needed
        cursor.execute("""
            SELECT repo_run_id, create_date
            FROM run_uid
            ORDER BY create_date DESC
        """)
        rows = cursor.fetchall()

        # rows: [(12, '2025-12-22 18:01:23'), (11, '2025-12-22 17:50:01'), ...]
        runs = [
            {"id": row[0], "created": row[1]}
            for row in rows
        ]
        return runs

    except Exception as e:
        log.error("Error reading run_infos from %s: %s", db_path, e)
        return []
    finally:
        if conn is not None:
            conn.close()

@app.route("/api/client/upload", methods=["POST"])
def upload_testmon_data():
    file = request.files.get("file")
    repo_id = request.form.get("repo_id")
    job_id = request.form.get("job_id")
    repo_name = request.form.get("repo_name")
    run_id= request.form.get("run_id")

    # Enrich per-request context for logging
    g.repo_id, g.job_id = repo_id or "-", job_id or "-"

    if not file:
        log.warning("upload_missing_file")
        return jsonify({"error": "No file provided"}), 400

    log.info(
        "upload_received filename=%s", getattr(file, "filename", None)
    )

    if not repo_id or not job_id:
        log.warning("upload_missing_params")
        return jsonify({"error": "repo_id and job_id are required"}), 400

    try:
        register_repo_job(repo_id, job_id, repo_name)

        db_path = get_job_db_path(repo_id, job_id)
     
        # Attempt to write uploaded file
        log.info("file_write_attempt dest=%s", db_path)
        file.save(db_path)
        add_run_id_to_testmon_data(db_path, run_id)
        size = db_path.stat().st_size
        log.info("file_write_success dest=%s size=%s (%s)", db_path, size, human_bytes(size))

        # Update metadata
        metadata = get_metadata()
        metadata["repos"][repo_id]["jobs"][job_id]["last_updated"] = now_iso()
        metadata["repos"][repo_id]["jobs"][job_id]["upload_count"] += 1
        save_metadata(metadata)
        log.info("upload_metadata_updated")

        return jsonify(
            {
                "success": True,
                "message": f"Testmon data uploaded for {repo_id}/{job_id}",
                "db_path": str(db_path.relative_to(BASE_DATA_DIR)),
            }
        ), 200

    except Exception:
        log_exception("upload_handler", repo_id=repo_id, job_id=job_id)
        return jsonify({"error": "Upload failed"}), 500

@app.route("/api/client/download", methods=["GET"])
def download_testmon_data():
    repo_id = request.args.get("repo_id")
    job_id = request.args.get("job_id")
    g.repo_id, g.job_id = repo_id or "-", job_id or "-"

    log.info("download_request")

    if not repo_id or not job_id:
        log.warning("download_missing_params")
        return jsonify({"error": "repo_id and job_id are required"}), 400

    db_path = get_job_db_path(repo_id, job_id)

    log.info("file_read_attempt path=%s", db_path)
    if not db_path.exists():
        log.warning("file_read_not_found path=%s", db_path)
        return jsonify({"error": "No data found for this repo/job"}), 404

    try:
        size = db_path.stat().st_size
        log.info("file_read_success path=%s size=%s (%s)", db_path, size, human_bytes(size))
        return send_file(
            db_path,
            as_attachment=True,
            download_name=".testmondata",
            mimetype="application/octet-stream",
        )
    except Exception:
        log_exception("download_send_file", path=str(db_path))
        return jsonify({"error": "Failed to send file"}), 500

@app.route("/api/client/exists", methods=["GET"])
def check_testmon_data_exists():
    repo_id = request.args.get("repo_id")
    job_id = request.args.get("job_id")
    g.repo_id, g.job_id = repo_id or "-", job_id or "-"

    log.info("exists_request")

    if not repo_id or not job_id:
        log.warning("exists_missing_params")
        return jsonify({"error": "repo_id and job_id are required"}), 400

    db_path = get_job_db_path(repo_id, job_id)
    exists = db_path.exists()
    log.info("exists_checked path=%s exists=%s", db_path, exists)

    return jsonify({"exists": exists, "repo_id": repo_id, "job_id": job_id})

# -----------------------------------------------------------------------------
# API ENDPOINTS - Visualization Data (with DB logging)
# -----------------------------------------------------------------------------
def _open_db_or_404(repo_id: str, job_id: str):
    db_path = get_job_db_path(repo_id, job_id)
    log.info("db_read_attempt path=%s", db_path)
    if not db_path.exists():
        log.warning("db_missing path=%s", db_path)
        return None, jsonify({"error": "No data found"}), 404
    return db_path, None, None

@app.route("/api/repos", methods=["GET"])
def list_repos():
    metadata = get_metadata()
    user_repositories_dict = get_user_repositories()
    user_repositories_set = set()
    for repo in user_repositories_dict:
         user_repositories_set.add(repo.get('full_name'))
    system_repositories = []
    for repo_id, repo_data in metadata.get("repos", {}).items():
        if not (repo_data.get('name') in user_repositories_set):
            continue
        jobs = []
        for job_id, job_data in repo_data.get("jobs", {}).items():
            db_path = get_job_db_path(repo_id, job_id)
            runs = get_run_infos(db_path)
            jobs.append(
                {
                    "id": job_id,
                    "name": job_data.get("name", job_id),
                    "created": job_data["created"],
                    "runs": runs,
                }
            )

        system_repositories.append(
            {
                "id": repo_id,
                "name": repo_data["name"],
                "created": repo_data["created"],
                "jobs": jobs,
            }
        )

    log.info("repos_list_success count=%s", len(system_repositories))
    return jsonify({
        "system_repos": system_repositories,
        "user_repos": [{
            "id": repo["id"],
            "name": repo["name"],
            "full_name": repo["full_name"],
            "owner": repo["owner"]["login"],
            "private": repo["private"],
            "url": repo["html_url"],
            "description": repo["description"],
            "permissions": repo.get("permissions", {}),
            "default_branch": repo["default_branch"]
        }
        for repo in user_repositories_dict]
    })

@login_required
def get_user_repositories():
    """Fetch only repositories the user owns or collaborates on"""
    token = session["github_token"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json"
    }

    all_repos = []

    page = 1
    while True:
        resp = requests.get(
            "https://api.github.com/user/repos",
            headers=headers,
            params={
                "affiliation": "owner, collaborator",
                "sort": "updated",
                "per_page": 100,
                "page": page
            }
        )
        repos = resp.json()
        if not repos:
            break
        all_repos.extend(repos)
        page += 1

    return all_repos

@app.route("/api/repos/<owner>/<repo>/actions/workflows")
@login_required
def get_workflow_files(owner, repo):
    token = session["github_token"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json"
    }

    # 1. Get the list of all workflows
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows"
    resp = requests.get(url, headers=headers)

    if resp.status_code == 404:
        return jsonify([])
    if resp.status_code != 200:
        return jsonify({"error": f"GitHub API returned {resp.status_code}"}), resp.status_code

    data = resp.json()
    all_workflows = data.get('workflows', [])

    # 2. Filter workflows: Must contain 'pytest'
    pytest_workflows = []

    for wf in all_workflows:
        # Fetch content and check
        print(f"Checking content of: {wf['path']}")
        if contains_pytest(owner, repo, wf['path'], token):
            pytest_workflows.append(wf)

    print(f"Found {len(pytest_workflows)} pytest workflows")
    return jsonify(pytest_workflows)

def contains_pytest(owner, repo, file_path, token):
    """
    Helper to fetch file content and check for 'pytest'.
    Uses the 'raw' media type to avoid Base64 decoding.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3.raw" # Important: Asks for raw text
    }

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            # Check if 'pytest' is in the file content
            return "pytest" in resp.text
        return False
    except Exception as e:
        print(f"Error fetching {file_path}: {e}")
        return False

@app.route("/api/repos/<owner>/<repo>/contents")
@login_required
def get_file_content(owner, repo):
    """
    Fetch the raw content of a specific file.
    """
    file_path = request.args.get("path")
    if not file_path:
        return jsonify({"error": "Path is required"}), 400

    token = session["github_token"]

    # URL to fetch file content
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}"

    # Use the 'raw' header to get the plain text (YAML) directly
    # avoiding the need to decode Base64 manually
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3.raw"
    }

    resp = requests.get(url, headers=headers)

    if resp.status_code != 200:
        return jsonify({"error": "Could not fetch file content"}), resp.status_code

    # Return the raw text content in a JSON wrapper
    return jsonify({"content": resp.text})

@app.route('/api/data/<path:repo_id>/<job_id>/<run_id>/summary', methods=['GET'])
def get_summary(repo_id: str, job_id: str, run_id: str):
    g.repo_id, g.job_id, g.run_id = repo_id, job_id, run_id
    db_path, resp, code = _open_db_or_404(repo_id, job_id)
    if resp:
        return resp, code

    try:
        conn = get_db_connection(db_path, readonly=True)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        env = cursor.execute(
            """
            SELECT environment_name, python_version, system_packages
            FROM environment
            LIMIT 1;
        """
        ).fetchone()

        test_count_row = cursor.execute("SELECT tests_all FROM run_infos Where run_uid = (Select id from run_uid Where repo_run_id=?)", (run_id,)).fetchone()
        test_count = test_count_row[0] if test_count_row else 0
        file_count = cursor.execute(
            "SELECT COUNT(DISTINCT filename) FROM file_fp_infos WHERE run_uid = (Select id from run_uid Where repo_run_id=?) ",
            (run_id,)
        ).fetchone()[0]
       
        test_savings = cursor.execute(
            "SELECT tests_saved FROM run_infos WHERE run_uid = (SELECT id FROM run_uid WHERE repo_run_id=?)",
            (run_id,)
        ).fetchone()

        time_savings = cursor.execute(
            "SELECT run_time_saved FROM run_infos WHERE run_uid = (SELECT id FROM run_uid WHERE repo_run_id=?)",
            (run_id,)
        ).fetchone()

        time_all = cursor.execute(
            "SELECT run_time_all FROM run_infos WHERE run_uid = (SELECT id FROM run_uid WHERE repo_run_id=?)",
            (run_id,)
        ).fetchone()

        row = cursor.execute(
            "SELECT create_date FROM run_uid WHERE repo_run_id = ?",
            (run_id,)
        ).fetchone()

        create_date = row[0] if row else None

        savings = {}
        if test_savings and test_savings[0] is not None:
            savings["tests_saved"] = test_savings[0]
        if time_savings and time_savings[0] is not None:
            savings["time_saved"] = time_savings[0]     
        if time_all and time_all[0] is not None:
            savings["time_all"] = time_all[0]     
    

        conn.close()
        log.info(
            "summary_success tests=%s files=%s",
            test_count,
            file_count,
        )

        return jsonify(
            {
                "repo_id": repo_id,
                "job_id": job_id,
                "run_id": run_id,
                "create_date": create_date,
                "test_count": test_count,
                "file_count": file_count,
                "environment": {
                    "name": env["environment_name"] if env else "default",
                    "python_version": env["python_version"] if env else "unknown",
                    "packages": (env["system_packages"][:100] + "...")
                    if env and env["system_packages"]
                    else "",
                },
                "savings": savings,
            }
        )

    except Exception:
        log_exception("summary_query", repo_id=repo_id, job_id=job_id)
        return jsonify({"error": "Failed to read summary"}), 500


@app.route("/api/data/<path:repo_id>/<job_id>/<run_id>/test_files", methods=["GET"])
def list_test_files(repo_id: str, job_id: str, run_id: str):
    g.repo_id, g.job_id, g.run_id = repo_id, job_id, run_id

    db_path, resp, code = _open_db_or_404(repo_id, job_id)
    if resp:
        return resp, code

    try:
        conn = get_db_connection(db_path, readonly=True)
        conn.row_factory = sqlite3.Row

        test_files = conn.execute(
            """
            SELECT
                CASE 
                    WHEN instr(te.test_name, '::') > 0 
                        THEN substr(te.test_name, 1, instr(te.test_name, '::') - 1)
                    ELSE te.test_name
                END AS file_name,
                COUNT(*) AS test_count,
                SUM(te.duration) AS total_duration,
                SUM(CASE WHEN te.failed = 1 THEN 1 ELSE 0 END) AS failed_count,
                SUM(CASE WHEN te.forced = 1 THEN 1 ELSE 0 END) AS forced_count,
                COUNT(DISTINCT tef.fingerprint_id) AS dependency_count,
                GROUP_CONCAT(
                    DISTINCT
                    CASE 
                        WHEN instr(te.test_name, '::') > 0 
                            THEN substr(te.test_name, instr(te.test_name, '::') + 2)
                        ELSE NULL
                    END
                ) AS test_methods
            FROM test_infos te
            LEFT JOIN test_execution_file_fp_infos tef
                ON te.id = tef.test_execution_id     
            WHERE te.run_uid = (SELECT id FROM run_uid WHERE repo_run_id=?)                      
            GROUP BY file_name
            ORDER BY file_name;
            """,
            (run_id,),
        ).fetchall()


        conn.close()
       

        return jsonify({"test_files": [dict(test) for test in test_files]})

    except Exception:
        log_exception("test_list_query", repo_id=repo_id, job_id=job_id)
        return jsonify({"error": "Failed to read test list"}), 500

@app.route("/api/data/<path:repo_id>/<job_id>/<run_id>/tests", methods=["GET"])
def get_tests(repo_id: str, job_id: str, run_id: str):
    g.repo_id, g.job_id, g.run_id = repo_id, job_id, run_id

    db_path, resp, code = _open_db_or_404(repo_id, job_id)
    if resp:
        return resp, code

    try:
        conn = get_db_connection(db_path, readonly=True)
        conn.row_factory = sqlite3.Row

        tests = conn.execute(
            """
            SELECT 
                te.id,
                te.test_name,
                te.duration,
                te.failed,
                te.forced,
                COUNT(DISTINCT tef.fingerprint_id) AS dependency_count
            FROM test_infos te
            LEFT JOIN test_execution_file_fp_infos tef 
                ON te.test_execution_id = tef.test_execution_id
                AND tef.run_uid = (
                    SELECT id FROM run_uid WHERE repo_run_id=?
                )
            WHERE te.run_uid = (
                    SELECT id FROM run_uid WHERE repo_run_id=?
                )
            GROUP BY te.id, te.test_name, te.duration, te.failed, te.forced
            ORDER BY te.test_name
            """,
            (run_id, run_id)
).fetchall()

        conn.close()
        #log.info("tests_list_success count=%s", len(tests))

        return jsonify({"run_id": run_id, "tests": [dict(test) for test in tests]})

    except Exception:
        log_exception("tests_query", repo_id=repo_id, job_id=job_id)
        return jsonify({"error": "Failed to read tests"}), 500

@app.route("/api/data/<path:repo_id>/<job_id>/<run_id>/test/<int:test_id>", methods=["GET"])
def get_test_details(repo_id: str, job_id: str, run_id:str, test_id: int):
    g.repo_id, g.job_id , g.run_id = repo_id, job_id, run_id

    db_path, resp, code = _open_db_or_404(repo_id, job_id)
    if resp:
        return resp, code

    try:
        conn = get_db_connection(db_path, readonly=True)
        conn.row_factory = sqlite3.Row

        test = conn.execute(
            "SELECT * FROM test_infos WHERE id = ?", (test_id,)
        ).fetchone()
        if not test:
            conn.close()
            log.warning("test_not_found test_id=%s", test_id)
            return jsonify({"error": "Test not found"}), 404

        deps = conn.execute(
            """
            SELECT 
                fp.filename,
                fp.fsha,
                fp.method_checksums,
                fp.mtime
            FROM test_infos ti
            JOIN run_uid r
                ON ti.run_uid = r.id
            JOIN test_execution_file_fp_infos tef
                ON tef.test_execution_id = ti.test_execution_id
            AND tef.run_uid = r.id
            JOIN file_fp_infos fp
                ON fp.fingerprint_id = tef.fingerprint_id
            AND fp.run_uid = r.id
            WHERE ti.id = ?
            AND r.repo_run_id = ?
            """,
            (test_id, run_id)
        ).fetchall()
        coverage_rows = conn.execute(
            """
                SELECT filename, lines
                FROM test_execution_coverage
                WHERE run_uid=(
                    Select id
                    From run_uid
                    Where repo_run_id=?
                )
                AND test_execution_id = (
                    Select test_execution_id 
                    From test_infos
                    WHERE id=? 
                )
                
            """,
            (run_id,test_id)
        ).fetchall()

        # lines is stored as JSON string, so decode it
        coverage = {
            row["filename"]: json.loads(row["lines"])
            for row in coverage_rows
        }


        conn.close()

        dependencies = []
        for dep in deps:

            checksums_arr = array.array("i")
            checksums_arr.frombytes(dep["method_checksums"])
            dependencies.append(
                {
                    "filename": dep["filename"],
                    "fsha": dep["fsha"],
                    "mtime": dep["mtime"],
                    "checksums": checksums_arr.tolist(),
                }
            )


        return jsonify({
            "test": dict(test),
            "dependencies": dependencies,
            "coverage": coverage,
        })


    except Exception:
        log_exception("test_details_query", repo_id=repo_id, job_id=job_id, test_id=test_id)
        return jsonify({"error": "Failed to read test details"}), 500

@app.route("/api/data/<path:repo_id>/<job_id>/<run_id>/files", methods=["GET"])
def get_files(repo_id: str, job_id: str ,run_id:str):
    g.repo_id, g.job_id , g.run_id = repo_id, job_id ,run_id

    db_path, resp, code = _open_db_or_404(repo_id, job_id)
    if resp:
        return resp, code

    try:
        conn = get_db_connection(db_path, readonly=True)
        conn.row_factory = sqlite3.Row
        files = conn.execute(
            """
            SELECT 
                fpi.filename,
                COUNT(DISTINCT tefi.test_execution_id) AS test_count,
                COUNT(DISTINCT fpi.fingerprint_id)     AS fingerprint_count
            FROM file_fp_infos fpi
            LEFT JOIN test_execution_file_fp_infos tefi
                ON  fpi.fingerprint_id = tefi.fingerprint_id
                AND fpi.run_uid  = tefi.run_uid   
            WHERE 
                fpi.run_uid = (SELECT id FROM run_uid WHERE repo_run_id=?)
            GROUP BY 
                fpi.filename
            ORDER BY 
                fpi.filename
            """,
            (run_id,)
        ).fetchall()

        conn.close()
        log.info("files_list_success count=%s", len(files))

        return jsonify({"run_id": run_id, "files": [dict(file) for file in files]})

    except Exception:
        log_exception("files_query", repo_id=repo_id, job_id=job_id)
        return jsonify({"error": "Failed to read files"}), 500


@app.route(
    "/api/data/<path:repo_id>/<job_id>/<run_id>/fileDetails/<path:file_name>",
    methods=["GET"]
)
def get_file_details(repo_id: str, job_id: str ,run_id:str , file_name:str):
    g.repo_id, g.job_id , g.run_id = repo_id, job_id ,run_id

    db_path, resp, code = _open_db_or_404(repo_id, job_id)
    if resp:
        return resp, code

    try:
        conn = get_db_connection(db_path, readonly=True)
        conn.row_factory = sqlite3.Row
        files = conn.execute(
            """
            SELECT  tei.test_name , tei.duration , tei.failed , tei.forced 
            FROM file_fp_infos fpi
            JOIN test_execution_file_fp_infos tefi
            ON tefi.fingerprint_id = fpi.fingerprint_id
            AND tefi.run_uid        = fpi.run_uid
            JOIN test_infos tei
            ON tei.test_execution_id      = tefi.test_execution_id
            AND tei.run_uid = fpi.run_uid
            WHERE fpi.run_uid  = (SELECT id FROM run_uid WHERE repo_run_id = ?)
            AND fpi.filename = ?
            ORDER BY tei.test_name
            """,
            (run_id, file_name)

        ).fetchall()
        conn.close()
        log.info("files_list_success count=%s", len(files))

        return jsonify({"run_id": run_id, "files": [dict(file) for file in files]})

    except Exception:
        log_exception("files_query", repo_id=repo_id, job_id=job_id)
        return jsonify({"error": "Failed to read files"}), 500


@app.route("/api/data/<path:repo_id>/<job_id>/<int:run_id>/fileDependencies", methods=["GET"])
def get_file_dependencies(repo_id: str, job_id: str, run_id: int):

    db_path, resp, code = _open_db_or_404(repo_id, job_id)
    if resp:
        return resp, code
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 1) Get all files that appear in this run (by external repo_run_id)
    all_files_sql = """
        SELECT DISTINCT
          fpi.filename
        FROM file_fp_infos fpi
        JOIN run_uid ru
          ON fpi.run_uid = ru.id
        WHERE ru.repo_run_id = ?
    """
    cur.execute(all_files_sql, (run_id,))
    all_files_rows = cur.fetchall()

    # Initialize map: filename -> set(dependencies)
    deps_map = {row["filename"]: set() for row in all_files_rows}

    # Early-out if no files in this run
    if not deps_map:
        conn.close()
        return jsonify({"run_id": run_id, "files": []})

    # 2) Get pairwise "file -> dependency" relations via shared test executions
    file_deps_sql = """
        WITH run AS (
          SELECT id AS run_uid
          FROM run_uid
          WHERE repo_run_id = ?
        ),
        file_tests AS (
          SELECT DISTINCT
            fpi.filename      AS file,
            tefi.test_execution_id
          FROM file_fp_infos fpi
          JOIN run r
            ON fpi.run_uid = r.run_uid
          JOIN test_execution_file_fp_infos tefi
            ON tefi.run_uid        = r.run_uid
           AND tefi.fingerprint_id = fpi.fingerprint_id
        ),
        file_cofiles AS (
          SELECT DISTINCT
            ft.file           AS filename,
            fpi2.filename     AS dependency
          FROM file_tests ft
          JOIN test_execution_file_fp_infos tefi2
            ON tefi2.test_execution_id = ft.test_execution_id
          JOIN file_fp_infos fpi2
            ON fpi2.run_uid        = tefi2.run_uid
           AND fpi2.fingerprint_id = tefi2.fingerprint_id
          JOIN run r
            ON fpi2.run_uid = r.run_uid
          WHERE fpi2.filename <> ft.file
        )
        SELECT
          filename,
          dependency
        FROM file_cofiles
        ORDER BY filename, dependency;
    """
    cur.execute(file_deps_sql, (run_id,))
    for row in cur.fetchall():
        filename = row["filename"]
        dependency = row["dependency"]
        # Only add dependencies for files that are in this run
        if filename in deps_map:
            deps_map[filename].add(dependency)

    conn.close()

    # 3) Build final JSON in the shape the React graph expects
    files_list = [
        {
            "filename": filename,
            "dependencies": sorted(list(deps))
        }
        for filename, deps in sorted(deps_map.items())
    ]

    return jsonify({
        "run_id": run_id,
        "files": files_list
    })


@app.route("/api/client/testPreferences", methods=["POST"])
def upload_test_preferences():
    """Store user's test preferences (which tests to always run)"""
    
    # Get data from request body (JSON)
    data = request.get_json()
    repo_id = data.get("repo_id")
    job_id = data.get("job_id")
    
    selected_test_files = data.get("alwaysRunTests", [])  # Array of test file names
    
    log.info("Selected test files !!!!!!!!"  ,selected_test_files)
    # Enrich per-request context for logging
    g.repo_id, g.job_id = repo_id or "-", job_id or "-"

    if not repo_id or not job_id:
        log.warning("preferences_missing_params")
        return jsonify({"error": "repo_id and job_id are required"}), 400

    if not isinstance(selected_test_files, list):
        log.warning("preferences_invalid_format")
        return jsonify({"error": "selectedTests must be an array"}), 400

    try:
        # Create preferences file path
        job_path = get_job_db_path(repo_id, job_id).parent
        preferences_path = job_path / "test_preferences.json"
        
        log.info(
            "preferences_write_attempt path=%s test_count=%s", 
            preferences_path, 
            len(selected_test_files)
        )
        
        # Store preferences as JSON
        preferences_data = {
            "repo_id": repo_id,
            "job_id": job_id,
            "always_run_tests": selected_test_files,
            "updated_at": now_iso(),
        }
        
        with open(preferences_path, "w") as f:
            json.dump(preferences_data, f, indent=2)
        
        size = preferences_path.stat().st_size
        log.info(
            "preferences_write_success path=%s size=%s (%s) test_count=%s",
            preferences_path,
            size,
            human_bytes(size),
            len(selected_test_files)
        )

        return jsonify({
            "success": True,
            "message": f"Test preferences saved for {repo_id}/{job_id}",
            "test_count": len(selected_test_files),
        }), 200

    except Exception:
        log_exception("preferences_handler", repo_id=repo_id, job_id=job_id)
        return jsonify({"error": "Failed to save preferences"}), 500


@app.route("/api/client/testPreferences", methods=["GET"])
def get_test_preferences():
    """Retrieve user's test preferences"""
    
    repo_id = request.args.get("repo_id")
    job_id = request.args.get("job_id")
    g.repo_id, g.job_id = repo_id or "-", job_id or "-"

    if not repo_id or not job_id:
        log.warning("preferences_get_missing_params")
        return jsonify({"error": "repo_id and job_id are required"}), 400

    try:
        job_path = get_job_db_path(repo_id, job_id).parent
        preferences_path = job_path / "test_preferences.json"
        
        log.info("preferences_read_attempt path=%s", preferences_path)
        
        if not preferences_path.exists():
            log.info("preferences_not_found path=%s", preferences_path)
            return jsonify({
                "repo_id": repo_id,
                "job_id": job_id,
                "always_run_tests": [],
                "updated_at": None,
            }), 200
        
        with open(preferences_path, "r") as f:
            preferences_data = json.load(f)
        
        size = preferences_path.stat().st_size
        log.info(
            "preferences_read_success path=%s size=%s (%s)",
            preferences_path,
            size,
            human_bytes(size)
        )
        
        return jsonify(preferences_data), 200

    except Exception:
        log_exception("preferences_get_handler", repo_id=repo_id, job_id=job_id)
        return jsonify({"error": "Failed to read preferences"}), 500
    

# -----------------------------------------------------------------------------
# Pytest JSON Report Storage
# -----------------------------------------------------------------------------


def get_pytest_report_path(repo_id: str, job_id: str, run_id: str) -> Path:
    """Get path for storing pytest JSON report inside job folder"""
    repo_path = get_repo_path(repo_id)
    safe_job_id = "".join(c for c in job_id if c.isalnum() or c in ("-", "_"))
    safe_run_id = "".join(c for c in run_id if c.isalnum() or c in ("-", "_"))
    
    job_path = repo_path / safe_job_id
    if not job_path.exists():
        log.info(
            "job_dir_create_attempt repo_id=%s job_id=%s path=%s",
            repo_id, job_id, job_path,
        )
        job_path.mkdir(parents=True, exist_ok=True)
        log.info("job_dir_create_success path=%s", job_path)

    # Store as pytest_report_{run_id}.json in the job folder
    return job_path / f"pytest_report_{safe_run_id}.json"



@app.route("/api/client/pytest-report", methods=["POST"])
def upload_pytest_report():
    """Store pytest JSON report from CI/CD"""
    data = request.get_json()

    if not data:
        log.warning("pytest_report_missing_data")
        return jsonify({"error": "No JSON data provided"}), 400

    repo_id = request.args.get("repo_id") or data.get("repo_id")
    job_id = request.args.get("job_id") or data.get("job_id")
    run_id = request.args.get("run_id") or data.get("run_id")
    
    g.repo_id, g.job_id = repo_id or "-", job_id or "-"

    if not repo_id or not job_id or not run_id:
        log.warning("pytest_report_missing_params")
        return jsonify({"error": "repo_id, job_id, and run_id are required"}), 400

    try:
        # Register repo/job in metadata
        register_repo_job(repo_id, job_id)

        # Save the pytest report
        report_path = get_pytest_report_path(repo_id, job_id, run_id)
        
        log.info("pytest_report_write_attempt dest=%s", report_path)
        with open(report_path, "w") as f:
            json.dump(data, f, indent=2)
        
        size = report_path.stat().st_size
        log.info("pytest_report_write_success dest=%s size=%s (%s)",
                 report_path, size, human_bytes(size))
        
        # Update metadata with run info
        metadata = get_metadata()
        if repo_id in metadata["repos"] and job_id in metadata["repos"][repo_id]["jobs"]:
            job_meta = metadata["repos"][repo_id]["jobs"][job_id]
            if "runs" not in job_meta:
                job_meta["runs"] = {}
            job_meta["runs"][run_id] = {
                "created": now_iso(),
                "summary": data.get("summary", {}),
                "duration": data.get("duration"),
                "exitcode": data.get("exitcode"),
            }
            job_meta["last_updated"] = now_iso()
            save_metadata(metadata)

        return jsonify({
            "success": True,
            "repo_id": repo_id,
            "job_id": job_id,
            "run_id": run_id,
            "tests_stored": len(data.get("tests", [])),
        })

    except Exception:
        log_exception("pytest_report_upload", repo_id=repo_id, job_id=job_id, run_id=run_id)
        return jsonify({"error": "Failed to store pytest report"}), 500


@app.route("/api/client/pytest-report", methods=["GET"])
def get_pytest_report():
    """Retrieve pytest JSON report"""
    repo_id = request.args.get("repo_id")
    job_id = request.args.get("job_id")
    run_id = request.args.get("run_id")
    
    g.repo_id, g.job_id = repo_id or "-", job_id or "-"

    if not repo_id or not job_id or not run_id:
        log.warning("pytest_report_get_missing_params")
        return jsonify({"error": "repo_id, job_id, and run_id are required"}), 400

    report_path = get_pytest_report_path(repo_id, job_id, run_id)

    if not report_path.exists():
        log.warning("pytest_report_not_found path=%s", report_path)
        return jsonify({"error": "Report not found"}), 404

    try:
        with open(report_path, "r") as f:
            data = json.load(f)
        log.info("pytest_report_read_success path=%s", report_path)
        return jsonify(data)
    except Exception:
        log_exception("pytest_report_read", path=str(report_path))
        return jsonify({"error": "Failed to read pytest report"}), 500


@app.route("/api/data/<path:repo_id>/<job_id>/<run_id>/pytest-summary", methods=["GET"])
def get_pytest_summary(repo_id: str, job_id: str, run_id: str):
    """Get summary of pytest run from stored JSON report"""
    g.repo_id, g.job_id = repo_id, job_id

    report_path = get_pytest_report_path(repo_id, job_id, run_id)

    if not report_path.exists():
        log.warning("pytest_summary_not_found path=%s", report_path)
        return jsonify({"error": "Report not found"}), 404

    try:
        with open(report_path, "r") as f:
            data = json.load(f)

        summary = data.get("summary", {})
        tests = data.get("tests", [])

        # Calculate additional metrics
        total_duration = sum(
            t.get("setup", {}).get("duration", 0) +
            t.get("call", {}).get("duration", 0) +
            t.get("teardown", {}).get("duration", 0)
            for t in tests
        )

        # Group tests by file
        test_files = {}
        for test in tests:
            nodeid = test.get("nodeid", "")
            file_name = nodeid.split("::")[0] if "::" in nodeid else nodeid
            if file_name not in test_files:
                test_files[file_name] = {"passed": 0, "failed": 0, "total": 0}
            test_files[file_name]["total"] += 1
            if test.get("outcome") == "passed":
                test_files[file_name]["passed"] += 1
            elif test.get("outcome") == "failed":
                test_files[file_name]["failed"] += 1

        # Get failed test details
        failed_tests = [
            {
                "nodeid": t.get("nodeid"),
                "lineno": t.get("lineno"),
                "message": t.get("call", {}).get("crash", {}).get("message"),
                "longrepr": t.get("call", {}).get("longrepr"),
            }
            for t in tests if t.get("outcome") == "failed"
        ]

        result = {
            "repo_id": repo_id,
            "job_id": job_id,
            "run_id": run_id,
            "created": data.get("created"),
            "duration": data.get("duration"),
            "exitcode": data.get("exitcode"),
            "root": data.get("root"),
            "summary": {
                "passed": summary.get("passed", 0),
                "failed": summary.get("failed", 0),
                "total": summary.get("total", 0),
                "collected": summary.get("collected", 0),
            },
            "total_test_duration": total_duration,
            "test_files": test_files,
            "file_count": len(test_files),
            "failed_tests": failed_tests,
        }

        log.info("pytest_summary_success repo=%s job=%s run=%s", repo_id, job_id, run_id)
        return jsonify(result)

    except Exception:
        log_exception("pytest_summary_read", repo_id=repo_id, job_id=job_id, run_id=run_id)
        return jsonify({"error": "Failed to read pytest summary"}), 500


@app.route("/api/data/<path:repo_id>/<job_id>/<run_id>/pytest-tests", methods=["GET"])
def get_pytest_tests(repo_id: str, job_id: str, run_id: str):
    """Get all tests from pytest JSON report"""
    g.repo_id, g.job_id = repo_id, job_id

    report_path = get_pytest_report_path(repo_id, job_id, run_id)

    if not report_path.exists():
        log.warning("pytest_tests_not_found path=%s", report_path)
        return jsonify({"error": "Report not found"}), 404

    try:
        with open(report_path, "r") as f:
            data = json.load(f)

        tests = []
        for t in data.get("tests", []):
            test_duration = (
                t.get("setup", {}).get("duration", 0) +
                t.get("call", {}).get("duration", 0) +
                t.get("teardown", {}).get("duration", 0)
            )
            tests.append({
                "nodeid": t.get("nodeid"),
                "lineno": t.get("lineno"),
                "outcome": t.get("outcome"),
                "duration": test_duration,
                "keywords": t.get("keywords", []),
                "failed": t.get("outcome") == "failed",
                "error_message": t.get("call", {}).get("crash", {}).get("message") if t.get("outcome") == "failed" else None,
            })

        log.info("pytest_tests_success count=%s", len(tests))
        return jsonify({
            "repo_id": repo_id,
            "job_id": job_id,
            "run_id": run_id,
            "tests": tests,
        })

    except Exception:
        log_exception("pytest_tests_read", repo_id=repo_id, job_id=job_id, run_id=run_id)
        return jsonify({"error": "Failed to read pytest tests"}), 500


@app.route("/api/data/<path:repo_id>/<job_id>/runs", methods=["GET"])
def list_runs(repo_id: str, job_id: str):
    """List all runs for a specific repo/job"""
    g.repo_id, g.job_id = repo_id, job_id

    try:
        metadata = get_metadata()

        if repo_id not in metadata.get("repos", {}):
            return jsonify({"error": "Repository not found"}), 404

        if job_id not in metadata["repos"][repo_id].get("jobs", {}):
            return jsonify({"error": "Job not found"}), 404

        job_meta = metadata["repos"][repo_id]["jobs"][job_id]
        runs = []

        for run_id, run_data in job_meta.get("runs", {}).items():
            runs.append({
                "run_id": run_id,
                "created": run_data.get("created"),
                "summary": run_data.get("summary", {}),
                "duration": run_data.get("duration"),
                "exitcode": run_data.get("exitcode"),
            })

        # Sort by created date, newest first
        runs.sort(key=lambda x: x.get("created", ""), reverse=True)

        log.info("list_runs_success repo=%s job=%s count=%s", repo_id, job_id, len(runs))
        return jsonify({
            "repo_id": repo_id,
            "job_id": job_id,
            "runs": runs,
        })

    except Exception:
        log_exception("list_runs", repo_id=repo_id, job_id=job_id)
        return jsonify({"error": "Failed to list runs"}), 500



# -----------------------------------------------------------------------------
# WEB + Health - UPDATED FOR REACT
# -----------------------------------------------------------------------------

# Serve React App for root route
@app.route("/")
def serve_react_root():
    react_index = Path(app.root_path) / 'client' / 'dist' / 'index.html'
    log.info("serve_react_root path=%s exists=%s", react_index, react_index.exists())

    if react_index.exists():
        return send_file(react_index)
    else:
        log.error("react_build_missing expected=%s", react_index)
        return jsonify({"error": "React app not built. Run 'npm run build' in client directory"}), 500

# Serve React App's static assets (CSS, JS, images, etc.)
@app.route('/assets/<path:path>')
def serve_react_assets(path):
    assets_dir = Path(app.root_path) / 'client' / 'dist' / 'assets'
    log.info("serve_assets path=%s dir=%s", path, assets_dir)
    return send_from_directory(assets_dir, path)

# Catch-all route for React Router (client-side routing)
@app.route("/<path:path>")
def serve_react_app(path):
    # Don't catch API routes
    if path.startswith('api/'):
        log.warning("invalid_api_route path=%s", path)
        return jsonify({"error": "API endpoint not found"}), 404

    # Don't catch the .ezmon-fp routes
    if path.startswith('.ezmon-fp/'):
        return serve_ezmon_fp(path.replace('.ezmon-fp/', ''))

    # Check if the path is a static file in dist
    file_path = Path(app.root_path) / 'client' / 'dist' / path
    if file_path.exists() and file_path.is_file():
        return send_file(file_path)

    # Otherwise, serve index.html for React Router
    react_index = Path(app.root_path) / 'client' / 'dist' / 'index.html'
    log.info("serve_react_app path=%s", path)

    if react_index.exists():
        return send_file(react_index)
    else:
        log.error("react_build_missing expected=%s", react_index)
        return jsonify({"error": "React app not built"}), 500

@app.route("/health")
def health():
    repo_count = len(get_metadata().get("repos", {}))
    log.info("health_check repo_count=%s data_dir=%s", repo_count, BASE_DATA_DIR)
    return jsonify(
        {"status": "healthy!!!", "data_dir": str(BASE_DATA_DIR), "repo_count": repo_count}
    )

# @app.route("/fingerprints")
# def fingerprints_page():
#     log.info("fingerprints_render")
#     return render_template("fingerprints.html")

@app.route("/.ezmon-fp/<path:subpath>")
def serve_ezmon_fp(subpath: str):
    # Static file bridge for the ezmon snapshots
    fp_path = EZMON_FP_DIR / subpath
    if not fp_path.exists() or fp_path.is_dir():
        log.warning("ezmon_fp_missing path=%s", fp_path)
        return jsonify({"error": "Not found"}), 404
    try:
        size = fp_path.stat().st_size
    except Exception:
        size = -1
    log.info("ezmon_fp_serve path=%s size=%s", fp_path, size)
    return send_from_directory(EZMON_FP_DIR, subpath, as_attachment=False)


if __name__ == "__main__":
    log.info("server_start data_dir=%s", BASE_DATA_DIR.absolute())
    print("Starting Testmon Multi-Project Server")
    print(f"Data directory: {BASE_DATA_DIR.absolute()}")
    print("Server running on http://localhost:8000")
    app.run(debug=True, host="0.0.0.0", port=8000)