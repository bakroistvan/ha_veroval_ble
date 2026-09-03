#!/usr/bin/env python3
"""Wall-clock logger for BLE capture sessions (issue #29 timing, reusable).

The window draws a **two-row left-to-right flowchart** for the selected capture kind.
Every step stays visible; only the current step (or branch) is enabled.

    python scripts/capture_session.py
"""

from __future__ import annotations

import json
import time
import tkinter as tk
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SESSIONS_DIR = ROOT / "docs" / "captures" / "sessions"
SCHEMA = "veroval_capture_session/v1"
START = "_start"

# Stable event ids — keep these even if button labels change.
EVENT_LABELS: dict[str, str] = {
    "session_start": "Start session",
    "snoop_on": "HCI snoop on",
    "cuff_asleep": "Cuff asleep",
    "unpair": "Forgot / unpaired",
    "user_button": "User button pressed",
    "measurement_start": "Measurement started",
    "result_on_display": "Result on display",
    "bluetooth_flash": "Bluetooth flashing",
    "host_app_opened": "Host app opened",
    "pin_shown": "PIN shown on cuff",
    "pin_entered": "PIN entered on host",
    "transfer_done": "Transfer finished",
    "force_dump": "Force dump started",
    "ha_no_connect": "HA did not connect",
    "ha_sensors_updated": "HA sensors updated",
    "window_end": "Window ended / display off",
    "bugreport_pull": "Pull bugreport / log",
    "note": "Note",
    "phone_clock": "Phone clock",
}

ALWAYS_EVENTS = ("note", "phone_clock")
Flow = dict[str, tuple[str, ...]]

# What to do, then click the matching box. {host} {device} {user} {file} are filled in.
EVENT_HELP: dict[str, str] = {
    "session_start": (
        "Confirm Kind, User, Device, and Host app above. Click this box to stamp T=0 "
        "and start the session file."
    ),
    "snoop_on": (
        "On the phone: Developer options → enable Bluetooth HCI snoop log. Toggle "
        "Bluetooth off/on if the OEM requires it. Leave snoop on. Click when it is on."
    ),
    "cuff_asleep": (
        "Wait until {device} is fully asleep: display off, Bluetooth not flashing. "
        "Click when it is asleep."
    ),
    "unpair": (
        "Forget/unpair {device} in the phone Bluetooth settings and remove it from "
        "{host}. Click when it is unpaired."
    ),
    "user_button": (
        "Do not inflate. Press User {user} once on the cuff. Click at the moment you press it."
    ),
    "measurement_start": (
        "Put the cuff on User {user} and start a blood pressure measurement. "
        "Click when inflation starts."
    ),
    "result_on_display": (
        "Wait until systolic / diastolic / pulse are on the cuff display. "
        "Click when the result is shown."
    ),
    "bluetooth_flash": (
        "Wait until the Bluetooth symbol flashes on the cuff. Click at the first flash."
    ),
    "host_app_opened": (
        "Open {host} while the symbol is still flashing. Click when you open the app."
    ),
    "pin_shown": (
        "Watch the cuff for the 6-digit PIN (first pairing). Click when the PIN appears."
    ),
    "pin_entered": (
        "Enter the PIN on the phone / OS prompt. Click when you submit it."
    ),
    "transfer_done": (
        "Wait until {host} finishes the dump (history transferred). Click when it is done."
    ),
    "force_dump": (
        "In Home Assistant, run Force data sync (veroval_ble.force_dump) for this device. "
        "Click when you start it."
    ),
    "ha_no_connect": (
        "Watch Home Assistant sensors (and bluetoothctl if you have it). Click if the cuff "
        "is flashing and HA does not connect by itself."
    ),
    "ha_sensors_updated": (
        "Click when HA systolic / diastolic / pulse or last-synchronized updates."
    ),
    "window_end": (
        "Click when the advertise window ends: display off or Bluetooth flash stops."
    ),
    "bugreport_pull": (
        "Keep USB connected. Pull the snoop log (suggested file `{file}`). On Samsung, "
        "`adb pull` is often denied — use `adb bugreport` and extract "
        "FS/data/log/bt/btsnoop_hci.log. Click when you start the pull."
    ),
    "note": "Type a free-text observation (quality flags, display text, HA log line).",
    "phone_clock": (
        "Read the time on the phone screen (HCI uses the phone clock). Click and type HH:MM:SS."
    ),
}

