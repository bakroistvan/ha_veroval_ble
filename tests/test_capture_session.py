"""Tests for capture session wall-clock logger (no Tk)."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "capture_session.py"
_SPEC = importlib.util.spec_from_file_location("capture_session", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_mod = importlib.util.module_from_spec(_SPEC)
sys.modules["capture_session"] = _mod
_SPEC.loader.exec_module(_mod)

CAPTURE_KINDS = _mod.CAPTURE_KINDS
EVENT_HELP = _mod.EVENT_HELP
EVENT_LABELS = _mod.EVENT_LABELS
step_instruction = _mod.step_instruction
step_panel_text = _mod.step_panel_text
iso_utc = _mod.iso_utc
kind_by_id = _mod.kind_by_id
new_session = _mod.new_session
flow_layers = _mod.flow_layers
flow_nodes = _mod.flow_nodes
kind_events = _mod.kind_events
next_choices = _mod.next_choices
next_unused_event = _mod.next_unused_event
session_stem = _mod.session_stem
visible_events = _mod.visible_events
wrap_layer_rows = _mod.wrap_layer_rows
write_session = _mod.write_session


def test_every_flow_node_has_help() -> None:
    for kind in CAPTURE_KINDS:
        for event_id in flow_nodes(kind.flow):
            assert event_id in EVENT_HELP
            text = step_instruction(kind, event_id, host="App", user=2, device="Cuff")
            assert text
            assert "{host}" not in text
            assert "{user}" not in text


def test_kind_overrides_change_step_copy() -> None:
    button = kind_by_id("user_button_only")
    post = kind_by_id("post_measurement")
    assert "Do not take a new reading" in step_instruction(button, "user_button")
    assert "Do not press User 1/2 extra" in step_instruction(post, "measurement_start")
    ha = kind_by_id("ha_window")
    panel = step_panel_text(ha, ("user_button", "measurement_start"), user=2)
    assert "branch" in panel.lower()
    assert "User 2" in panel
    done = step_panel_text(button, ())
    assert "finished" in done.lower()


def test_kind_ids_unique() -> None:
    ids = [k.id for k in CAPTURE_KINDS]
    codes = [k.code for k in CAPTURE_KINDS]
    assert len(ids) == len(set(ids))
    assert len(codes) == len(set(codes))


def test_readme_kinds_exist() -> None:
    for kind_id in (
        "bonded_transfer",
        "fresh_pairing",
        "user_button_only",
        "post_measurement",
        "quality_flags",
        "ha_window",
        "generic_sig_bps",
    ):
        kind = kind_by_id(kind_id)
        assert kind_events(kind)
        for event_id in kind_events(kind):
            assert event_id in EVENT_LABELS


def test_visible_events_include_note_and_phone_clock() -> None:
    kind = kind_by_id("user_button_only")
    visible = visible_events(kind)
    assert visible[-2:] == ("note", "phone_clock")
    assert "user_button" in visible
    assert "measurement_start" not in visible


def test_next_unused_skips_logged() -> None:
    kind = kind_by_id("user_button_only")
    nxt = next_unused_event(kind, ["session_start", "snoop_on"])
    assert nxt == "cuff_asleep"
    assert next_unused_event(kind, ["session_start", *kind_events(kind)]) is None


def test_wrap_layer_rows_two_bands() -> None:
    layers = [(str(i),) for i in range(9)]
    top, bottom = wrap_layer_rows(layers, 2)
    assert len(top) == 5
    assert len(bottom) == 4
    assert [col[0] for col in top + bottom] == [str(i) for i in range(9)]


def test_flow_c_is_linear_user_button() -> None:
    kind = kind_by_id("user_button_only")
    layers = flow_layers(kind.flow)
    assert all(len(layer) == 1 for layer in layers)
    assert "user_button" in flow_nodes(kind.flow)
    assert "measurement_start" not in flow_nodes(kind.flow)


def test_flow_h_branches_then_merges() -> None:
    kind = kind_by_id("ha_window")
    flow = kind.flow
    assert next_choices(flow, ["session_start", "cuff_asleep"]) == (
        "user_button",
        "measurement_start",
    )
    assert next_choices(flow, ["session_start", "cuff_asleep", "user_button"]) == (
        "bluetooth_flash",
    )
    assert next_choices(
        flow, ["session_start", "cuff_asleep", "measurement_start"]
    ) == ("result_on_display",)
    assert "host_app_opened" not in flow_nodes(flow)


def test_session_stamps_and_user_slot() -> None:
    kind = kind_by_id("post_measurement")
    t0 = datetime(2026, 9, 1, 19, 22, 0, tzinfo=timezone.utc)
    session = new_session(
        kind, device_model="BPU26", host_app="medi.connect", user_slot=2, now=t0
    )
    assert session.events[0].event_id == "session_start"
    assert session.events[0].since_session_s == 0.0
    assert session.user_slot == 2
    flash = session.append(
        "bluetooth_flash",
        now=datetime(2026, 9, 1, 19, 22, 40, tzinfo=timezone.utc),
        monotonic_now=session.t0_monotonic + 40.25,
    )
    assert flash.since_session_s == 40.25
    assert iso_utc(t0).startswith("2026-09-01T19:22:00")

    button = session.append("user_button", monotonic_now=session.t0_monotonic + 41)
    assert button.extra["user_slot"] == 2

    stem = session_stem(session, t0)
    assert stem.startswith("D_post_measurement_20260901_")
    with tempfile.TemporaryDirectory() as raw:
        json_path, md_path = write_session(session, Path(raw), stem)
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        assert payload["schema"] == "veroval_capture_session/v1"
        assert payload["kind_code"] == "D"
        assert payload["events"][1]["event_id"] == "bluetooth_flash"
        assert "bluetooth_flash" in md_path.read_text(encoding="utf-8")


def test_undo_last_event() -> None:
    kind = kind_by_id("bonded_transfer")
    session = new_session(
        kind, device_model="X", host_app="app", user_slot=1
    )
    session.append("cuff_asleep")
    removed = session.undo()
    assert removed is not None
    assert removed.event_id == "cuff_asleep"
    assert session.events[-1].event_id == "session_start"
