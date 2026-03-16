"""
Functions to fetch test preferences from remote server.
"""
import json
import os
import urllib.request
from typing import Optional, Dict

from ezmon.common import get_logger
from ezmon.net_db import get_net_db_config

logger = get_logger(__name__)


def get_test_preferences() -> dict:
    """
    Download test_preferences.json from server.
    Returns dict with 'always_run_tests' and 'prioritized_tests' lists.
    """
    net_config = get_net_db_config()
    if net_config is None:
        return {"always_run_tests": [], "prioritized_tests": []}

    server_url = net_config["server_url"]
    repo_id = net_config["repo_id"]
    job_id = net_config["job_id"]

    url = f"{server_url}/api/client/testPreferences?repo_id={repo_id}&job_id={job_id}"

    logger.info("Fetching test preferences...")

    try:
        req = urllib.request.Request(url, method='GET')
        auth_token = net_config.get("auth_token")
        if auth_token:
            req.add_header("Authorization", f"Bearer {auth_token}")
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                always_run = data.get('always_run_tests', [])
                prioritized = data.get('prioritized_tests', [])
                logger.info(f"Loaded preferences. Always run: {len(always_run)} files, Prioritized: {len(prioritized)} files")
                return {"always_run_tests": always_run, "prioritized_tests": prioritized}
    except Exception as e:
        logger.warning(f"Could not fetch preferences: {e}")

    return {"always_run_tests": [], "prioritized_tests": []}
