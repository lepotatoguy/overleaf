"""Authenticated clients for the two servers. Secrets live only in .state/auth-*.json (mode 600)."""

import getpass
import os
import re
from pathlib import Path

import requests
from olsync.auth import load_auth, save_auth
from olsync.olclient import OverleafClient

STATE_DIR = Path(os.environ.get("OLBRIDGE_STATE") or Path.home() / "overleaf-mirror" / ".state")
AUTH = {"com": STATE_DIR / "auth-com.json", "local": STATE_DIR / "auth-local.json"}
LOCAL_URL = "http://localhost:8788"
TIMEOUT = 60


def make_client(side):
    """OverleafClient for 'com' or 'local', plus its raw auth for the few calls olsync lacks."""
    auth = load_auth(AUTH[side])
    return OverleafClient(cookie=auth["cookies"], csrf=auth["csrf"], base_url=auth["server"]), auth


def upload_zip_as_project(auth, name, zip_bytes):
    """Create a new project from a zip on the given server. Returns the new project id."""
    response = requests.post(
        auth["server"] + "/project/new/upload",
        params={"_csrf": auth["csrf"]},
        cookies=auth["cookies"],
        data={"name": f"{name}.zip"},
        files={"qqfile": (f"{name}.zip", zip_bytes, "application/zip")},
        timeout=TIMEOUT,
    )
    body = response.json() if response.ok else {}
    if not body.get("success"):
        raise RuntimeError(f"project upload failed (HTTP {response.status_code})")
    return body["project_id"]


def login_local(email):
    """Log in to the local server with a password typed at a hidden prompt; only the cookie is stored."""
    password = getpass.getpass(f"Local Overleaf password for {email}: ")
    session = requests.Session()
    page = session.get(LOCAL_URL + "/login", timeout=TIMEOUT)
    token = re.search(r'name="ol-csrfToken" content="([^"]+)"', page.text).group(1)
    reply = session.post(
        LOCAL_URL + "/login", json={"_csrf": token, "email": email, "password": password}, timeout=TIMEOUT
    )
    if not reply.ok:
        raise SystemExit("Login rejected by the local server.")
    dashboard = session.get(LOCAL_URL + "/project", timeout=TIMEOUT)
    csrf = re.search(r'name="ol-csrfToken" content="([^"]+)"', dashboard.text).group(1)
    save_auth(AUTH["local"], session.cookies.get_dict(), csrf, LOCAL_URL)
    print("Local session saved.")
