#!/usr/bin/env python3
"""Copy veroval_ble onto the HAOS Samba share and restart Core over SSH.

GUI: destination path + Browse, and a deploy button. Restart runs only when
at least one file was copied. SSH identity file is VEROVAL_HAOS_SSH_KEY.
Optional overrides: scripts/deploy_haos.local.json
"""

from __future__ import annotations

import filecmp
import json
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "custom_components" / "veroval_ble"
CONFIG_PATH = Path(__file__).resolve().parent / "deploy_haos.local.json"
SSH_KEY_ENV = "VEROVAL_HAOS_SSH_KEY"

DEFAULTS = {
    "dest": r"V:\\",
    "ssh_host": "homeassistant.lan",
    "ssh_user": "root",
    "ssh_port": 22,
}

SKIP_DIR_NAMES = {"__pycache__", ".git"}


def _env(name: str) -> str:
    """Process env, then Windows user env (so a just-set User variable is visible)."""
    raw = os.environ.get(name, "").strip().strip('"')
    if raw:
        return raw
    if sys.platform != "win32":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return ""
    return str(value).strip().strip('"')


def _load_config() -> dict:
    data = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass
    return data


def _save_config(data: dict) -> None:
    payload = {
        "dest": data.get("dest", DEFAULTS["dest"]),
        "ssh_host": data.get("ssh_host", DEFAULTS["ssh_host"]),
        "ssh_user": data.get("ssh_user", DEFAULTS["ssh_user"]),
        "ssh_port": int(data.get("ssh_port", DEFAULTS["ssh_port"])),
    }
    CONFIG_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _should_skip_dir(name: str) -> bool:
    return name in SKIP_DIR_NAMES or name.endswith(".egg-info")


def copy_changed(src_root: Path, dest_root: Path) -> list[str]:
    """Copy files that are new or differ. Returns relative paths that were written."""
    if not src_root.is_dir():
        raise FileNotFoundError(f"Source not found: {src_root}")
    dest_root.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for src in src_root.rglob("*"):
        if not src.is_file():
            continue
        if any(_should_skip_dir(part) for part in src.relative_to(src_root).parts):
            continue
        if src.suffix == ".pyc":
            continue
        rel = src.relative_to(src_root)
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and filecmp.cmp(src, dest, shallow=False):
            continue
        shutil.copy2(src, dest)
        copied.append(rel.as_posix())
    return copied


def _ssh_identity() -> Path:
    raw = _env(SSH_KEY_ENV)
    if not raw:
        raise RuntimeError(
            f"Set {SSH_KEY_ENV} to your SSH private key path "
            r'(e.g. C:\Users\user\ssh\gamer).'
        )
    path = Path(raw).expanduser()
    if not path.is_file():
        raise RuntimeError(f"{SSH_KEY_ENV} is not a file: {path}")
    return path


def restart_core(host: str, user: str, port: int) -> str:
    target = f"{user}@{host}"
    identity = _ssh_identity()
    cmd = [
        "ssh",
        "-i",
        str(identity),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-p",
        str(port),
        target,
        "ha core restart",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "ssh not found. Install OpenSSH Client and set "
            f"{SSH_KEY_ENV} for {target}."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("SSH timed out after 60s.") from exc

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        detail = err or out or f"exit {proc.returncode}"
        raise RuntimeError(
            f"SSH restart failed ({target}): {detail}\n"
            f"Using {SSH_KEY_ENV}={identity}"
        )
    return out or "ha core restart requested"


class DeployApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("HAOS deploy")
        self.minsize(520, 240)
        self._cfg = _load_config()
        self._busy = False

        pad = {"padx": 10, "pady": 6}
        row = ttk.Frame(self)
        row.pack(fill=tk.X, **pad)
        ttk.Label(row, text="Path").pack(side=tk.LEFT)
        self._path = tk.StringVar(value=str(self._cfg.get("dest", DEFAULTS["dest"])))
        ttk.Entry(row, textvariable=self._path).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8)
        )
        ttk.Button(row, text="Browse", command=self._browse).pack(side=tk.RIGHT)

        self._btn = ttk.Button(self, text="deploy", command=self._on_deploy)
        self._btn.pack(fill=tk.X, padx=10, pady=(4, 8), ipady=8)

        self._log = tk.Text(self, height=10, wrap=tk.WORD, state=tk.DISABLED)
        self._log.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        key = _env(SSH_KEY_ENV) or "(unset)"
        self._append(
            f"Source: {SOURCE}\n"
            f"SSH: {self._cfg['ssh_user']}@{self._cfg['ssh_host']}:"
            f"{self._cfg['ssh_port']}  ha core restart\n"
            f"{SSH_KEY_ENV}: {key}"
        )

    def _browse(self) -> None:
        initial = self._path.get().strip() or str(DEFAULTS["dest"])
        chosen = filedialog.askdirectory(title="HAOS veroval_ble folder", initialdir=initial)
        if chosen:
            self._path.set(chosen)

    def _append(self, text: str) -> None:
        self._log.configure(state=tk.NORMAL)
        self._log.insert(tk.END, text.rstrip() + "\n")
        self._log.see(tk.END)
        self._log.configure(state=tk.DISABLED)

    def _on_deploy(self) -> None:
        if self._busy:
            return
        dest = Path(self._path.get().strip())
        if not dest.drive and not dest.exists():
            self._append(f"Destination does not exist: {dest}")
            return
        self._busy = True
        self._btn.configure(state=tk.DISABLED)
        self._append(f"\nDeploy → {dest}")
        threading.Thread(target=self._run, args=(dest,), daemon=True).start()

    def _run(self, dest: Path) -> None:
        try:
            copied = copy_changed(SOURCE, dest)
            if copied:
                preview = "\n".join(f"  {name}" for name in copied[:20])
                extra = "" if len(copied) <= 20 else f"\n  … {len(copied) - 20} more"
                self.after(0, self._append, f"Copied {len(copied)} file(s):\n{preview}{extra}")
                self.after(0, self._append, "Restarting Home Assistant Core…")
                message = restart_core(
                    str(self._cfg["ssh_host"]),
                    str(self._cfg["ssh_user"]),
                    int(self._cfg["ssh_port"]),
                )
                self.after(0, self._append, message)
                self.after(0, self._append, "Done.")
            else:
                self.after(0, self._append, "Already up to date; skipped restart.")
            self._cfg["dest"] = str(dest)
            try:
                _save_config(self._cfg)
            except OSError:
                pass
        except Exception as exc:
            self.after(0, self._append, f"Failed: {exc}")
        finally:
            self.after(0, self._done)

    def _done(self) -> None:
        self._busy = False
        self._btn.configure(state=tk.NORMAL)


def main() -> int:
    if sys.platform != "win32":
        print("This GUI is meant for Windows (Samba drive + OpenSSH).", file=sys.stderr)
    app = DeployApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
