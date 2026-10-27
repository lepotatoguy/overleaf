"""Stateful service: links, polling, import, sync, conflict queue, activity feed."""

import json
import os
import tempfile
import threading
from datetime import datetime, timezone

from olsync.olclient import SessionExpiredError

import clients
import engine

LINKS_FILE = clients.STATE_DIR / "links.json"
POLL_SECONDS = 15
MAX_EVENTS = 200
EDITING_WINDOW_S = 180


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def who(project):
    user = (project or {}).get("lastUpdatedBy") or {}
    name = " ".join(filter(None, [user.get("firstName"), user.get("lastName")])).strip()
    return name or user.get("email") or "someone"


def seconds_since(iso):
    try:
        then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return (datetime.now(timezone.utc) - then).total_seconds()


class Bridge:
    def __init__(self):
        self.lock = threading.RLock()
        self.poke = threading.Event()
        self.cond = threading.Condition()
        self.events, self.next_event = [], 1
        self.conflicts = {}  # link id -> {path: {"payload", "com", "local"}}
        self.session_ok = {"com": True, "local": True}
        self.last_warning = None
        self.links = self._load_links()

    # ---- persistence -------------------------------------------------
    def _load_links(self):
        try:
            return json.loads(LINKS_FILE.read_text())
        except FileNotFoundError:
            return []

    def _save_links(self):
        fd, tmp = tempfile.mkstemp(dir=LINKS_FILE.parent, prefix="links.", suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(self.links, f, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, LINKS_FILE)

    def _link(self, link_id):
        return next((l for l in self.links if l["id"] == link_id), None)

    # ---- activity feed ----------------------------------------------
    def emit(self, kind, text, link=None):
        with self.cond:
            event = {"id": self.next_event, "ts": now_iso(), "kind": kind, "text": text, "link": link}
            self.next_event += 1
            self.events = (self.events + [event])[-MAX_EVENTS:]
            self.cond.notify_all()

    def events_after(self, last_id, timeout=25):
        with self.cond:
            if not any(e["id"] > last_id for e in self.events):
                self.cond.wait(timeout)
            return [e for e in self.events if e["id"] > last_id]

    # ---- server access ----------------------------------------------
    def _projects(self, side):
        client, _ = clients.make_client(side)
        try:
            projects = {p["id"]: p for p in client.all_projects()}
        except SessionExpiredError:
            self._set_session(side, False)
            return None
        self._set_session(side, True)
        return projects

    def _set_session(self, side, ok):
        if self.session_ok[side] != ok:
            self.session_ok[side] = ok
            if not ok:
                fix = "olbridge login-local" if side == "local" else "ols login --cookie --auth-file .state/auth-com.json"
                self.emit("warn", f"{side} session expired; run `{fix}`")

    # ---- sync ---------------------------------------------------------
    def sync(self, link_id, push=None):
        with self.lock:
            link = self._link(link_id)
            com_client, _ = clients.make_client("com")
            loc_client, _ = clients.make_client("local")
            com = engine.snapshot(com_client, link["com_id"])
            loc = engine.snapshot(loc_client, link["local_id"])
            to_local, to_com, conflict_paths, base = engine.plan(com, loc, link["base"])
            pulled = engine.apply_changes(loc_client, link["local_id"], to_local)
            base = engine.settle(base, to_local, pulled)
            do_push = link["push"] if push is None else push
            pushed = engine.apply_changes(com_client, link["com_id"], to_com) if do_push else set()
            base = engine.settle(base, to_com, pushed)
            self.conflicts[link_id] = {
                p: {"payload": engine.describe(p, com, loc), "com": com.get(p), "local": loc.get(p)}
                for p in conflict_paths
            }
            link.update(base=base, last_sync=now_iso(), ahead=sorted(set(to_com) - pushed))
            self._save_links()
            self._report(link, sorted(pulled), sorted(pushed), conflict_paths)
            return {"pulled": sorted(pulled), "pushed": sorted(pushed), "conflicts": conflict_paths}

    def _report(self, link, pulled, pushed, conflict_paths):
        name = link["name"]
        if pulled:
            self.emit("pull", f"{name}: pulled from overleaf.com: {', '.join(pulled)}", name)
        if pushed:
            self.emit("push", f"{name}: pushed to overleaf.com: {', '.join(pushed)}", name)
        if conflict_paths:
            self.emit("conflict", f"{name}: conflict in {', '.join(conflict_paths)}", name)

    def resolve(self, link_id, path, choice):
        with self.lock:
            link, item = self._link(link_id), self.conflicts.get(link_id, {}).get(path)
            if link is None or item is None or choice not in ("com", "local", "skip"):
                raise ValueError("unknown conflict or choice")
            if choice == "skip":
                return
            target, source = ("local", item["com"]) if choice == "com" else ("com", item["local"])
            client, _ = clients.make_client(target)
            pid = link["local_id" if target == "local" else "com_id"]
            changes = {path: source}
            done = engine.apply_changes(client, pid, changes)
            link["base"] = engine.settle(link["base"], changes, done)
            if done:
                del self.conflicts[link_id][path]
            self._save_links()
            kept = "overleaf.com" if choice == "com" else "local"
            self.emit("info", f"{link['name']}: kept {kept} version of {path}", link["name"])

    # ---- import -------------------------------------------------------
    def import_project(self, com_id):
        with self.lock:
            if any(l["com_id"] == com_id for l in self.links):
                raise ValueError("already linked")
            com_projects = self._projects("com") or {}
            project = com_projects.get(com_id)
            if project is None:
                raise ValueError("project not found on overleaf.com")
            name = project["name"]
            local_projects = self._projects("local") or {}
            existing = next((p for p in local_projects.values() if p["name"] == name), None)
            com_client, _ = clients.make_client("com")
            if existing:
                local_id, base = existing["id"], {}
            else:
                zip_bytes = com_client.download_project(com_id)
                _, local_auth = clients.make_client("local")
                local_id = clients.upload_zip_as_project(local_auth, name, zip_bytes)
                base = {p: engine.norm_hash(d) for p, d in engine.snapshot(com_client, com_id).items()}
            self.links = self.links + [
                {"id": com_id, "name": name, "com_id": com_id, "local_id": local_id, "base": base,
                 "push": False, "stamp": None, "last_sync": None, "ahead": []}
            ]
            self._save_links()
            self.emit("info", f"{name}: imported from overleaf.com", name)
            return self.sync(com_id)

    def set_push(self, link_id, enabled):
        with self.lock:
            self._link(link_id)["push"] = bool(enabled)
            self._save_links()

    # ---- polling ------------------------------------------------------
    def tick(self):
        com_projects, loc_projects = self._projects("com"), self._projects("local")
        if com_projects is None or loc_projects is None:
            return
        for link in list(self.links):
            cp, lp = com_projects.get(link["com_id"]), loc_projects.get(link["local_id"])
            if cp is None or lp is None:
                continue
            stamp = [cp.get("lastUpdated"), lp.get("lastUpdated")]
            if stamp == link.get("stamp") and not self.conflicts.get(link["id"]):
                continue
            if stamp[0] != (link.get("stamp") or [None])[0]:
                self._announce_remote_edit(link, cp)
            self.sync(link["id"])
            link["stamp"] = stamp
            self._save_links()

    def _announce_remote_edit(self, link, project):
        age = seconds_since(project.get("lastUpdated"))
        if link.get("stamp") is not None and age is not None and age < EDITING_WINDOW_S:
            self.emit("edit", f"{who(project)} is editing {link['name']} on overleaf.com", link["name"])

    def run_forever(self):
        while True:
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - the poller must survive any one bad cycle
                message = f"sync error: {type(exc).__name__}"
                if message != self.last_warning:
                    self.last_warning = message
                    self.emit("warn", message)
            else:
                self.last_warning = None
            self.poke.wait(POLL_SECONDS)
            self.poke.clear()

    # ---- in-page banner ----------------------------------------------
    def link_id_for_local(self, local_id):
        link = next((l for l in self.links if l["local_id"] == local_id), None)
        if link is None:
            raise ValueError("project is not linked")
        return link["id"]

    def page(self, local_id):
        """What the in-page script needs: is this project linked, and which conflicts wait (any project)."""
        current = next((l for l in self.links if l["local_id"] == local_id), None)
        pending = [
            {"link": l["id"], "project": l["name"], **item["payload"]}
            for l in self.links for item in self.conflicts.get(l["id"], {}).values()
        ]
        return {
            "linked": bool(current),
            "name": current["name"] if current else None,
            "ahead": current["ahead"] if current else [],
            "conflicts": pending,
        }

    # ---- UI state -----------------------------------------------------
    def state(self):
        com_projects = self._projects("com") or {}
        linked = {l["com_id"] for l in self.links}
        return {
            "session": dict(self.session_ok),
            "links": [
                {k: l[k] for k in ("id", "name", "push", "last_sync", "ahead")}
                | {"conflicts": [i["payload"] for i in self.conflicts.get(l["id"], {}).values()]}
                for l in self.links
            ],
            "importable": sorted(
                ({"id": p["id"], "name": p["name"], "lastUpdated": p.get("lastUpdated"), "by": who(p)}
                 for p in com_projects.values() if p["id"] not in linked),
                key=lambda p: p["lastUpdated"] or "", reverse=True,
            ),
        }
