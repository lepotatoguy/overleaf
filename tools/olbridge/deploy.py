"""Render the proxy config and launchd agents. Secrets only ever land in .state/ (mode 600)."""

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

import clients
import server

HERE = Path(__file__).resolve().parent
STATE = clients.STATE_DIR
NGINX_DIR = STATE / "nginx"
NGINX = shutil.which("nginx") or "/opt/homebrew/bin/nginx"
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
BREW_PATH = "/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
LABELS = {"serve": "local.olbridge.serve", "nginx": "local.olbridge.nginx", "colima": "local.olbridge.colima"}


def _write_private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def setup_proxy():
    """Write .state/nginx/nginx.conf and check it with `nginx -t`."""
    for sub in ("", "tmp"):
        (NGINX_DIR / sub).mkdir(parents=True, exist_ok=True, mode=0o700)
    template = (HERE / "deploy" / "nginx.conf.tmpl").read_text()
    config = template.replace("{{STATE}}", str(STATE)).replace("{{TOKEN}}", server.load_token())
    _write_private(NGINX_DIR / "nginx.conf", config)
    subprocess.run([NGINX, "-t", "-c", str(NGINX_DIR / "nginx.conf"), "-p", str(NGINX_DIR)], check=True)


def _agent(label, args, keep_alive, log=None):
    plist = {
        "Label": label,
        "ProgramArguments": args,
        "RunAtLoad": True,
        "KeepAlive": keep_alive,
        "EnvironmentVariables": {"PATH": BREW_PATH, "HOME": str(Path.home())},
        "ProcessType": "Background",
        "Umask": 0o077,
    }
    if log:
        plist["StandardOutPath"] = plist["StandardErrorPath"] = str(log)
    return plist


def install_agents():
    """Install and load: Colima VM, nginx proxy, olbridge server. Run setup_proxy() first."""
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    agents = {
        "colima": _agent(LABELS["colima"], ["/opt/homebrew/bin/colima", "start", "overleaf", "--activate=false"], False, STATE / "colima.log"),
        "nginx": _agent(LABELS["nginx"], [NGINX, "-c", str(NGINX_DIR / "nginx.conf"), "-p", str(NGINX_DIR), "-g", "daemon off;"], True, NGINX_DIR / "stdout.log"),
        "serve": _agent(LABELS["serve"], [str(HERE / "olbridge"), "serve"], True, STATE / "server.log"),
    }
    uid = os.getuid()
    for key, plist in agents.items():
        path = AGENTS_DIR / f"{LABELS[key]}.plist"
        with open(path, "wb") as f:
            plistlib.dump(plist, f)
        os.chmod(path, 0o600)
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LABELS[key]}"], capture_output=True)
        subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(path)], check=True)
        print(f"loaded {LABELS[key]}")


def uninstall_agents():
    uid = os.getuid()
    for label in LABELS.values():
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)
        (AGENTS_DIR / f"{label}.plist").unlink(missing_ok=True)
        print(f"removed {label}")
