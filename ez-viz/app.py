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
import base64
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
import zipfile
import io
import re
from github import Github, GithubException

# Ensure repo root is on sys.path so ezmon modules are importable.
_ROOT_DIR = Path(__file__).resolve().parent.parent
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

# OpenAI is optional - only needed for AI-assisted workflow modification
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    OpenAI = None

EZMON_FP_DIR = Path(os.getenv("EZMON_FP_DIR", "./.ezmon-fp")).resolve()
CURRENT_MODEL = "gpt-4o-mini"

# CI/CD Authentication Token - set via environment variable or use default for testing
# In production, set EZMON_CI_TOKEN to a secure random string
CI_AUTH_TOKEN = os.environ.get("EZMON_CI_TOKEN", "ezmon-ci-test-token-2024")
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
if allowed_origin:
    cors_origins = allowed_origin
else:
    cors_origins = "*"
CORS(
    app,
    supports_credentials=True,
    origins=cors_origins,
    allow_headers=["Content-Type"],
    methods=["GET", "POST", "OPTIONS"],
)

BASE_DATA_DIR = Path(os.getenv("TESTMON_DATA_DIR", "../testmon_data"))
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
# Helper functions
# -----------------------------------------------------------------------------

def _decode_bitmap(blob) -> set:
    try:
        import zstandard as zstd
        raw = zstd.ZstdDecompressor().decompress(blob)
    except ImportError:
        import gzip
        raw = gzip.decompress(blob)

    try:
        from pyroaring import BitMap
        return set(BitMap.deserialize(raw))
    except ImportError:
        import pickle
        return pickle.loads(raw)

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
    if not OPENAI_AVAILABLE:
        return jsonify({"error": "OpenAI library is not installed on the server. Install with: pip install openai"}), 503

    data = request.get_json()
    content = data.get("content")
    if not content:
        return jsonify({"error": "No content provided"}), 400
    api_key = os.getenv("AI_GITHUB_TOKEN")
    if not api_key:
        error_message = "Server configuration error: AI_GITHUB_TOKEN is missing."
        print(f"Error: {error_message}")
        return jsonify({"error": error_message}), 500

    client = OpenAI(
        base_url="https://models.inference.ai.azure.com",
        api_key=api_key,
    )

    print(f"--- Using {CURRENT_MODEL}")
    user_prompt = (
        "You are an expert GitHub Actions engineer. Update the following workflow file to integrate 'ezmon', "
        "an improved version of the testmon plugin for intelligent test selection.\n\n"

        "CRITICAL REQUIREMENTS:\n"
        "1. Add ezmon environment variables at the workflow or job level:\n"
        "   TESTMON_NET_ENABLED: \"true\"\n"
        "   TESTMON_SERVER: \"https://ezmon.aloiz.ch\"\n"
        "   TESTMON_AUTH_TOKEN: ${{ secrets.EZMON_AUTH_TOKEN }}\n"
        "   REPO_ID: ${{ github.repository }}\n"
        "   RUN_ID: ${{ github.run_id }}\n\n"

        "2. MANDATORY: Add a step to install the ezmon fork AFTER all other Python dependencies are installed.\n"
        "   This step must include:\n"
        "   pip install \"git+https://github.com/andreas16700/pytest-testmon@main\"\n"
        "   pip install networkx pyvis\n"
        "   Name this step 'Install ezmon plugin' or similar.\n"
        "   IMPORTANT: Keep all existing dependency installation commands (requirements.txt, setup.py, etc.)\n\n"

        "3. In the pytest execution step:\n"
        "   a. Add JOB_ID environment variable: JOB_ID=\"python-${{ matrix.python-version }}-${{ matrix.os }}\"\n"
        "      (or create a unique identifier combining OS and Python version for non-matrix builds)\n"
        "   b. Modify the pytest command to use: pytest --ezmon -v\n"
        "      If there are existing pytest flags, keep them and add --ezmon\n"
        "   If there's an existing pytest step, update it. If not, add a new step named 'Run tests with ezmon'.\n\n"

        "GUIDELINES:\n"
        "- Preserve all existing steps and configuration that don't conflict with ezmon\n"
        "- If the workflow has multiple jobs, apply changes to the primary test job\n"
        "- Keep existing Python version, OS, and other configurations unchanged\n"
        "- Maintain the workflow's existing structure and formatting style\n"
        "- Do not remove any existing environment variables or steps\n"
        "- For matrix builds with multiple OS/Python combinations, ensure each job gets a unique JOB_ID\n\n"

        "OUTPUT FORMAT:\n"
        "Return ONLY the complete updated YAML content. Do not include markdown code blocks (```yaml), "
        "explanations, or comments about what was changed.\n\n"

        "EXISTING WORKFLOW FILE:\n"
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
        "scope": "repo workflow read:user user:email",
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

        # Get run data with stats from run_infos table
        cursor.execute("""
            SELECT
                r.id,
                r.created_at,
                r.tests_all,
                r.tests_selected,
                r.tests_deselected,
                r.time_all,
                r.time_saved,
                r.commit_id
            FROM runs r
            ORDER BY r.created_at DESC
        """)
        rows = cursor.fetchall()

        runs = [
            {
                "id": row[0],
                "created_at": row[1],
                "tests_all": row[2],
                "tests_selected": row[3],
                "tests_deselected": row[4],
                "time_all": row[5],
                "time_saved": row[6],
                "commit_id": row[7],
            }
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

@app.route("/api/client/upload_graph", methods=["POST"])
def upload_dependency_graph():
    file = request.files.get("file")
    repo_id = request.form.get("repo_id")
    job_id = request.form.get("job_id")
    run_id = request.form.get("run_id")

    g.repo_id, g.job_id = repo_id or "-", job_id or "-"

    if not file:
        log.warning("upload_graph_missing_file")
        return jsonify({"error": "No file provided"}), 400

    log.info(
        "upload_graph_received filename=%s", getattr(file, "filename", None)
    )

    if not repo_id or not job_id:
        log.warning("upload_graph_missing_params")
        return jsonify({"error": "repo_id and job_id are required"}), 400

    try:
        # 1. Determine Path
        # We get the standard DB path, then swap the filename
        db_path = get_job_db_path(repo_id, job_id)
        graph_path = db_path.parent / f"dependency_graph_{run_id}.html"

        # 2. Write File
        log.info("graph_write_attempt dest=%s", graph_path)
        file.save(graph_path)

        size = graph_path.stat().st_size
        log.info("graph_write_success dest=%s size=%s (%s)", graph_path, size, human_bytes(size))

        # 3. Update Metadata
        metadata = get_metadata()
        job_meta = metadata["repos"][repo_id]["jobs"][job_id]

        job_meta["last_updated"] = now_iso()
        # Add a flag or timestamp specifically for the graph so the UI knows to show the button
        job_meta["last_graph_upload"] = now_iso()

        save_metadata(metadata)
        log.info("upload_graph_metadata_updated")

        return jsonify(
            {
                "success": True,
                "message": f"Dependency graph uploaded for {repo_id}/{job_id}",
                "graph_path": str(graph_path.relative_to(BASE_DATA_DIR)),
            }
        ), 200

    except Exception:
        log_exception("upload_graph_handler", repo_id=repo_id, job_id=job_id)
        return jsonify({"error": "Graph upload failed"}), 500

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

@app.route("/api/commit_workflow", methods=["POST"])
@login_required
def commit_workflow():
    print("--- DEBUG: Entering commit_workflow endpoint ---")

    # 1. Check Token
    token = session.get("github_token")
    if not token:
        print("--- DEBUG: Error - No token found in session")
        log.warning("commit_workflow_unauthorized: No token in session")
        return jsonify({"error": "No access token found"}), 401
    print(f"--- DEBUG: Token found (length: {len(token)})")

    # 2. Parse Request Data
    data = request.json
    print(f"--- DEBUG: Raw payload: {data}")

    owner = data.get("owner")
    repo_name = data.get("repo")
    file_path = data.get("path")
    new_content = data.get("content")
    commit_message = data.get("message", "Update workflow via Ezmon")

    # Default to main, but allow frontend to override if needed
    # IMPORTANT: We will print the branch being used
    branch = data.get("branch", "main")

    print(f"--- DEBUG: Parsed vars -> Owner: {owner}, Repo: {repo_name}, Path: {file_path}, Branch: {branch}")

    # 3. Validate Inputs
    if not all([owner, repo_name, file_path, new_content]):
        print("--- DEBUG: Error - Missing required fields")
        log.warning("commit_workflow_missing_fields data=%s", data.keys())
        return jsonify({"error": "Missing required fields (owner, repo, path, content)"}), 400

    try:
        # 4. Initialize GitHub Client
        print("--- DEBUG: Initializing PyGithub client...")
        gh = Github(token)

        target_repo_string = f"{owner}/{repo_name}"
        print(f"--- DEBUG: Fetching repo object for '{target_repo_string}'...")
        repo = gh.get_repo(target_repo_string)
        print("--- DEBUG: Repo object fetched successfully.")

        try:
            # 5. Try to get existing file (Update Mode)
            print(f"--- DEBUG: Checking if file exists at '{file_path}' on branch '{branch}'...")

            # We MUST get the current file content to retrieve its 'sha'.
            contents = repo.get_contents(file_path, ref=branch)
            print(f"--- DEBUG: File found! Existing SHA: {contents.sha}")

            print("--- DEBUG: Attempting update_file...")
            repo.update_file(
                path=file_path,
                message=commit_message,
                content=new_content,
                sha=contents.sha, # Required: The SHA of the file we are replacing
                branch=branch
            )
            print("--- DEBUG: update_file successful!")

            log.info("commit_workflow_updated repo=%s/%s path=%s", owner, repo_name, file_path)
            return jsonify({"success": True, "action": "updated"}), 200

        except GithubException as e:
            print(f"--- DEBUG: GithubException inside inner try block. Status: {e.status}")

            # 6. If file not found (404), Create it (Create Mode)
            if e.status == 404:
                print("--- DEBUG: File not found (404). Switching to create_file mode...")
                repo.create_file(
                    path=file_path,
                    message=commit_message,
                    content=new_content,
                    branch=branch
                )
                print("--- DEBUG: create_file successful!")

                log.info("commit_workflow_created repo=%s/%s path=%s", owner, repo_name, file_path)
                return jsonify({"success": True, "action": "created"}), 201
            else:
                # If it's a permission error (403) or other issue, raise it to the outer block
                print(f"--- DEBUG: Exception was not 404. Re-raising: {e}")
                raise e

    except GithubException as e:
        # Handle GitHub-specific API errors
        error_msg = e.data.get('message', str(e)) if e.data else str(e)
        print(f"--- DEBUG: FATAL GithubException: {error_msg}")
        print(f"--- DEBUG: Full Exception Data: {e.data}")

        log.error("github_api_error repo=%s/%s error=%s", owner, repo_name, error_msg)
        return jsonify({"error": f"GitHub API Error: {error_msg}"}), 500

    except Exception as e:
        # Handle generic server errors
        print(f"--- DEBUG: FATAL Unexpected Exception: {str(e)}")
        traceback.print_exc() # Print full stack trace to console

        log_exception("commit_workflow_unexpected_error", repo=f"{owner}/{repo_name}")
        return jsonify({"error": str(e)}), 500

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
    results = []

    for wf in all_workflows:
        # Fetch content and check
        print(f"Checking content of: {wf['path']}")
        has_pytest = contains_pytest(owner, repo, wf['path'], token)
        results.append({
            "id": wf["id"],
            "name": wf["name"],
            "path": wf["path"],
            "node_id": wf["node_id"],
            "uses_pytest": has_pytest
        })
    return jsonify(results)

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

        run_info_row = cursor.execute(
            "SELECT tests_all, tests_deselected, tests_failed, time_saved, time_all, created_at FROM runs WHERE id = ?",
            (run_id,)
        ).fetchone()

        savings = {}
        if run_info_row:
            if run_info_row["tests_deselected"] is not None:
                savings["tests_saved"] = run_info_row["tests_deselected"]
            if run_info_row["time_saved"] is not None:
                savings["time_saved"] = run_info_row["time_saved"]
            if run_info_row["time_all"] is not None:
                savings["time_all"] = run_info_row["time_all"]

            test_count = run_info_row["tests_all"]
            tests_failed = run_info_row["tests_failed"] or 0
            create_date = run_info_row["created_at"]

        conn.close()
        log.info("summary_success tests=%s",test_count)

        return jsonify(
            {
                "repo_id": repo_id,
                "job_id": job_id,
                "run_id": run_id,
                "create_date": create_date,
                "test_count": test_count,
                "tests_failed": tests_failed,
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

        query = """
            SELECT
                CASE 
                    WHEN instr(name, '::') > 0 
                        THEN substr(name, 1, instr(name, '::') - 1)
                    ELSE name
                END AS file_name,
                COUNT(*) AS test_count,
                SUM(duration) AS total_duration,
                SUM(CASE WHEN failed = 1 THEN 1 ELSE 0 END) AS failed_count,
                GROUP_CONCAT(
                    DISTINCT
                    CASE 
                        WHEN instr(name, '::') > 0 
                            THEN substr(name, instr(name, '::') + 2)
                        ELSE NULL
                    END
                ) AS test_methods
            FROM tests
            GROUP BY file_name
            ORDER BY file_name;
        """

        test_files = conn.execute(query).fetchall()

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

        query = """
            WITH RankedHistory AS (
                SELECT 
                    test_id,
                    name,
                    failed,
                    test_file,
                    duration,
                    forced,
                    ROW_NUMBER() OVER(PARTITION BY test_id ORDER BY run_id DESC) as rn
                FROM tests_failed_history
                WHERE run_id <= ?
            )
            SELECT 
                rh.test_id AS id,
                rh.name,
                rh.failed,
                -- Use COALESCE to prefer the main table, but fall back to history if deleted
                COALESCE(t.duration, rh.duration) AS duration,
                COALESCE(t.forced, rh.forced) AS forced,
                COALESCE(t.test_file, rh.test_file) AS test_file
            FROM RankedHistory rh
            LEFT JOIN tests t ON rh.test_id = t.id
            WHERE rh.rn = 1 AND rh.failed != -1
            ORDER BY rh.name
        """

        tests = conn.execute(query, (run_id,)).fetchall()
        conn.close()

        return jsonify({
            "run_id": run_id,
            "tests": [dict(test) for test in tests],
            "count": len(tests)
        })

    except Exception:
        log_exception("tests_query", repo_id=repo_id, job_id=job_id)
        return jsonify({"error": "Failed to read tests from history"}), 500

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
            "SELECT * FROM tests WHERE id = ?", (test_id,)
        ).fetchone()
        if not test:
            conn.close()
            log.warning("test_not_found test_id=%s", test_id)
            return jsonify({"error": "Test not found"}), 404

        dependency_row = conn.execute(
            "SELECT file_bitmap, external_packages FROM test_deps WHERE test_id = ?",(test_id,)
        ).fetchone()

        dependencies = []
        external_packages = []

        if dependency_row:
            # Decode the bitmap to get file IDs
            file_ids = _decode_bitmap(dependency_row["file_bitmap"])

            if file_ids:
                # Fetch file metadata for all dependency IDs in one query
                placeholders = ",".join("?" * len(file_ids))
                ids = [i for i in file_ids]
                file_rows = conn.execute(
                    f"SELECT id, path, checksum, fsha, file_type FROM files WHERE id IN ({placeholders})",
                    ids
                ).fetchall()

                for f in file_rows:
                    dependencies.append({
                        "filename": f["path"],
                        "fsha": f["fsha"],
                        "checksum": f["checksum"],
                        "file_type": f["file_type"],
                    })

            # Parse external packages string e.g. "pytest,numpy==2.2.1"
            if dependency_row["external_packages"]:
                external_packages = [
                    p.strip()
                    for p in dependency_row["external_packages"].split(",")
                    if p.strip()
                ]

        conn.close()

        return jsonify({
            "test": {
                "id": test["id"],
                "name": test["name"],
                "duration": test["duration"],
                "failed": test["failed"],
            },
            "dependencies": dependencies,
            "external_packages": external_packages
        })

    except Exception:
        log_exception("test_details_query", repo_id=repo_id, job_id=job_id, test_id=test_id)
        return jsonify({"error": "Failed to read test details"}), 500

@app.route("/api/data/<path:repo_id>/<job_id>/<run_id>/files", methods=["GET"])
def get_files(repo_id: str, job_id: str, run_id: str):
    g.repo_id, g.job_id, g.run_id = repo_id, job_id, run_id

    db_path, resp, code = _open_db_or_404(repo_id, job_id)
    if resp:
        return resp, code

    try:
        conn = get_db_connection(db_path, readonly=True)
        conn.row_factory = sqlite3.Row

        query = """
            SELECT path
            FROM (
                SELECT path, checksum,
                       ROW_NUMBER() OVER(PARTITION BY file_id ORDER BY run_id DESC) as rn
                FROM files_history
                WHERE run_id <= ?
            )
            WHERE rn = 1 AND checksum IS NOT NULL
            ORDER BY path;
        """

        rows = conn.execute(query, (run_id,)).fetchall()
        conn.close()

        files = [{"path": row["path"]} for row in rows]

        return jsonify({
            "run_id": run_id,
            "files": files
        })

    except Exception as e:
        return jsonify({
            "error": "Failed to fetch files from database snapshot",
            "detail": str(e)
        }), 500


@app.route( "/api/data/<path:repo_id>/<job_id>/<run_id>/fileDetails/<path:file_name>", methods=["GET"])
def get_file_details(repo_id: str, job_id: str, run_id: str, file_name: str):
    g.repo_id, g.job_id , g.run_id = repo_id, job_id ,run_id

    db_path, resp, code = _open_db_or_404(repo_id, job_id)
    if resp:
        return resp, code

    try:
        conn = get_db_connection(db_path, readonly=True)
        conn.row_factory = sqlite3.Row

        file = conn.execute(
            "SELECT * FROM files WHERE path = ?", (file_name,)
        ).fetchone()

        if not file:
            conn.close()
            log.warning("file_not_found file_name=%s", file_name)
            return jsonify({"error": "File not found"}), 404

        dependency_rows = conn.execute(
            "SELECT test_id, file_bitmap FROM test_deps"
        ).fetchall()

        affected_tests = []
        for row in dependency_rows:
            file_ids = _decode_bitmap(row["file_bitmap"])
            if file["id"] in file_ids:
                test_id = row["test_id"]
                test_info = conn.execute(
                    "SELECT name, duration, failed FROM tests WHERE id = ?",
                    (test_id,)
                ).fetchone()
                if test_info:
                    log.info("file_test_dependency file_name=%s test_name=%s", file_name, test_info["name"])
                    affected_tests.append({
                        "testId": test_id,
                        "testName": test_info["name"],
                        "duration": test_info["duration"],
                        "failed": test_info["failed"],
                    })

        conn.close()

        return jsonify({"affectedTests": affected_tests})

    except Exception:
        log_exception("file_tests_dependency_query", repo_id=repo_id, job_id=job_id)
        return jsonify({"error": "Failed to retrieve affected tests"}), 500


@app.route("/api/data/<path:repo_id>/<job_id>/<run_id>/fileDependencies", methods=["GET"])
def get_file_dependencies(repo_id: str, job_id: str, run_id: str):
    db_path, resp, code = _open_db_or_404(repo_id, job_id)
    if resp:
        return resp, code

    try:
        conn = get_db_connection(db_path, readonly=True)
        conn.row_factory = sqlite3.Row

        files_query = """
            WITH RankedFiles AS (
                SELECT file_id, path, checksum,
                       ROW_NUMBER() OVER(PARTITION BY file_id ORDER BY run_id DESC) as rn
                FROM files_history
                WHERE run_id <= ?
            )
            SELECT file_id, path 
            FROM RankedFiles 
            WHERE rn = 1 AND checksum IS NOT NULL
        """

        id_to_path = dict(
            (row["file_id"], row["path"])
            for row in conn.execute(files_query, (run_id,)).fetchall()
        )

        deps_query = """
            WITH RankedTests AS (
                SELECT test_id, failed,
                       ROW_NUMBER() OVER(PARTITION BY test_id ORDER BY run_id DESC) as rn
                FROM tests_failed_history
                WHERE run_id <= ?
            ),
            RankedDeps AS (
                SELECT test_id, file_bitmap, external_packages,
                       ROW_NUMBER() OVER(PARTITION BY test_id ORDER BY run_id DESC) as rn
                FROM test_deps_history
                WHERE run_id <= ?
            )
            SELECT d.file_bitmap, d.external_packages
            FROM RankedDeps d
            JOIN RankedTests t ON d.test_id = t.test_id
            WHERE d.rn = 1 AND t.rn = 1 AND t.failed != -1
        """

        dep_rows = conn.execute(deps_query, (run_id, run_id)).fetchall()

        file_deps: dict[str, set[str]] = {}
        file_ext_deps: dict[str, set[str]] = {}

        for row in dep_rows:
            if not row["file_bitmap"]:
                continue

            file_ids = _decode_bitmap(row["file_bitmap"])
            paths = [id_to_path.get(i) for i in file_ids]
            paths = [p for p in paths if p]

            for path in paths:
                file_deps.setdefault(path, set()).update(p for p in paths if p != path)

            if row["external_packages"]:
                pkgs = [p.strip() for p in row["external_packages"].split(",") if p.strip()]
                for path in paths:
                    file_ext_deps.setdefault(path, set()).update(pkgs)

        conn.close()

        return jsonify({
            "run_id": run_id,
            "files": [
                {
                    "filename": filename,
                    "dependencies": sorted(deps),
                    "external_dependencies": sorted(file_ext_deps.get(filename, set())),
                }
                for filename, deps in sorted(file_deps.items())
            ]
        })

    except Exception as e:
        log_exception("file_dependencies_query", repo_id=repo_id, job_id=job_id)
        return jsonify({
            "error": "Failed to fetch file dependencies from database snapshot",
            "detail": str(e)
        }), 500

@app.route("/api/client/testPreferences", methods=["POST"])
def upload_test_preferences():
    """Store user's test preferences (which tests to always run and which to prioritize)"""

    # Get data from request body (JSON)
    data = request.get_json()
    repo_id = data.get("repo_id")
    job_id = data.get("job_id")

    always_run_tests = data.get("alwaysRunTests", [])  # Array of test file names
    prioritized_tests = data.get("prioritizedTests", [])  # Array of test file names

    log.info("Always run tests %s", always_run_tests)
    log.info("Prioritized tests %s", prioritized_tests)
    # Enrich per-request context for logging
    g.repo_id, g.job_id = repo_id or "-", job_id or "-"

    if not repo_id or not job_id:
        log.warning("preferences_missing_params")
        return jsonify({"error": "repo_id and job_id are required"}), 400

    if not isinstance(always_run_tests, list) or not isinstance(prioritized_tests, list):
        log.warning("preferences_invalid_format")
        return jsonify({"error": "alwaysRunTests and prioritizedTests must be arrays"}), 400

    try:
        # Create preferences file path
        job_path = get_job_db_path(repo_id, job_id).parent
        preferences_path = job_path / "test_preferences.json"

        log.info(
            "preferences_write_attempt path=%s always_run=%s prioritized=%s",
            preferences_path,
            len(always_run_tests),
            len(prioritized_tests)
        )

        # Store preferences as JSON
        preferences_data = {
            "repo_id": repo_id,
            "job_id": job_id,
            "always_run_tests": always_run_tests,
            "prioritized_tests": prioritized_tests,
            "updated_at": now_iso(),
        }

        with open(preferences_path, "w") as f:
            json.dump(preferences_data, f, indent=2)

        size = preferences_path.stat().st_size
        log.info(
            "preferences_write_success path=%s size=%s (%s) always_run=%s prioritized=%s",
            preferences_path,
            size,
            human_bytes(size),
            len(always_run_tests),
            len(prioritized_tests)
        )

        return jsonify({
            "success": True,
            "message": f"Test preferences saved for {repo_id}/{job_id}",
            "always_run_count": len(always_run_tests),
            "prioritized_count": len(prioritized_tests),
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
                "prioritized_tests": [],
                "updated_at": None,
            }), 200

        with open(preferences_path, "r") as f:
            preferences_data = json.load(f)

        # Ensure both fields exist for backward compatibility
        if "prioritized_tests" not in preferences_data:
            preferences_data["prioritized_tests"] = []
        if "always_run_tests" not in preferences_data:
            preferences_data["always_run_tests"] = []

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
        total_duration = sum(_parse_test_duration(t) for t in tests)

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
                "message": t.get("error_message") or t.get("call", {}).get("crash", {}).get("message"),
                "longrepr": t.get("longrepr") or t.get("call", {}).get("longrepr"),
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


def _gh_headers():
    """Build GitHub API headers using the session token if available."""
    headers = {"Accept": "application/vnd.github+json"}
    token = session.get("github_token") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_commit_sha_for_run(db_path, run_id: str) -> Optional[str]:
    """Look up commit_id from the testmon DB for a given run_id."""
    try:
        con = sqlite3.connect(str(db_path))
        row = con.execute(
            "SELECT commit_id FROM runs WHERE id = ? LIMIT 1", (run_id,)
        ).fetchone()
        con.close()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def _parse_test_duration(t: dict) -> float:
    """Return test duration, supporting both flat and pytest-json-report nested formats."""
    flat = t.get("duration")
    if flat is not None:
        return float(flat)
    return (
            t.get("setup", {}).get("duration", 0) +
            t.get("call", {}).get("duration", 0) +
            t.get("teardown", {}).get("duration", 0)
    )


def _download_artifact_from_run(repo_id: str, gh_run_id: int, headers: dict) -> Optional[dict]:
    """Find and download the test-report artifact from a specific GitHub Actions run ID."""
    artifacts_url = f"https://api.github.com/repos/{repo_id}/actions/runs/{gh_run_id}/artifacts"
    art_resp = requests.get(artifacts_url, headers=headers, timeout=15)
    if not art_resp.ok:
        return None
    artifacts = art_resp.json().get("artifacts", [])
    artifact = next((a for a in artifacts if "test-report" in a["name"]), None)
    if not artifact:
        log.warning("gh_artifact_not_found repo=%s gh_run_id=%s", repo_id, gh_run_id)
        return None

    zip_url = f"https://api.github.com/repos/{repo_id}/actions/artifacts/{artifact['id']}/zip"
    zip_resp = requests.get(zip_url, headers=headers, timeout=30)
    zip_resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
        json_file = next((n for n in zf.namelist() if n.endswith(".json")), None)
        if not json_file:
            return None
        with zf.open(json_file) as f:
            data = json.load(f)
    log.info("gh_artifact_fetched repo=%s gh_run_id=%s artifact=%s", repo_id, gh_run_id, artifact["name"])
    return data


def _fetch_pytest_report_from_github(repo_id: str, commit_sha: str) -> Optional[dict]:
    """Fetch pytest JSON report artifact from GitHub Actions for a given commit SHA."""
    try:
        headers = _gh_headers()

        runs_url = f"https://api.github.com/repos/{repo_id}/actions/runs?head_sha={commit_sha}"
        runs_resp = requests.get(runs_url, headers=headers, timeout=15)
        runs_resp.raise_for_status()
        workflow_runs = runs_resp.json().get("workflow_runs", [])
        if not workflow_runs:
            log.warning("gh_artifact_no_runs repo=%s sha=%s", repo_id, commit_sha)
            return None

        for run in workflow_runs:
            data = _download_artifact_from_run(repo_id, run["id"], headers)
            if data:
                return data

        log.warning("gh_artifact_not_found repo=%s sha=%s", repo_id, commit_sha)
        return None

    except Exception:
        log_exception("gh_artifact_fetch", repo_id=repo_id, commit_sha=commit_sha)
        return None


@app.route("/api/pytest-tests-from-url", methods=["GET"])
def get_pytest_tests_from_url():
    """Fetch pytest test report directly from a GitHub Actions URL.
    Query param: url = https://github.com/{owner}/{repo}/actions/runs/{run_id}[/job/{job_id}]
    """
    gh_url = request.args.get("url")
    if not gh_url:
        return jsonify({"error": "url param required"}), 400

    match = re.search(r"github\.com/([^/]+/[^/]+)/actions/runs/(\d+)", gh_url)
    if not match:
        return jsonify({"error": "Could not parse GitHub Actions URL"}), 400

    repo_id = match.group(1)
    gh_run_id = int(match.group(2))

    try:
        data = _download_artifact_from_run(repo_id, gh_run_id, _gh_headers())
        if not data:
            return jsonify({"error": "No test-report artifact found"}), 404

        tests = []
        for t in data.get("tests", []):
            outcome = t.get("outcome")
            duration = _parse_test_duration(t)
            tests.append({
                "nodeid": t.get("nodeid"),
                "lineno": t.get("lineno"),
                "outcome": outcome,
                "duration": duration,
                "error_message": (t.get("error_message") or t.get("call", {}).get("crash", {}).get("message")) if outcome == "failed" else None,
                "longrepr": (t.get("longrepr") or t.get("call", {}).get("longrepr")) if outcome == "failed" else None,
            })

        log.info("pytest_tests_from_url repo=%s gh_run_id=%s count=%s", repo_id, gh_run_id, len(tests))
        return jsonify({
            "repo_id": repo_id,
            "gh_run_id": gh_run_id,
            "summary": data.get("summary", {}),
            "tests": tests,
        })

    except Exception:
        log_exception("pytest_tests_from_url", url=gh_url)
        return jsonify({"error": "Failed to fetch pytest tests"}), 500


@app.route("/api/data/<path:repo_id>/<job_id>/<run_id>/pytest-tests", methods=["GET"])
def get_pytest_tests(repo_id: str, job_id: str, run_id: str):
    """Get all tests from pytest JSON report fetched from GitHub Actions artifact."""
    g.repo_id, g.job_id = repo_id, job_id

    try:
        gh_run_id = request.args.get("gh_run_id")
        commit_sha = request.args.get("commit_id")

        if gh_run_id:
            log.info("pytest_tests_direct_gh_run repo=%s gh_run_id=%s", repo_id, gh_run_id)
            data = _download_artifact_from_run(repo_id, int(gh_run_id), _gh_headers())
        elif commit_sha:
            log.info("pytest_tests_commit_sha repo=%s sha=%s", repo_id, commit_sha)
            data = _fetch_pytest_report_from_github(repo_id, commit_sha)
        else:
            # Last resort: look up commit SHA from DB
            db_path = get_job_db_path(repo_id, job_id)
            commit_sha = _get_commit_sha_for_run(db_path, run_id) if db_path.exists() else None
            if not commit_sha:
                log.warning("pytest_tests_no_commit repo=%s job=%s run=%s", repo_id, job_id, run_id)
                return jsonify({"error": "No commit SHA found for this run"}), 404
            data = _fetch_pytest_report_from_github(repo_id, commit_sha)
        if not data:
            return jsonify({"error": "No pytest report artifact found on GitHub"}), 404

        tests = []
        for t in data.get("tests", []):
            outcome = t.get("outcome")
            duration = _parse_test_duration(t)
            tests.append({
                "nodeid": t.get("nodeid"),
                "lineno": t.get("lineno"),
                "outcome": outcome,
                "duration": duration,
                "error_message": (t.get("error_message") or t.get("call", {}).get("crash", {}).get("message")) if outcome == "failed" else None,
                "longrepr": (t.get("longrepr") or t.get("call", {}).get("longrepr")) if outcome == "failed" else None,
            })

        log.info("pytest_tests_success count=%s", len(tests))
        return jsonify({
            "repo_id": repo_id,
            "job_id": job_id,
            "run_id": run_id,
            "commit_sha": commit_sha,
            "tests": tests,
        })

    except Exception:
        log_exception("pytest_tests_read", repo_id=repo_id, job_id=job_id, run_id=run_id)
        return jsonify({"error": "Failed to fetch pytest tests"}), 500


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

# -----------------------------------------------------------------------------
# IMPACT ESTIMATION ENDPOINTS
# -----------------------------------------------------------------------------

@app.route("/health")
def health():
    repo_count = len(get_metadata().get("repos", {}))
    log.info("health_check repo_count=%s data_dir=%s", repo_count, BASE_DATA_DIR)
    return jsonify(
        {"status": "healthy!!!", "data_dir": str(BASE_DATA_DIR), "repo_count": repo_count}
    )

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
    port = int(os.environ.get("PORT", 8004))
    host = os.environ.get("HOST", "0.0.0.0")
    debug = os.environ.get("FLASK_DEBUG", "true").lower() == "true"

    log.info("server_start data_dir=%s port=%s", BASE_DATA_DIR.absolute(), port)
    print("Starting Testmon Multi-Project Server")
    print(f"Data directory: {BASE_DATA_DIR.absolute()}")
    print(f"Server running on http://{host}:{port}")
    app.run(debug=debug, host=host, port=port)