# Kind-specific wording when the same event means a different action.
KIND_STEP_NOTES: dict[str, dict[str, str]] = {
    "user_button_only": {
        "user_button": (
            "Do not take a new reading. Press User {user} only. Click at the press."
        ),
        "bluetooth_flash": (
            "The symbol should flash from the User press, not from a new measurement. "
            "Click at the first flash."
        ),
    },
    "post_measurement": {
        "measurement_start": (
            "Take a new reading on User {user}. Do not press User 1/2 extra afterward. "
            "Click when inflation starts."
        ),
        "bluetooth_flash": (
            "Do not press User. Wait for the cuff to turn Bluetooth on by itself after "
            "the result. Click at the first auto-flash. Wait ~15 s before opening {host} "
            "if you need more advertisement packets."
        ),
    },
    "quality_flags": {
        "result_on_display": (
            "Note whether this was a clean or messy reading (talk, move, loose cuff). "
            "Use the Note button for rest / AFib / fit. Click when the result is shown."
        ),
    },
    "ha_window": {
        "user_button": (
            "C-style wake: press User {user} only (no new reading). Keep {host} closed. "
            "Click at the press."
        ),
        "measurement_start": (
            "D-style wake: take a new reading on User {user}. Do not press User extra. "
            "Keep {host} closed. Click when inflation starts."
        ),
        "bluetooth_flash": (
            "Keep {host} closed. Optional: phone Bluetooth off so only HA scans. "
            "Click at the first flash."
        ),
        "ha_no_connect": (
            "This is the miss we are timing. Click if HA does not connect while the "
            "symbol is flashing."
        ),
    },
    "generic_sig_bps": {
        "unpair": (
            "Only if this is a fresh pairing. Skip this box if already bonded — choose "
            "User button or Measurement instead."
        ),
        "host_app_opened": (
            "Open {host}. If the cuff shows a PIN, take the PIN branch next; otherwise "
            "go to Transfer finished."
        ),
        "pin_shown": (
            "Only if the cuff shows a passkey. Click when it appears. Skip if already bonded."
        ),
        "force_dump": (
            "Optional: only if you are testing a Home Assistant / host dump besides {host}."
        ),
    },
}


def step_instruction(
    kind: CaptureKind,
    event_id: str,
    *,
    host: str = "medi.connect",
    user: int | str = 1,
    device: str = "BPU26",
) -> str:
    """Return the action text for one flowchart box."""
    table = KIND_STEP_NOTES.get(kind.id, {})
    raw = table.get(event_id) or EVENT_HELP[event_id]
    return raw.format(
        host=(host or "the host app").strip() or "the host app",
        user=user,
        device=(device or "the cuff").strip() or "the cuff",
        file=kind.save_as,
    )


def step_panel_text(
    kind: CaptureKind,
    choices: Sequence[str],
    *,
    host: str = "medi.connect",
    user: int | str = 1,
    device: str = "BPU26",
) -> str:
    """Copy for the 'What to do now' panel."""
    if not choices:
        return (
            "This path is finished.\n\n"
            "Use Phone clock if you have not yet (HCI timestamps follow the phone). "
            f"Then pull the capture if this kind needs a file (`{kind.save_as}`), "
            "or End session."
        )
    blocks: list[str] = []
    for event_id in choices:
        title = EVENT_LABELS[event_id]
        body = step_instruction(kind, event_id, host=host, user=user, device=device)
        blocks.append(f"{title}\n{body}\n\nClick “{title}” when that has happened.")
    if len(blocks) > 1:
        return "This step is a branch — pick the path that matches what you are doing.\n\n" + "\n\n".join(
            blocks
        )
    return blocks[0]


def _linear(*steps: str) -> Flow:
    """session_start → steps[0] → … → terminal."""
    flow: Flow = {START: ("session_start",)}
    chain = ("session_start", *steps)
    for src, dst in zip(chain, chain[1:]):
        flow[src] = (dst,)
    flow[chain[-1]] = ()
    return flow


def _merge(base: Flow, **overrides: tuple[str, ...]) -> Flow:
    flow = dict(base)
    flow.update(overrides)
    return flow


BONDED_DUMP = (
    "snoop_on",
    "cuff_asleep",
    "measurement_start",
    "result_on_display",
    "bluetooth_flash",
    "host_app_opened",
    "transfer_done",
    "bugreport_pull",
)


@dataclass(frozen=True)
class CaptureKind:
    """One capture procedure (Veroval A–D or a generic SIG BPS variant)."""

    id: str
    code: str
    title: str
    save_as: str
    blurb: str
    flow: Mapping[str, tuple[str, ...]]


