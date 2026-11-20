"""PipeReader framing/robustness (pure logic; no pipe needed)."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
UI = Path(__file__).resolve().parents[2] / "ui"
sys.path.insert(0, str(UI))


def _load_ui_main():
    spec = importlib.util.spec_from_file_location("mg_ui_main_under_test", UI / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Sig:
    def __init__(self):
        self.states = []
        self.state_changed = self

    def emit(self, state):
        self.states.append(state)


def _reader():
    mod = _load_ui_main()
    sig = _Sig()
    return mod.PipeReader(sig, threading.Event()), sig


def _line(**kw):
    return (json.dumps(kw, ensure_ascii=False) + "\n").encode("utf-8")


def test_two_lines_in_one_chunk_emit_two_states():
    r, sig = _reader()
    r.feed(_line(state="scanning") + _line(state="active"))
    assert [s.name for s in sig.states] == ["scanning", "active"]


def test_line_split_across_chunks_is_reassembled():
    r, sig = _reader()
    data = _line(state="soft_locked", detail="héllo")
    cut = data.index("é".encode()) + 1  # split inside a UTF-8 character
    r.feed(data[:cut])
    assert sig.states == []
    r.feed(data[cut:])
    assert [s.name for s in sig.states] == ["soft_locked"]
    assert sig.states[0].detail == "héllo"


def test_non_object_and_bad_values_do_not_raise_or_emit():
    r, sig = _reader()
    r.feed(b"[1,2,3]\n")
    r.feed(b'"text"\n')
    r.feed(_line(state="scanning", confidence="not-a-number"))
    r.feed(b"{broken\n")
    assert sig.states == []
    r.feed(_line(state="active"))
    assert [s.name for s in sig.states] == ["active"]


def test_thread_stop_name_is_not_shadowed():
    r, _ = _reader()
    # Thread internals own the name ``_stop``; an instance attribute would shadow it.
    assert "_stop" not in vars(r)
    assert isinstance(r._stop_event, threading.Event)
