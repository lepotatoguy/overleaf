"""olbridge command line: serve | open | login-local."""

import json
import os
import subprocess
import sys
import urllib.request

import clients
import server

BASE = f"http://127.0.0.1:{server.PORT}"
LOCAL_EMAIL = os.environ.get("OLBRIDGE_LOCAL_EMAIL", "")


def open_dashboard():
    """Ask the running server for a one-time code; the browser swaps it for a private cookie."""
    token = server.TOKEN_FILE.read_text().strip()
    request = urllib.request.Request(
        BASE + "/api/nonce", data=b"{}", headers={"X-OLB-Token": token, "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=10) as reply:
        nonce = json.load(reply)["nonce"]
    subprocess.run(["open", f"{BASE}/?n={nonce}"], check=True)


def main(argv):
    command = argv[1] if len(argv) > 1 else "serve"
    if command == "serve":
        import bridge
        server.serve(bridge.Bridge())
    elif command == "open":
        open_dashboard()
    elif command == "login-local":
        email = argv[2] if len(argv) > 2 else LOCAL_EMAIL
        if not email:
            raise SystemExit("usage: olbridge login-local EMAIL   (or set OLBRIDGE_LOCAL_EMAIL)")
        clients.login_local(email)
    elif command in ("setup-proxy", "install-agents", "uninstall-agents"):
        import deploy
        {"setup-proxy": deploy.setup_proxy, "install-agents": deploy.install_agents,
         "uninstall-agents": deploy.uninstall_agents}[command]()
    else:
        raise SystemExit("usage: olbridge [serve | open | login-local [email] | setup-proxy | install-agents | uninstall-agents]")


if __name__ == "__main__":
    main(sys.argv)