CAPTURE_KINDS: tuple[CaptureKind, ...] = (
    CaptureKind(
        id="bonded_transfer",
        code="A",
        title="A — Bonded transfer (after measurement)",
        save_as="bonded_transfer.btsnoop",
        blurb=(
            "Cuff already paired. Measure, wait for the Bluetooth flash, open the "
            "host app, let the dump finish."
        ),
        flow=_linear(*BONDED_DUMP),
    ),
    CaptureKind(
        id="fresh_pairing",
        code="B",
        title="B — Fresh pairing + GATT dump",
        save_as="fresh_pairing.btsnoop",
        blurb=(
            "Unpair on the phone (and in the host app). Measure, open the app, "
            "enter the 6-digit PIN, let pairing and dump finish."
        ),
        flow=_linear(
            "snoop_on",
            "unpair",
            "cuff_asleep",
            "measurement_start",
            "result_on_display",
            "bluetooth_flash",
            "host_app_opened",
            "pin_shown",
            "pin_entered",
            "transfer_done",
            "bugreport_pull",
        ),
    ),
    CaptureKind(
        id="user_button_only",
        code="C",
        title="C — User button only (no new reading)",
        save_as="user_button_only.btsnoop",
        blurb=(
            "Cuff asleep. Do not inflate. Press User 1/2, wait for flash, open the "
            "host app."
        ),
        flow=_linear(
            "snoop_on",
            "cuff_asleep",
            "user_button",
            "bluetooth_flash",
            "host_app_opened",
            "transfer_done",
            "bugreport_pull",
        ),
    ),
    CaptureKind(
        id="post_measurement",
        code="D",
        title="D — Post-measurement auto Bluetooth",
        save_as="post_measurement.btsnoop",
        blurb=(
            "Cuff asleep. Take a new reading. Do not press User extra. Wait for "
            "auto Bluetooth, then open the host app."
        ),
        flow=_linear(*BONDED_DUMP),
    ),
    CaptureKind(
        id="quality_flags",
        code="Q",
        title="Q — Quality-flag bonded dump",
        save_as="quality_flags.btsnoop",
        blurb=(
            "Same path as A. Use Note for rest / AFib / loose cuff vs a clean reading."
        ),
        flow=_linear(*BONDED_DUMP),
    ),
    CaptureKind(
        id="ha_window",
        code="H",
        title="H — HA window (host app closed)",
        save_as="(HA Core log + bluetoothctl; optional snoop)",
        blurb=(
            "Home Assistant miss: host app stays closed. After the cuff is asleep, "
            "choose User-button (C-style) or a new measurement (D-style)."
        ),
        flow=_merge(
            {
                START: ("session_start",),
                "session_start": ("cuff_asleep",),
                "cuff_asleep": ("user_button", "measurement_start"),
                "user_button": ("bluetooth_flash",),
                "measurement_start": ("result_on_display",),
                "result_on_display": ("bluetooth_flash",),
                "bluetooth_flash": ("ha_no_connect", "ha_sensors_updated"),
                "ha_no_connect": ("force_dump",),
                "force_dump": ("ha_sensors_updated", "window_end"),
                "ha_sensors_updated": ("window_end",),
                "window_end": (),
            }
        ),
    ),
    CaptureKind(
        id="generic_sig_bps",
        code="G",
        title="G — Generic SIG Blood Pressure (0x1810)",
        save_as="generic_sig_bps.btsnoop",
        blurb=(
            "Same protocol family, different cuff/app. Branches cover unpair, "
            "button vs measurement, optional PIN, optional force dump."
        ),
        flow={
            START: ("session_start",),
            "session_start": ("snoop_on",),
            "snoop_on": ("cuff_asleep",),
            "cuff_asleep": ("unpair", "user_button", "measurement_start"),
            "unpair": ("user_button", "measurement_start"),
            "user_button": ("bluetooth_flash",),
            "measurement_start": ("result_on_display",),
            "result_on_display": ("bluetooth_flash",),
            "bluetooth_flash": ("host_app_opened",),
            "host_app_opened": ("pin_shown", "transfer_done"),
            "pin_shown": ("pin_entered",),
            "pin_entered": ("transfer_done",),
            "transfer_done": ("force_dump", "bugreport_pull"),
            "force_dump": ("bugreport_pull",),
            "bugreport_pull": (),
        },
    ),
)


def kind_by_id(kind_id: str) -> CaptureKind:
    for kind in CAPTURE_KINDS:
        if kind.id == kind_id:
            return kind
    raise KeyError(kind_id)


