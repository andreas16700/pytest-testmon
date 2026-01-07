"""
Functions to sync testmon data with remote server
"""
import os
import sqlite3
import json
from pathlib import Path
from typing import Optional, Dict
from ezmon.common import get_logger
import requests
import urllib.request
logger = get_logger(__name__)


def get_env_vars() -> Dict[str, Optional[str]]:
    """Get required environment variables for server sync."""
    return {
        "server_url": os.getenv("TESTMON_SERVER"),
        "repo_id": os.getenv("REPO_ID") or os.getenv("GITHUB_REPOSITORY"),
        "job_id": os.getenv("JOB_ID"),
        "run_id": os.getenv("RUN_ID") or os.getenv("GITHUB_RUN_ID"),
    }


def should_sync() -> bool:
    """Check if all required environment variables are set."""
    env_vars = get_env_vars()
    return all(env_vars.values())


def is_valid_testmon_file(testmon_file: Path) -> bool:
    """
    Validate that testmon file is a valid SQLite database with tables.
    
    Returns:
        True if valid, False otherwise
    """
    if not testmon_file.exists():
        return False
    
    if testmon_file.stat().st_size == 0:
        logger.warning("Testmon file is empty (0 bytes)")
        return False
    
    try:
        conn = sqlite3.connect(testmon_file)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        conn.close()
        
        if not tables:
            logger.warning("Testmon database has no tables")
            return False
        
        logger.debug(f"Testmon file valid: {testmon_file.stat().st_size:,} bytes, {len(tables)} tables")
        return True
        
    except Exception as e:
        logger.warning(f"Cannot verify testmon database: {e}")
        return False


def download_testmon_data(testmon_file: Path) -> bool:
    """
    Download testmon data from server before test run.
    
    Returns:
        True if download successful, False otherwise
    """
    if not should_sync():
        logger.debug("Server sync disabled (missing env vars)")
        return False
    
    env_vars = get_env_vars()
    server_url = env_vars["server_url"]
    repo_id = env_vars["repo_id"]
    job_id = env_vars["job_id"]
    run_id = env_vars["run_id"]

    logger.info(f"📥 Downloading from server: repo={repo_id}, job={job_id}, run={run_id}")
    
    try:
        import urllib.request
        import urllib.error
        
        url = f"{server_url}/api/client/download?repo_id={repo_id}&job_id={job_id}&run_id={run_id}"
        
        with urllib.request.urlopen(url, timeout=10) as response:
            if response.status == 200:
                content = response.read()
                with open(testmon_file, 'wb') as f:
                    f.write(content)
                
                if is_valid_testmon_file(testmon_file):
                    logger.info(f"✅ Downloaded testmon data ({len(content):,} bytes)")
                    return True
                else:
                    logger.warning("Downloaded file is invalid")
                    testmon_file.unlink(missing_ok=True)
                    return False
                    
    except urllib.error.HTTPError as e:
        if e.code == 404:
            logger.info("ℹ️  No existing testmon data on server (first run)")
        else:
            logger.warning(f"⚠️  Download failed (HTTP {e.code})")
        return False
        
    except Exception as e:
        logger.warning(f"⚠️  Download failed: {e}")
        return False


def get_test_preferences() -> dict:
    """
    Download test_preferences.json from server.
    Returns dict with 'always_run_tests' list.
    """
    if not should_sync():
        return {"always_run_tests": []}

    env_vars = get_env_vars()
    server_url = env_vars["server_url"]
    repo_id = env_vars["repo_id"]
    job_id = env_vars["job_id"]
    
    # Assuming an endpoint exists for this, or we construct the URL
    url = f"{server_url}/api/client/testPreferences?repo_id={repo_id}&job_id={job_id}"
    
    logger.info(f" Fetching test preferences...")
    
    try:
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                logger.info(f"✅ Loaded preferences. Always run: {len(data.get('always_run_tests', []))} files")
                return data
    except Exception as e:
        logger.warning(f" Could not fetch preferences: {e}")
    
    return {"always_run_tests": []}

def upload_testmon_data(testmon_file: Path, repo_name: Optional[str] = None) -> bool:
    """
    Upload testmon data to server after test run.
    
    Returns:
        True if upload successful, False otherwise
    """
    if not should_sync():
        logger.debug("Server sync disabled (missing env vars)")
        return False
    
    if not is_valid_testmon_file(testmon_file):
        logger.warning("⚠️  Cannot upload: invalid testmon file")
        return False
    
    env_vars = get_env_vars()
    server_url = env_vars["server_url"]
    repo_id = env_vars["repo_id"]
    
    url = f"{server_url}/api/client/upload"
    
    logger.info(f"📤 Uploading testmon data: repo={repo_id}, size={testmon_file.stat().st_size:,} bytes")
    
    try:
        # Use requests library - much simpler for multipart uploads
        with open(testmon_file, 'rb') as f:
            files = {
                'file': ('.testmondata', f, 'application/octet-stream')
            }
            data = {
                'repo_id': repo_id,
                'job_id': env_vars["job_id"],
                'run_id': env_vars["run_id"],
                'repo_name': repo_name or repo_id,
            }
            
            logger.debug(f"Upload URL: {url}")
            logger.debug(f"Upload data: {data}")
            
            response = requests.post(url, files=files, data=data, timeout=30)
            
            if response.status_code == 200:
                logger.info("✅ Upload successful")
                logger.debug(f"Response: {response.text}")
                return True
            else:
                logger.warning(f"⚠️  Upload failed (HTTP {response.status_code})")
                logger.warning(f"Response body: {response.text}")
                return False
                
    except Exception as e:
        logger.warning(f"⚠️  Upload failed: {e}")
        logger.exception("Full error traceback:")
        logger.info("   Next run won't have optimization data")
        return False

def upload_dependency_graph(graph_file: Path) -> bool:
    """
    Upload dependency graph HTML to server after test run.
    Returns true if upload successful, false otherwise
    """
    if not should_sync():
        logger.debug("Server sync disabled (missing env vars)")
        return False

    if not graph_file.exists():
        logger.debug(f"No dependency graph found at {graph_file} to upload.")
        return False

    env_vars = get_env_vars()
    server_url = env_vars["server_url"]
    repo_id = env_vars["repo_id"]

    url = f"{server_url}/api/client/upload_graph"

    logger.info(f"Uploading dependency graph: repo={repo_id}, size={graph_file.stat().st_size:,} bytes")

    try:
        with open(graph_file, 'rb') as f:
            files = {
                'file': ('dependency_graph.html', f, 'text/html')
            }
            data = {
                'repo_id': repo_id,
                'job_id': env_vars["job_id"],
                'run_id': env_vars["run_id"],
                'type': 'graph'
            }

            response = requests.post(url, files=files, data=data, timeout=30)

            if response.status_code == 200:
                logger.info("Graph upload successful")
                return True
            else:
                logger.warning(f"Graph upload failed (HTTP {response.status_code})")
                logger.debug(f"Response: {response.text}")
                return False

    except Exception as e:
        logger.warning(f"Graph upload exception: {e}")
        return False