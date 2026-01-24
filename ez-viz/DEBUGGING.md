# ez-viz Server Debugging Guide

This guide covers debugging the Flask server, testing the new RPC endpoints, and verifying the NetDB implementation works correctly.

## Table of Contents

1. [Local Development Setup](#local-development-setup)
2. [Running the Server Locally](#running-the-server-locally)
3. [Debugging Server Issues](#debugging-server-issues)
4. [Testing RPC Endpoints](#testing-rpc-endpoints)
5. [Testing NetDB Client](#testing-netdb-client)
6. [Production Debugging](#production-debugging)
7. [Common Issues](#common-issues)

---

## Local Development Setup

### Prerequisites

```bash
# Install Python dependencies
cd ez-viz
pip install -r requirements.txt

# Additional dependencies for testing
pip install requests pytest
```

### Environment Variables

Create a `.env` file or export these variables:

```bash
# Required for OAuth (can use dummy values for local testing)
export GITHUB_CLIENT_ID=your-client-id
export GITHUB_CLIENT_SECRET=your-client-secret
export SECRET_KEY=dev-secret-key-change-in-production

# Optional: Custom data directory
export TESTMON_DATA_DIR=/path/to/data
```

---

## Running the Server Locally

### Option 1: Flask Development Server (Recommended for Debugging)

```bash
cd ez-viz

# Run with debug mode enabled
FLASK_DEBUG=1 python app.py

# Or with explicit host/port
FLASK_DEBUG=1 python -c "from app import app; app.run(host='127.0.0.1', port=8004, debug=True)"
```

The debug server provides:
- Auto-reload on code changes
- Detailed error pages with stack traces
- Interactive debugger in browser

### Option 2: Gunicorn (Production-like)

```bash
cd ez-viz
gunicorn -w 1 -b 127.0.0.1:8004 --reload --log-level debug app:app
```

### Option 3: PM2 (Production Configuration)

```bash
# Start with PM2
pm2 start app.py --name ezmon-server --interpreter python3

# View logs
pm2 logs ezmon-server

# Restart
pm2 restart ezmon-server
```

---

## Debugging Server Issues

### Enable Verbose Logging

Add this near the top of `app.py` (after imports):

```python
import logging
logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)
```

### Check Server Health

```bash
# Health endpoint
curl http://localhost:8004/health

# Expected response:
# {"status": "healthy"}
```

### View Request/Response Details

Add a request logger in `app.py`:

```python
@app.before_request
def log_request():
    log.debug(f"Request: {request.method} {request.path}")
    log.debug(f"Headers: {dict(request.headers)}")
    if request.data:
        log.debug(f"Body: {request.data[:500]}")  # First 500 chars

@app.after_request
def log_response(response):
    log.debug(f"Response: {response.status_code}")
    return response
```

### Debug Specific Endpoints

Add print statements or use Python debugger:

```python
@app.route("/api/rpc/session/initiate", methods=["POST"])
@rpc_auth_required
def rpc_session_initiate():
    import pdb; pdb.set_trace()  # Breakpoint
    # ... rest of function
```

---

## Database Concurrency

The server uses SQLite with special handling for concurrent access from multiple CI jobs.

### Key Helper Functions

**`connect_db_with_retry(db_path, max_retries=5, base_delay=0.1)`**
- Connects to SQLite with exponential backoff retry logic
- Enables WAL mode for better concurrent read/write performance
- Sets 30-second busy timeout
- Enables foreign key constraints

**`cleanup_old_environments(cursor, environment_name, keep_id)`**
- Safely deletes old environments with the same name
- Catches IntegrityError for environments still in use by concurrent requests
- Used for deferred cleanup after the main transaction commits

### Transaction Strategy

The RPC endpoints use this pattern for safe concurrent access:

```python
# 1. Connect with retry logic
conn = connect_db_with_retry(db_path)
cursor = conn.cursor()

# 2. Acquire write lock early with IMMEDIATE transaction
cursor.execute("BEGIN IMMEDIATE")

# 3. Perform operations...

# 4. Commit main transaction
conn.commit()

# 5. Deferred cleanup (non-critical, can fail)
try:
    cleanup_old_environments(cursor, env_name, new_id)
    conn.commit()
except Exception:
    pass  # Log and continue

conn.close()
```

### Why Deferred Deletion?

When CI jobs run in parallel (e.g., matrix builds with py3.10, py3.11, py3.12), multiple requests may call `/api/rpc/session/initiate` simultaneously. If packages have changed, the old approach immediately deleted the old environment, causing:

1. **Race condition**: Request A deletes environment while Request B still references it
2. **FK violation**: Request B's operations fail with `FOREIGN KEY constraint failed`

The fix defers deletion until after the main transaction commits, so concurrent requests see consistent data.

---

## Testing RPC Endpoints

### Manual Testing with curl

#### 1. Test Session Initiation

```bash
# Start a new session
curl -X POST http://localhost:8004/api/rpc/session/initiate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test-token" \
  -H "X-Repo-ID: test/repo" \
  -H "X-Job-ID: test-job" \
  -d '{
    "repo_id": "test/repo",
    "job_id": "test-job",
    "environment_name": "default",
    "system_packages": "pytest 7.0.0",
    "python_version": "3.11.0"
  }'

# Expected response:
# {"session_id": "uuid-here", "exec_id": 1, "filenames": [], "packages_changed": false}
```

#### 2. Test Batch Insert

```bash
curl -X POST http://localhost:8004/api/rpc/test_execution/batch_insert \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test-token" \
  -H "X-Repo-ID: test/repo" \
  -H "X-Job-ID: test-job" \
  -d '{
    "exec_id": 1,
    "tests": {
      "tests/test_example.py::test_one": {
        "duration": 0.5,
        "failed": false,
        "forced": true,
        "deps": [
          {
            "filename": "src/example.py",
            "fsha": "abc123",
            "method_checksums": "00000000"
          }
        ]
      }
    }
  }'

# Expected response:
# {"success": true, "inserted": 1}
```

#### 3. Test Session Finish

```bash
curl -X POST http://localhost:8004/api/rpc/session/finish \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test-token" \
  -H "X-Repo-ID: test/repo" \
  -H "X-Job-ID: test-job" \
  -H "X-Session-ID: uuid-from-initiate" \
  -d '{
    "exec_id": 1,
    "select": true
  }'

# Expected response:
# {"success": true, "run_saved_time": ..., ...}
```

#### 4. Test Get All Tests

```bash
curl -X GET "http://localhost:8004/api/rpc/tests/all?exec_id=1" \
  -H "Authorization: Bearer test-token" \
  -H "X-Repo-ID: test/repo" \
  -H "X-Job-ID: test-job"

# Expected response:
# {"tests": {"tests/test_example.py::test_one": {"duration": 0.5, ...}}}
```

### Automated Testing Script

Create `test_rpc_endpoints.py`:

```python
#!/usr/bin/env python3
"""Test script for RPC endpoints."""
import requests
import json
import sys

BASE_URL = "http://localhost:8004"
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": "Bearer test-token",
    "X-Repo-ID": "test/repo",
    "X-Job-ID": "test-job-rpc-test",
}


def test_health():
    """Test health endpoint."""
    r = requests.get(f"{BASE_URL}/health")
    assert r.status_code == 200, f"Health check failed: {r.text}"
    print("✓ Health check passed")


def test_session_flow():
    """Test complete session flow."""
    # 1. Initiate session
    r = requests.post(
        f"{BASE_URL}/api/rpc/session/initiate",
        headers=HEADERS,
        json={
            "repo_id": "test/repo",
            "job_id": "test-job-rpc-test",
            "environment_name": "default",
            "system_packages": "pytest 7.0.0",
            "python_version": "3.11.0",
        },
    )
    assert r.status_code == 200, f"Session initiate failed: {r.text}"
    data = r.json()
    session_id = data["session_id"]
    exec_id = data["exec_id"]
    print(f"✓ Session initiated: session_id={session_id}, exec_id={exec_id}")

    # 2. Insert test results
    r = requests.post(
        f"{BASE_URL}/api/rpc/test_execution/batch_insert",
        headers=HEADERS,
        json={
            "exec_id": exec_id,
            "tests": {
                "tests/test_example.py::test_one": {
                    "duration": 0.5,
                    "failed": False,
                    "forced": True,
                    "deps": [
                        {
                            "filename": "src/example.py",
                            "fsha": "abc123",
                            "method_checksums": "00000000",
                        }
                    ],
                }
            },
        },
    )
    assert r.status_code == 200, f"Batch insert failed: {r.text}"
    print(f"✓ Batch insert: {r.json()}")

    # 3. Get all tests
    r = requests.get(
        f"{BASE_URL}/api/rpc/tests/all",
        headers=HEADERS,
        params={"exec_id": exec_id},
    )
    assert r.status_code == 200, f"Get tests failed: {r.text}"
    tests = r.json().get("tests", {})
    print(f"✓ Get all tests: {len(tests)} tests found")

    # 4. Finish session
    headers_with_session = {**HEADERS, "X-Session-ID": session_id}
    r = requests.post(
        f"{BASE_URL}/api/rpc/session/finish",
        headers=headers_with_session,
        json={"exec_id": exec_id, "select": True},
    )
    assert r.status_code == 200, f"Session finish failed: {r.text}"
    print(f"✓ Session finished: {r.json()}")


def test_auth_required():
    """Test that auth is required."""
    r = requests.post(
        f"{BASE_URL}/api/rpc/session/initiate",
        headers={"Content-Type": "application/json"},  # No auth header
        json={"repo_id": "test/repo", "job_id": "test-job"},
    )
    assert r.status_code == 401, f"Expected 401, got {r.status_code}"
    print("✓ Auth required check passed")


def main():
    print(f"Testing RPC endpoints at {BASE_URL}\n")

    try:
        test_health()
        test_auth_required()
        test_session_flow()
        print("\n✓ All tests passed!")
        return 0
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        return 1
    except requests.exceptions.ConnectionError:
        print(f"\n✗ Cannot connect to {BASE_URL}. Is the server running?")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

Run it:

```bash
python test_rpc_endpoints.py
```

---

## Testing NetDB Client

### Unit Test with Mocked Server

Create `test_net_db.py`:

```python
#!/usr/bin/env python3
"""Test NetDB client against a running server."""
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ezmon.net_db import NetDB
from ezmon.process_code import checksums_to_blob


def test_net_db_flow():
    """Test complete NetDB flow against local server."""

    # Create NetDB instance
    net_db = NetDB(
        server_url="http://localhost:8004",
        repo_id="test/repo",
        job_id="netdb-test-job",
        auth_token="test-token",
    )

    # 1. Initiate execution
    result = net_db.initiate_execution(
        environment_name="default",
        system_packages="pytest 7.0.0",
        python_version="3.11.0",
        execution_metadata={},
    )
    print(f"✓ Initiated: exec_id={result['exec_id']}")
    exec_id = result["exec_id"]

    # 2. Insert test results
    test_data = {
        "tests/test_netdb.py::test_example": {
            "duration": 1.5,
            "failed": False,
            "forced": True,
            "deps": [
                {
                    "filename": "src/netdb_example.py",
                    "fsha": "sha256hash",
                    "mtime": 1234567890.0,
                    "method_checksums": [12345, 67890],
                }
            ],
        }
    }
    net_db.insert_test_file_fps(test_data, exec_id=exec_id)
    print("✓ Inserted test results")

    # 3. Finish execution
    net_db.finish_execution(exec_id)
    print("✓ Finished execution")

    # 4. Query tests
    tests = net_db.all_test_executions(exec_id)
    print(f"✓ Retrieved {len(tests)} tests")

    # Cleanup
    net_db.close()
    print("\n✓ NetDB client test passed!")


if __name__ == "__main__":
    test_net_db_flow()
```

Run it:

```bash
# From project root
python ez-viz/test_net_db.py
```

### Integration Test with Sample Project

```bash
# Set up environment for NetDB mode
export TESTMON_NET_ENABLED=true
export TESTMON_SERVER=http://localhost:8004
export REPO_ID=test/sample-project
export JOB_ID=integration-test
export TESTMON_AUTH_TOKEN=test-token

# Run integration tests
cd integration_tests/sample_project
pytest --ezmon -v

# Check that data was stored on server
curl -X GET "http://localhost:8004/api/rpc/tests/all?exec_id=1" \
  -H "Authorization: Bearer test-token" \
  -H "X-Repo-ID: test/sample-project" \
  -H "X-Job-ID: integration-test"
```

---

## Production Debugging

### SSH into Server

```bash
ssh your-server
```

### Check PM2 Status

```bash
pm2 status
pm2 logs ezmon-server --lines 100
pm2 logs ezmon-server --err --lines 50  # Errors only
```

### Check nginx Logs

```bash
# Access logs
sudo tail -f /var/log/nginx/access.log | grep api/rpc

# Error logs
sudo tail -f /var/log/nginx/error.log
```

### Test Endpoint from Server

```bash
# Test locally on server
curl -X GET http://127.0.0.1:8004/health

# Test through nginx
curl -X GET https://your-domain.com/health
```

### Check Database Files

```bash
# List all testmon databases
find /path/to/data -name "*.testmondata" -ls

# Check a specific database
sqlite3 /path/to/data/repo/job/.testmondata ".tables"
sqlite3 /path/to/data/repo/job/.testmondata "SELECT count(*) FROM test_execution"
```

### Restart Server

```bash
pm2 restart ezmon-server
```

### nginx Configuration for RPC Endpoints

Add to your nginx config:

```nginx
location /api/rpc/ {
    proxy_pass http://127.0.0.1:8004;
    proxy_read_timeout 120s;
    proxy_connect_timeout 10s;
    client_max_body_size 10m;

    # Headers
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Reload nginx:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

---

## Common Issues

### 1. "Unauthorized" Error (401)

**Cause**: Missing or invalid authentication token.

**Fix**:
```bash
# Ensure token is set
export TESTMON_AUTH_TOKEN=your-token

# Or add to curl
curl -H "Authorization: Bearer your-token" ...
```

### 2. "Connection Refused"

**Cause**: Server not running or wrong port.

**Fix**:
```bash
# Check if server is running
ps aux | grep app.py
pm2 status

# Start server
cd ez-viz && python app.py
```

### 3. "Invalid Request Data" (400)

**Cause**: Malformed JSON or missing required fields.

**Fix**: Check request body format:
```bash
# Debug the request
curl -v -X POST http://localhost:8004/api/rpc/session/initiate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test-token" \
  -d '{"repo_id": "test/repo", "job_id": "test-job"}'
```

### 4. "Database Locked" Error

**Cause**: SQLite locking issues with concurrent access.

**Fix**:
- The server now uses `connect_db_with_retry()` with exponential backoff
- WAL mode is enabled by default: `PRAGMA journal_mode = WAL`
- Busy timeout is set to 30 seconds: `PRAGMA busy_timeout = 30000`
- Transactions use `BEGIN IMMEDIATE` to acquire write locks early

### 5. "FOREIGN KEY constraint failed" (IntegrityError)

**Cause**: Race condition when multiple concurrent CI jobs call `/api/rpc/session/initiate` simultaneously and packages have changed. The old code immediately deleted the old environment, causing FK violations for concurrent requests still referencing it.

**Fix** (implemented in app.py):
- Environment deletion is now **deferred** - new environment is created and committed first
- Old environments are cleaned up after the main transaction commits
- The `cleanup_old_environments()` helper catches IntegrityError for environments still in use
- If cleanup fails, it's logged but doesn't fail the request

**Testing concurrent access**:
```python
# Test script to verify concurrent session handling
import concurrent.futures
import requests

def initiate_session(job_id):
    return requests.post(
        "http://localhost:8004/api/rpc/session/initiate",
        headers={"Authorization": "Bearer your-token", ...},
        json={"repo_id": "test/repo", "job_id": job_id, ...},
    )

# Simulate 5 concurrent CI jobs
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
    futures = [executor.submit(initiate_session, f"job-{i}") for i in range(5)]
    results = [f.result() for f in futures]
    assert all(r.status_code == 200 for r in results)
```

### 6. Gzip Decompression Failed

**Cause**: Client sent gzip-compressed data but header is missing.

**Fix**: Ensure `Content-Encoding: gzip` header is set when sending compressed data.

### 7. Session Not Found

**Cause**: Session expired (30-minute TTL) or server restarted.

**Fix**: Start a new session. Sessions are stored in memory and don't persist across restarts.

### 9. Session Finish Timeout for Large Test Suites

**Cause**: For large test suites like matplotlib (~9000 tests), the `/api/rpc/session/finish` endpoint may timeout because it copies all test data to history tables and runs orphan cleanup.

**Fix** (implemented in app.py):
- History table operations are now batched (5000 records per batch)
- Orphan cleanup uses efficient `LEFT JOIN` instead of slow `NOT IN` subquery
- Added dedicated index on `test_execution_file_fp(fingerprint_id)` for fast joins
- Added detailed timing logs to identify bottlenecks
- Clients can pass `skip_history: true` to skip non-critical history operations

**Monitoring**: Check logs for timing breakdown:
```
rpc_session_finish_history tests=9000 tefp=450000 orphans_deleted=123
time_tests_ms=500 time_fp_ms=100 time_tefp_ms=2000 time_coverage_ms=50 time_orphans_ms=300
```

**Client-side mitigation**: Increase timeout or use `skip_history`:
```python
# In NetDB client, increase timeout for large test suites
response = session.post(
    f"{server}/api/rpc/session/finish",
    json={"exec_id": exec_id, "select": True, "skip_history": True},
    timeout=120,  # 2 minutes instead of 60s
)
```

### 10. NetDB Mode Not Activating

**Cause**: Environment variables not set correctly.

**Fix**:
```bash
# Verify all required vars are set
echo $TESTMON_NET_ENABLED   # Must be "true"
echo $TESTMON_SERVER        # Must be URL
echo $REPO_ID               # Must be set
echo $JOB_ID                # Must be set

# Check ezmon logs
pytest --ezmon -v 2>&1 | grep -i netdb
```

---

## Verification Checklist

After making changes, verify everything works:

- [ ] Server starts without errors: `python app.py`
- [ ] Health check passes: `curl http://localhost:8004/health`
- [ ] RPC endpoint tests pass: `python test_rpc_endpoints.py`
- [ ] NetDB client tests pass: `python test_net_db.py`
- [ ] Integration tests pass with NetDB: See "Integration Test with Sample Project"
- [ ] Existing visualization endpoints still work: Open frontend in browser
- [ ] nginx config is valid: `sudo nginx -t`
- [ ] PM2 process is healthy: `pm2 status`