def flow_nodes(flow: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:
    """All event ids in the graph, start-node first, then remaining."""
    ordered: list[str] = []
    seen: set[str] = set()

    def add(event_id: str) -> None:
        if event_id == START or event_id in seen:
            return
        seen.add(event_id)
        ordered.append(event_id)

    for event_id in flow.get(START, ()):
        add(event_id)
    for src, dests in flow.items():
        add(src)
        for event_id in dests:
            add(event_id)
    return tuple(ordered)


def flow_layers(flow: Mapping[str, tuple[str, ...]]) -> list[tuple[str, ...]]:
    """Rank nodes by longest path from session start (diamond merges)."""
    nodes = flow_nodes(flow)
    preds: dict[str, list[str]] = {event_id: [] for event_id in nodes}
    for src, dests in flow.items():
        if src == START:
            continue
        for dest in dests:
            if src not in preds[dest]:
                preds[dest].append(src)
    rank: dict[str, int] = {event_id: 0 for event_id in flow.get(START, ())}
    for _ in range(len(nodes) + 1):
        for event_id in nodes:
            parents = [p for p in preds[event_id] if p in rank]
            if parents:
                rank[event_id] = max(rank[p] + 1 for p in parents)
    if not rank:
        return []
    depth = max(rank.values())
    layers: list[tuple[str, ...]] = []
    for level in range(depth + 1):
        layer = tuple(event_id for event_id in nodes if rank.get(event_id) == level)
        if layer:
            layers.append(layer)
    return layers


FLOW_WRAP_ROWS = 2


def wrap_layer_rows(
    layers: Sequence[tuple[str, ...]],
    row_count: int = FLOW_WRAP_ROWS,
) -> list[list[tuple[str, ...]]]:
    """Split rank-columns into *row_count* left-to-right bands (default two)."""
    if row_count < 1:
        raise ValueError("row_count must be >= 1")
    if not layers:
        return [[] for _ in range(row_count)]
    rows: list[list[tuple[str, ...]]] = []
    start = 0
    remaining_rows = row_count
    remaining = len(layers)
    for _ in range(row_count):
        take = (remaining + remaining_rows - 1) // remaining_rows
        rows.append(list(layers[start : start + take]))
        start += take
        remaining -= take
        remaining_rows -= 1
    return rows


def last_flow_event(flow: Mapping[str, tuple[str, ...]], logged_ids: Sequence[str]) -> str:
    known = set(flow_nodes(flow))
    last = START
    for event_id in logged_ids:
        if event_id in known:
            last = event_id
    return last


def next_choices(flow: Mapping[str, tuple[str, ...]], logged_ids: Sequence[str]) -> tuple[str, ...]:
    last = last_flow_event(flow, logged_ids)
    if last == START:
        return tuple(flow.get(START, ()))
    return tuple(flow.get(last, ()))


def kind_events(kind: CaptureKind) -> tuple[str, ...]:
    return tuple(eid for eid in flow_nodes(kind.flow) if eid != "session_start")


def visible_events(kind: CaptureKind) -> tuple[str, ...]:
    seen: list[str] = []
    for event_id in (*kind_events(kind), *ALWAYS_EVENTS):
        if event_id not in seen:
            seen.append(event_id)
    return tuple(seen)


def next_unused_event(kind: CaptureKind, logged_ids: Sequence[str]) -> str | None:
    choices = next_choices(kind.flow, logged_ids)
    return choices[0] if choices else None


def iso_local(moment: datetime) -> str:
    """RFC 3339 local time with milliseconds and offset."""
    return moment.astimezone().isoformat(timespec="milliseconds")


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def local_tz_name() -> str:
    return datetime.now().astimezone().tzname() or ""


@dataclass
class CaptureEvent:
    seq: int
    event_id: str
    label: str
    pc_local: str
    pc_utc: str
    since_session_s: float
    note: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CaptureSession:
    kind_id: str
    kind_code: str
    kind_title: str
    save_as: str
    device_model: str
    host_app: str
    user_slot: int
    started_at_local: str
    started_at_utc: str
    timezone: str
    protocol: str = "sig-bps-1810"
    events: list[CaptureEvent] = field(default_factory=list)
    schema: str = SCHEMA
    t0_monotonic: float = field(default=0.0, repr=False)

    def append(
        self,
        event_id: str,
        *,
        note: str = "",
        extra: dict | None = None,
        now: datetime | None = None,
        monotonic_now: float | None = None,
    ) -> CaptureEvent:
        if event_id not in EVENT_LABELS:
            raise KeyError(event_id)
        moment = now or datetime.now(timezone.utc)
        mono = time.monotonic() if monotonic_now is None else monotonic_now
        if not self.events:
            self.t0_monotonic = mono
            since = 0.0
        else:
            since = round(mono - self.t0_monotonic, 3)
        payload = dict(extra or {})
        if event_id == "user_button":
            payload.setdefault("user_slot", self.user_slot)
        event = CaptureEvent(
            seq=len(self.events) + 1,
            event_id=event_id,
            label=EVENT_LABELS[event_id],
            pc_local=iso_local(moment),
            pc_utc=iso_utc(moment),
            since_session_s=since,
            note=note,
            extra=payload,
        )
        self.events.append(event)
        return event

    def undo(self) -> CaptureEvent | None:
        if not self.events:
            return None
        return self.events.pop()

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "protocol": self.protocol,
            "kind_id": self.kind_id,
            "kind_code": self.kind_code,
            "kind_title": self.kind_title,
            "suggested_capture_file": self.save_as,
            "device_model": self.device_model,
            "host_app": self.host_app,
            "user_slot": self.user_slot,
            "timezone": self.timezone,
            "started_at_local": self.started_at_local,
            "started_at_utc": self.started_at_utc,
            "events": [event.to_dict() for event in self.events],
        }

    def to_markdown(self) -> str:
        lines = [
            f"# Capture session {self.kind_code}",
            "",
            f"- Kind: {self.kind_title}",
            f"- Suggested file: `{self.save_as}`",
            f"- Device: {self.device_model}",
            f"- Host app: {self.host_app}",
            f"- User slot: {self.user_slot}",
            f"- Timezone: {self.timezone}",
            f"- Started (local): {self.started_at_local}",
            f"- Started (UTC): {self.started_at_utc}",
            "",
            "| seq | Δs | event | local | UTC | note |",
            "|-----|---:|-------|-------|-----|------|",
        ]
        for event in self.events:
            note = (event.note or "").replace("|", "/")
            extra = ""
            if event.extra:
                extra = " " + json.dumps(event.extra, ensure_ascii=True)
            lines.append(
                f"| {event.seq} | {event.since_session_s:.3f} | `{event.event_id}` "
                f"| {event.pc_local} | {event.pc_utc} | {note}{extra} |"
            )
        lines.extend(
            [
                "",
                "Cross-correlate: PC local/UTC vs Wireshark `frame.time` (often the "
                "**phone** clock in HCI snoop). Use a Phone-clock event to measure offset.",
                "",
            ]
        )
        return "\n".join(lines)


