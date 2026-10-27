import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import norm_hash, plan, settle  # noqa: E402


def base_of(files):
    return {p: norm_hash(d) for p, d in files.items()}


def test_identical_sides_need_nothing():
    files = {"a.tex": b"x"}
    to_local, to_com, conflicts, base = plan(files, files, {})
    assert (to_local, to_com, conflicts) == ({}, {}, [])
    assert base == base_of(files)


def test_remote_only_change_goes_to_local():
    old = {"a.tex": b"old"}
    to_local, to_com, conflicts, _ = plan({"a.tex": b"new"}, old, base_of(old))
    assert to_local == {"a.tex": b"new"} and not to_com and not conflicts


def test_local_only_change_goes_to_com():
    old = {"a.tex": b"old"}
    to_local, to_com, conflicts, _ = plan(old, {"a.tex": b"mine"}, base_of(old))
    assert to_com == {"a.tex": b"mine"} and not to_local and not conflicts


def test_both_changed_is_a_conflict_and_nothing_moves():
    old = {"a.tex": b"old"}
    to_local, to_com, conflicts, base = plan({"a.tex": b"theirs"}, {"a.tex": b"mine"}, base_of(old))
    assert conflicts == ["a.tex"] and not to_local and not to_com
    assert base == base_of(old)


def test_remote_delete_propagates_when_local_untouched():
    old = {"a.tex": b"x"}
    to_local, _, conflicts, _ = plan({}, old, base_of(old))
    assert to_local == {"a.tex": None} and not conflicts


def test_delete_vs_edit_is_a_conflict():
    old = {"a.tex": b"x"}
    _, _, conflicts, _ = plan({}, {"a.tex": b"edited"}, base_of(old))
    assert conflicts == ["a.tex"]


def test_crlf_difference_is_not_a_change():
    to_local, to_com, conflicts, _ = plan({"a.tex": b"a\r\nb"}, {"a.tex": b"a\nb"}, {})
    assert not (to_local or to_com or conflicts)


def test_settle_records_only_successful_paths():
    out = settle({}, {"a": b"1", "b": b"2"}, {"a"})
    assert out == {"a": norm_hash(b"1")}
