# olbridge

Keeps a self-hosted Overleaf Community Edition in sync with projects on overleaf.com. It runs next to Overleaf as a small local service. It does not modify Overleaf's source code or its Docker image.

Status: personal tool, tested on Overleaf CE 6.3.2 (Toolkit, x86 image under Rosetta on Apple Silicon, macOS). Only the sync engine has unit tests (`tests/`). The rest was tested by hand, including the in-page banner and conflict popup in a browser (pull-on-open banner, live "pulled" banner with Reload, conflict popup and "Keep overleaf.com" resolution).

## What it does

| Feature | How |
| --- | --- |
| Import from overleaf.com | Dashboard lists your overleaf.com projects. Import downloads the zip and creates the project on the local server through `/project/new/upload`. A same-named local project is linked instead. |
| Auto-pull | A poller reads both dashboards every 15 s (`POLL_SECONDS`). When a project's `lastUpdated` changes it runs a three-way sync. overleaf.com changes land locally. |
| Live activity | The dashboard shows who last edited (from `lastUpdatedBy`) and what was pulled. Polling only: overleaf.com offers no public push feed. |
| Conflicts | A file changed on both sides since the last agreed sync is never overwritten. A popup shows both versions: Keep overleaf.com, Keep local, or Decide later. |
| Local edits to overleaf.com | Off by default. Enable "auto-push" per project, or use "Sync now (both ways)". |
| In-page banner and popup | An nginx reverse proxy injects `inject.js` into every local Overleaf page. It triggers a pull when a project opens and shows the conflict popup inside Overleaf. |
| Autostart | `install-agents` writes three launchd agents (Colima VM, nginx, the service). |

## How it works

`engine.py` is the core. For every file it compares three hashes: overleaf.com, local, and `base` (the hash at the last agreed sync).

- Only one side differs from `base`: carry that change to the other side.
- Both differ, and differ from each other: conflict, nothing moves.
- Deletions follow the same rules (delete versus edit is a conflict).
- Line endings are normalised (CRLF to LF) before hashing.

Server access reuses the client from [overleaf-sync](https://github.com/moritzgloeckl/overleaf-sync) (`olsync`): dashboard listing, zip download, upload, delete.

## Privacy model

- Logins (session cookies), the API token, and sync state live only under the state directory (default `~/overleaf-mirror/.state`, override with `OLBRIDGE_STATE`), mode 700 and 600. No password is ever stored. `login-local` reads it at a hidden prompt and keeps only the session cookie.
- The service binds `127.0.0.1` only, rejects any other `Host`, and checks `Origin` on writes.
- You reach the dashboard with `olbridge open`: a one-time code is swapped for an HttpOnly, SameSite=Strict cookie, so the token never appears in a URL or in browser history.
- Request URLs are not logged (nginx `access_log off`, handler logging disabled). No file contents or cookies are logged.
- Through the Overleaf-facing proxy only five paths are reachable (`inject.js`, `api/events`, `api/page`, `api/pull`, `api/resolve`). The token is added by nginx, so it never reaches the browser. Writes need a custom header (forces a CORS preflight that is never answered) and a matching `Origin`.
- Trade-off: the proxy replaces Overleaf's `Content-Security-Policy` header, because the original (nonce plus `strict-dynamic`) blocks injected scripts. The replacement allows same-origin and inline scripts. Use only on a trusted, local-only setup.
- Conflict text is held in memory only, never written to disk.

## Setup (macOS)

Prerequisites: a running Overleaf Toolkit instance, Python with `overleaf-sync` installed, `brew install nginx`.

1. Overleaf listens on an internal port and nginx takes the public one. In `config/overleaf.rc` of the Toolkit set `OVERLEAF_PORT=8795` (any free port; the proxy template assumes 8795 and public port 8788), keep `OVERLEAF_SITE_URL=http://localhost:8788`, then `bin/up -d`.
2. Log in to both servers and place the session files in the state directory as `auth-com.json` and `auth-local.json`. `ols login --cookie --auth-file ...` writes the right format. For the local server, `./olbridge login-local EMAIL` does it.
3. `./olbridge setup-proxy` renders and validates the nginx config (it embeds the token, mode 600).
4. `./olbridge install-agents` starts everything at login. `./olbridge uninstall-agents` removes it.
5. `./olbridge open` opens the dashboard.

Environment: `OLBRIDGE_PYTHON` (interpreter with `olsync`), `OLBRIDGE_STATE`, `OLBRIDGE_LOCAL_EMAIL`.

## Known limits

- Polling, not push: expect up to about 15 s of delay.
- Sessions expire. The dashboard shows "signed out"; log in again.
- The "who is editing" message appears only for edits in the last three minutes and shows the last editor only.
- Not verified: behaviour with very large projects, and the "Keep local" and "Decide later" popup buttons (only "Keep overleaf.com" was exercised in the browser).
- Overleaf under emulation on a 4 GB VM is heavy. Expect high CPU while it boots.

## Tests

```
python -m pytest tests
```

Covers the three-way decision logic in `engine.py`.
