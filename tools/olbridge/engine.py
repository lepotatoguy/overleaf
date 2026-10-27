"""Three-way sync between an overleaf.com project and its twin on the local server.

State per link: `base`, the content hash of every file at the last agreed sync.
A file changed on only one side is carried over; changed on both sides (differently)
is a conflict that waits for a human decision. Nothing is ever overwritten silently.
"""

import hashlib
import io
import zipfile


def norm_hash(data):
    """Hash with CRLF folded, since Overleaf may normalise line endings of text files."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def snapshot(client, project_id):
    """All files of a project as {path: bytes}."""
    archive = zipfile.ZipFile(io.BytesIO(client.download_project(project_id)))
    return {i.filename: archive.read(i) for i in archive.infolist() if not i.is_dir()}


def plan(com, loc, base):
    """
    Decide per file what to do.
    Returns (to_local, to_com, conflicts, new_base); to_* map path -> bytes (None = delete).
    """
    digest = lambda files, path: norm_hash(files[path]) if path in files else None
    to_local, to_com, conflicts, new_base = {}, {}, [], dict(base)
    for path in sorted(set(com) | set(loc) | set(base)):
        c, l, b = digest(com, path), digest(loc, path), base.get(path)
        if c == l:
            if c is None:
                new_base.pop(path, None)
            else:
                new_base[path] = c
        elif l == b:
            to_local[path] = com.get(path)
        elif c == b:
            to_com[path] = loc.get(path)
        else:
            conflicts.append(path)
    return to_local, to_com, conflicts, new_base


def apply_changes(client, project_id, changes):
    """Upload or delete files on one server. Returns the set of paths that succeeded."""
    if not changes:
        return set()
    infos = client.get_project_infos(project_id)
    done = set()
    for path, data in changes.items():
        if data is None:
            ok = client.delete_file(project_id, infos, path)
        else:
            ok = client.upload_file(project_id, infos, path, len(data), io.BytesIO(data))
        if ok:
            done.add(path)
    return done


def settle(base, changes, done):
    """New base after `done` paths of `changes` were applied."""
    out = dict(base)
    for path in done:
        data = changes[path]
        if data is None:
            out.pop(path, None)
        else:
            out[path] = norm_hash(data)
    return out


def describe(path, com, loc):
    """Conflict payload for the UI: text for text files, sizes only for binaries."""
    def side(files):
        data = files.get(path)
        if data is None:
            return {"missing": True}
        try:
            return {"text": data.decode("utf-8"), "size": len(data)}
        except UnicodeDecodeError:
            return {"binary": True, "size": len(data)}
    return {"path": path, "com": side(com), "local": side(loc)}