def new_session(
    kind: CaptureKind,
    *,
    device_model: str,
    host_app: str,
    user_slot: int,
    now: datetime | None = None,
) -> CaptureSession:
    moment = now or datetime.now(timezone.utc)
    session = CaptureSession(
        kind_id=kind.id,
        kind_code=kind.code,
        kind_title=kind.title,
        save_as=kind.save_as,
        device_model=device_model.strip() or "BPU26",
        host_app=host_app.strip() or "medi.connect",
        user_slot=user_slot,
        started_at_local=iso_local(moment),
        started_at_utc=iso_utc(moment),
        timezone=local_tz_name(),
    )
    session.append("session_start", now=moment, monotonic_now=time.monotonic())
    return session


def session_stem(session: CaptureSession, started: datetime) -> str:
    stamp = started.astimezone().strftime("%Y%m%d_%H%M%S")
    return f"{session.kind_code}_{session.kind_id}_{stamp}"


def write_session(session: CaptureSession, directory: Path, stem: str) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{stem}.json"
    md_path = directory / f"{stem}.md"
    json_path.write_text(
        json.dumps(session.to_dict(), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(session.to_markdown(), encoding="utf-8")
    return json_path, md_path


class CaptureSessionApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Capture session timer")
        self.minsize(960, 520)
        self._session: CaptureSession | None = None
        self._stem: str | None = None
        self._buttons: dict[str, ttk.Button] = {}
        self._flow_window: int | None = None

        self._kind_id = tk.StringVar(value=CAPTURE_KINDS[0].id)
        self._user_slot = tk.IntVar(value=1)
        self._device = tk.StringVar(value="BPU26")
        self._host = tk.StringVar(value="medi.connect")

        self._build()
        self._host.trace_add("write", lambda *_: self._update_step_panel())
        self._device.trace_add("write", lambda *_: self._update_step_panel())
        self._user_slot.trace_add("write", lambda *_: self._update_step_panel())
        self._refresh_kind()

    def _build(self) -> None:
        style = ttk.Style(self)
        style.configure("Next.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("Done.TButton", foreground="#1a7f37")
        style.configure("Wait.TButton")
        style.configure("Arrow.TLabel", font=("Segoe UI", 14), foreground="#555")

        pad = {"padx": 10, "pady": 4}

        meta = ttk.Frame(self)
        meta.pack(fill=tk.X, **pad)
        ttk.Label(meta, text="Kind").pack(side=tk.LEFT)
        kind_values = [f"{k.code}  {k.title.split('—', 1)[-1].strip()}" for k in CAPTURE_KINDS]
        self._kind_combo = ttk.Combobox(
            meta, state="readonly", width=42, values=kind_values
        )
        self._kind_combo.current(0)
        self._kind_combo.pack(side=tk.LEFT, padx=(8, 12))
        self._kind_combo.bind("<<ComboboxSelected>>", self._on_kind_selected)

        ttk.Label(meta, text="User").pack(side=tk.LEFT)
        ttk.Radiobutton(meta, text="1", variable=self._user_slot, value=1).pack(side=tk.LEFT)
        ttk.Radiobutton(meta, text="2", variable=self._user_slot, value=2).pack(
            side=tk.LEFT, padx=(0, 12)
        )

        row2 = ttk.Frame(self)
        row2.pack(fill=tk.X, **pad)
        ttk.Label(row2, text="Device").pack(side=tk.LEFT)
        ttk.Entry(row2, textvariable=self._device, width=16).pack(side=tk.LEFT, padx=(6, 12))
        ttk.Label(row2, text="Host app").pack(side=tk.LEFT)
        ttk.Entry(row2, textvariable=self._host, width=22).pack(side=tk.LEFT, padx=(6, 0))

        self._blurb = ttk.Label(self, wraplength=920, justify=tk.LEFT)
        self._blurb.pack(fill=tk.X, padx=10, pady=(2, 4))

        actions = ttk.Frame(self)
        actions.pack(fill=tk.X, padx=10, pady=(0, 4))
        self._end_btn = ttk.Button(
            actions, text="End session", command=self._end_session, state=tk.DISABLED
        )
        self._end_btn.pack(side=tk.LEFT)
        ttk.Button(actions, text="Undo", command=self._undo).pack(side=tk.LEFT, padx=6)
        ttk.Button(actions, text="Save now", command=self._save).pack(side=tk.LEFT)
        ttk.Button(actions, text="Note", command=lambda: self._click("note")).pack(
            side=tk.LEFT, padx=(16, 4)
        )
        ttk.Button(
            actions, text="Phone clock", command=lambda: self._click("phone_clock")
        ).pack(side=tk.LEFT)

        self._next_hint = ttk.Label(self, text="")
        self._next_hint.pack(fill=tk.X, padx=10)

        flow_wrap = ttk.Frame(self)
        flow_wrap.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 4))
        flow_wrap.rowconfigure(0, weight=1)
        flow_wrap.columnconfigure(0, weight=1)
        self._flow_canvas = tk.Canvas(flow_wrap, highlightthickness=0, height=280)
        vscroll = ttk.Scrollbar(
            flow_wrap, orient=tk.VERTICAL, command=self._flow_canvas.yview
        )
        hscroll = ttk.Scrollbar(
            flow_wrap, orient=tk.HORIZONTAL, command=self._flow_canvas.xview
        )
        self._flow_canvas.configure(
            yscrollcommand=vscroll.set, xscrollcommand=hscroll.set
        )
        self._flow_canvas.grid(row=0, column=0, sticky="nsew")
        vscroll.grid(row=0, column=1, sticky="ns")
        hscroll.grid(row=1, column=0, sticky="ew")
        self._flow_inner = ttk.Frame(self._flow_canvas)
        self._flow_window = self._flow_canvas.create_window(
            (0, 0), window=self._flow_inner, anchor="nw"
        )
        self._flow_inner.bind("<Configure>", self._on_flow_configure)
        self._flow_canvas.bind("<Configure>", self._on_canvas_configure)
        self._flow_canvas.bind("<Shift-MouseWheel>", self._on_shift_wheel)
        self._flow_canvas.bind("<MouseWheel>", self._on_wheel)
        self._flow_inner.bind("<Shift-MouseWheel>", self._on_shift_wheel)
        self._flow_inner.bind("<MouseWheel>", self._on_wheel)

        notes = ttk.LabelFrame(self, text="What to do now")
        notes.pack(fill=tk.X, padx=10, pady=(4, 4))
        self._step_notes = tk.Text(
            notes, height=7, wrap=tk.WORD, state=tk.DISABLED, padx=8, pady=6
        )
        self._step_notes.pack(fill=tk.X, expand=True)

        self._log = tk.Text(self, height=7, wrap=tk.WORD, state=tk.DISABLED)
        self._log.pack(fill=tk.BOTH, expand=False, padx=10, pady=(0, 4))
        self._status = ttk.Label(
            self, text="Pick a kind. Click the first flowchart box to start."
        )
        self._status.pack(fill=tk.X, padx=10, pady=(0, 10))

        self.bind("<Escape>", lambda _e: self._undo())

    def _on_flow_configure(self, _event: object = None) -> None:
        self._flow_canvas.configure(scrollregion=self._flow_canvas.bbox("all"))
        self._center_flow()

    def _on_canvas_configure(self, _event: tk.Event) -> None:
        self._center_flow()

    def _center_flow(self) -> None:
        if self._flow_window is None:
            return
        canvas_h = max(self._flow_canvas.winfo_height(), 1)
        inner_h = self._flow_inner.winfo_reqheight()
        y = max((canvas_h - inner_h) // 2, 4)
        self._flow_canvas.coords(self._flow_window, 8, y)

    def _on_wheel(self, event: tk.Event) -> None:
        self._flow_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _on_shift_wheel(self, event: tk.Event) -> None:
        self._flow_canvas.xview_scroll(int(-event.delta / 120), "units")

    def _selected_kind(self) -> CaptureKind:
        index = self._kind_combo.current()
        if index < 0:
            index = 0
        return CAPTURE_KINDS[index]

    def _active_kind(self) -> CaptureKind:
        if self._session is not None:
            return kind_by_id(self._session.kind_id)
        return self._selected_kind()

    def _logged_ids(self) -> list[str]:
        if self._session is None:
            return []
        return [event.event_id for event in self._session.events]

    def _on_kind_selected(self, _event: object = None) -> None:
        if self._session is not None:
            messagebox.showinfo(
                "Session running",
                "Kind is locked for this session. End session, then change kind.",
            )
            for i, kind in enumerate(CAPTURE_KINDS):
                if kind.id == self._session.kind_id:
                    self._kind_combo.current(i)
                    break
            return
        self._refresh_kind()

    def _refresh_kind(self) -> None:
        kind = self._selected_kind()
        self._kind_id.set(kind.id)
        self._blurb.configure(text=f"{kind.blurb}\nSuggested capture file: {kind.save_as}")
        self._draw_flowchart()

    def _draw_flowchart(self) -> None:
        """Two left-to-right bands; future and unused-branch steps stay disabled."""
        for child in self._flow_inner.winfo_children():
            child.destroy()
        self._buttons.clear()
        kind = self._active_kind()
        flow = kind.flow
        done = {eid for eid in self._logged_ids() if eid in set(flow_nodes(flow))}
        choices = set(next_choices(flow, self._logged_ids()))
        bands = wrap_layer_rows(flow_layers(flow))
        nonempty = [band for band in bands if band]
        for index, band in enumerate(nonempty):
            band_frame = ttk.Frame(self._flow_inner)
            band_frame.pack(anchor="w", pady=(0, 12 if index < len(nonempty) - 1 else 0))
            wrap = index < len(nonempty) - 1
            self._draw_flow_band(band_frame, band, done, choices, trailing_wrap=wrap)

        self._update_next_hint()
        self._update_step_panel()
        self._flow_inner.update_idletasks()
        self._on_flow_configure()

    def _draw_flow_band(
        self,
        parent: ttk.Frame,
        layers: Sequence[tuple[str, ...]],
        done: set[str],
        choices: set[str],
        *,
        trailing_wrap: bool,
    ) -> None:
        rows = max((len(layer) for layer in layers), default=1)
        last_col = 0
        for col, layer in enumerate(layers):
            grid_col = col * 2
            last_col = grid_col
            span = rows if len(layer) == 1 else 1
            for row, event_id in enumerate(layer):
                self._place_node(
                    parent,
                    event_id,
                    done=event_id in done,
                    active=event_id in choices,
                    row=row,
                    column=grid_col,
                    rowspan=span,
                )
            if col < len(layers) - 1:
                ttk.Label(parent, text="→", style="Arrow.TLabel").grid(
                    row=0,
                    column=grid_col + 1,
                    rowspan=rows,
                    padx=4,
                    sticky="nsew",
                )
                last_col = grid_col + 1
        if trailing_wrap and layers:
            ttk.Label(parent, text="↴", style="Arrow.TLabel").grid(
                row=0,
                column=last_col + 1,
                rowspan=rows,
                padx=6,
                sticky="nsew",
            )

    def _place_node(
        self,
        parent: ttk.Frame,
        event_id: str,
        *,
        done: bool,
        active: bool,
        row: int,
        column: int,
        rowspan: int,
    ) -> None:
        if done:
            text = "✓  " + EVENT_LABELS[event_id]
            style = "Done.TButton"
            state = tk.DISABLED
        elif active:
            text = EVENT_LABELS[event_id]
            style = "Next.TButton"
            state = tk.NORMAL
        else:
            text = EVENT_LABELS[event_id]
            style = "Wait.TButton"
            state = tk.DISABLED
        btn = ttk.Button(
            parent,
            text=text,
            style=style,
            state=state,
            width=20,
            command=lambda eid=event_id: self._click(eid),
        )
        btn.grid(
            row=row,
            column=column,
            rowspan=rowspan,
            padx=2,
            pady=4,
            ipady=6,
            sticky="nsew",
        )
        self._buttons[event_id] = btn

    def _end_session(self) -> None:
        if self._session is None:
            return
        if not messagebox.askyesno(
            "End session",
            "Close this session? The log stays on disk. You can then pick another kind.",
        ):
            return
        self._persist()
        self._session = None
        self._stem = None
        self._kind_combo.configure(state="readonly")
        self._end_btn.configure(state=tk.DISABLED)
        self._append_line("— session closed; pick a kind and click Start session —")
        self._status.configure(text="Session closed.")
        self._draw_flowchart()

    def _begin_session(self) -> None:
        kind = self._selected_kind()
        started = datetime.now(timezone.utc)
        self._session = new_session(
            kind,
            device_model=self._device.get(),
            host_app=self._host.get(),
            user_slot=int(self._user_slot.get()),
            now=started,
        )
        self._stem = session_stem(self._session, started)
        self._kind_combo.configure(state="disabled")
        self._end_btn.configure(state=tk.NORMAL)
        self._append_line("— session file will autosave after each click —")
        self._show_event(self._session.events[0])
        self._persist()

    def _click(self, event_id: str) -> None:
        kind = self._active_kind()
        if event_id not in ALWAYS_EVENTS:
            allowed = next_choices(kind.flow, self._logged_ids())
            if event_id not in allowed:
                return
        if event_id == "session_start":
            if self._session is None:
                self._begin_session()
                self._draw_flowchart()
            return
        if self._session is None:
            self._begin_session()
        assert self._session is not None
        if event_id not in ALWAYS_EVENTS:
            allowed = next_choices(kind.flow, self._logged_ids())
            if event_id not in allowed:
                self._draw_flowchart()
                return
        note = ""
        extra: dict = {}
        if event_id == "note":
            typed = simpledialog.askstring("Note", "What happened?", parent=self)
            if typed is None:
                return
            note = typed.strip()
            if not note:
                return
        elif event_id == "phone_clock":
            typed = simpledialog.askstring(
                "Phone clock",
                "Time shown on the phone right now (HH:MM:SS is enough):",
                parent=self,
            )
            if typed is None:
                return
            extra["phone_clock"] = typed.strip()
            if not extra["phone_clock"]:
                return
        elif event_id == "user_button":
            extra["user_slot"] = int(self._user_slot.get())
            self._session.user_slot = extra["user_slot"]
        event = self._session.append(event_id, note=note, extra=extra)
        self._show_event(event)
        self._persist()
        self._draw_flowchart()

    def _undo(self) -> None:
        if self._session is None:
            return
        removed = self._session.undo()
        if removed is None:
            return
        self._append_line(f"undo seq {removed.seq} `{removed.event_id}`")
        if not self._session.events:
            self._session = None
            self._stem = None
            self._kind_combo.configure(state="readonly")
            self._end_btn.configure(state=tk.DISABLED)
            self._status.configure(text="Session cleared.")
        else:
            self._persist()
        self._draw_flowchart()

    def _save(self) -> None:
        if self._session is None:
            self._status.configure(text="Nothing to save.")
            return
        paths = self._persist()
        if paths:
            self._status.configure(text=f"Saved {paths[0].name}")

    def _persist(self) -> tuple[Path, Path] | None:
        if self._session is None or self._stem is None:
            return None
        return write_session(self._session, SESSIONS_DIR, self._stem)

    def _show_event(self, event: CaptureEvent) -> None:
        extra = f" {event.extra}" if event.extra else ""
        note = f" — {event.note}" if event.note else ""
        self._append_line(
            f"{event.seq:02d}  +{event.since_session_s:8.3f}s  {event.pc_local}  "
            f"{event.event_id}{extra}{note}"
        )
        self._status.configure(
            text=f"Last: {event.label} at {event.pc_local}  (Δ {event.since_session_s:.3f}s)"
        )

    def _append_line(self, text: str) -> None:
        self._log.configure(state=tk.NORMAL)
        self._log.insert(tk.END, text.rstrip() + "\n")
        self._log.see(tk.END)
        self._log.configure(state=tk.DISABLED)

    def _update_next_hint(self) -> None:
        kind = self._active_kind()
        choices = next_choices(kind.flow, self._logged_ids())
        if not choices:
            self._next_hint.configure(text="Flow finished. Add a note or pull the log.")
            return
        labels = "  or  ".join(EVENT_LABELS[eid] for eid in choices)
        self._next_hint.configure(text=f"Next: {labels}")

    def _update_step_panel(self) -> None:
        if not getattr(self, "_step_notes", None):
            return
        kind = self._active_kind()
        text = step_panel_text(
            kind,
            next_choices(kind.flow, self._logged_ids()),
            host=self._host.get(),
            user=int(self._user_slot.get()),
            device=self._device.get(),
        )
        widget = self._step_notes
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert("1.0", text)
        widget.configure(state=tk.DISABLED)


def main(argv: list[str] | None = None) -> int:
    del argv
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    app = CaptureSessionApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
