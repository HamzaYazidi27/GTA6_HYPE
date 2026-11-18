"""Running the radar on schedule, with or without the dashboard.

Three ways, all safe together (the scan lock and the "is a scan due?" check make
extra triggers harmless):

* inside the dashboard process: :class:`RadarWorker` thread, started by ``serve()``;
* standalone: ``python -m gta_factory radar worker`` (a foreground loop, e.g. in tmux);
* as a system service that also survives reboots and needs no open window:
  ``python -m gta_factory radar schedule install`` writes a launchd agent (macOS)
  or a crontab line (Linux) that runs ``radar tick`` every 15 minutes; ``tick``
  scans only when the interval set in the dashboard has elapsed.
"""
from __future__ import annotations

import os
import platform
import plistlib
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from ...app.core.errors import FactoryError
from .engine import radar_logger, tick

LAUNCHD_LABEL = "com.ghostplates.content-radar"
CRON_MARK = "# ghost-plates-content-radar"
TICK_MINUTES = 15


class RadarWorker(threading.Thread):
    """Background thread: checks every minute whether a scan is due and runs it."""

    def __init__(self, settings: Any, *, via: str = "dashboard", poll_seconds: float = 60.0, log: Callable[[str], None] | None = None):
        super().__init__(name="content-radar", daemon=True)
        self.settings = settings
        self.via = via
        self.poll = poll_seconds
        self.log = log or radar_logger(settings)
        self.stop_event = threading.Event()

    def run(self) -> None:
        self.log(f"radar: scheduler started ({self.via})")
        while not self.stop_event.is_set():
            try:
                tick(self.settings, via=self.via, log=self.log)
            except Exception as exc:  # the scheduler must outlive any single failure
                self.log(f"radar: scheduler error {type(exc).__name__}: {exc}")
            self.stop_event.wait(self.poll)

    def stop(self) -> None:
        self.stop_event.set()


def run_forever(settings: Any, *, via: str = "worker", poll_seconds: float = 60.0) -> None:
    w = RadarWorker(settings, via=via, poll_seconds=poll_seconds)
    w.daemon = False
    w.start()
    try:
        while w.is_alive():
            w.join(1.0)
    except KeyboardInterrupt:
        w.stop()


# ----------------------------------------------------------------------------- system schedule

def _python(settings: Any) -> str:
    venv = settings.root / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def launchd_plist(settings: Any) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def cron_line(settings: Any) -> str:
    log = settings.jobs_dir / ".radar" / "schedule.log"
    return f'*/{TICK_MINUTES} * * * * cd "{settings.root}" && "{_python(settings)}" -m gta_factory radar tick --via cron >> "{log}" 2>&1 {CRON_MARK}'


def _crontab() -> str:
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else ""


def schedule_status(settings: Any) -> dict[str, Any]:
    system = platform.system()
    if system == "Darwin":
        return {"system": "launchd", "installed": launchd_plist(settings).exists(), "where": str(launchd_plist(settings))}
    if shutil.which("crontab"):
        return {"system": "cron", "installed": CRON_MARK in _crontab(), "where": "crontab -l"}
    return {"system": "none", "installed": False, "where": "", "note": "No launchd or cron here: keep the dashboard or `radar worker` running."}


def install_schedule(settings: Any) -> str:
    """Install the 15-minute ``radar tick`` service. Returns a description of what was done."""
    (settings.jobs_dir / ".radar").mkdir(parents=True, exist_ok=True)
    system = platform.system()
    if system == "Darwin":
        path = launchd_plist(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        log = str(settings.jobs_dir / ".radar" / "schedule.log")
        plist = {
            "Label": LAUNCHD_LABEL,
            "ProgramArguments": [_python(settings), "-m", "gta_factory", "radar", "tick", "--via", "launchd"],
            "WorkingDirectory": str(settings.root),
            "StartInterval": TICK_MINUTES * 60,
            "RunAtLoad": True,
            "StandardOutPath": log,
            "StandardErrorPath": log,
            "EnvironmentVariables": {"PATH": f"/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:{Path.home()}/.local/bin"},
        }
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        with path.open("wb") as fh:
            plistlib.dump(plist, fh)
        proc = subprocess.run(["launchctl", "load", "-w", str(path)], capture_output=True, text=True)
        if proc.returncode != 0:
            raise FactoryError(f"launchctl could not load the radar service: {(proc.stderr or proc.stdout).strip()[:300]}")
        note = ""
        protected = [p for p in ("Desktop", "Documents", "Downloads") if str(settings.root).startswith(str(Path.home() / p))]
        if protected:
            note = (f"\n  note: the project is in ~/{protected[0]}, a folder macOS protects from background services. If the dashboard keeps"
                    " saying 'aucun planificateur', move the project folder to your home folder (e.g. ~/GTA6_HYPE) and run start.sh again.")
        return f"launchd agent installed: {path} (runs `radar tick` every {TICK_MINUTES} min, also after a reboot){note}"
    if shutil.which("crontab"):
        current = "\n".join(line for line in _crontab().splitlines() if CRON_MARK not in line)
        new = (current + "\n" if current.strip() else "") + cron_line(settings) + "\n"
        proc = subprocess.run(["crontab", "-"], input=new, capture_output=True, text=True)
        if proc.returncode != 0:
            raise FactoryError(f"crontab could not be updated: {(proc.stderr or proc.stdout).strip()[:300]}")
        return f"cron entry installed (runs `radar tick` every {TICK_MINUTES} min)"
    raise FactoryError("This system has neither launchd nor cron.", hint="Keep the dashboard open, or run `python -m gta_factory radar worker` in a terminal that stays open.")


def ensure_schedule(settings: Any) -> str:
    """Install the service unless it already is (what start.sh calls)."""
    st = schedule_status(settings)
    if st["system"] == "none":
        return "no launchd / cron here: scans run while the dashboard is open"
    if st["installed"]:
        return f"background service already installed ({st['system']})"
    return install_schedule(settings)


def uninstall_schedule(settings: Any) -> str:
    system = platform.system()
    if system == "Darwin":
        path = launchd_plist(settings)
        if path.exists():
            subprocess.run(["launchctl", "unload", "-w", str(path)], capture_output=True)
            path.unlink()
            return "launchd agent removed"
        return "no launchd agent was installed"
    if shutil.which("crontab"):
        lines = _crontab().splitlines()
        kept = [line for line in lines if CRON_MARK not in line]
        if len(kept) == len(lines):
            return "no cron entry was installed"
        subprocess.run(["crontab", "-"], input="\n".join(kept) + ("\n" if kept else ""), capture_output=True, text=True)
        return "cron entry removed"
    return "nothing to remove"


def spawn_scan(settings: Any) -> int:
    """Start a manual scan in its own process (the dashboard's "Scan now"). Returns its pid."""
    folder = settings.jobs_dir / ".radar"
    folder.mkdir(parents=True, exist_ok=True)
    out = open(folder / "scan.out", "ab")
    proc = subprocess.Popen([sys.executable, "-m", "gta_factory", "radar", "scan", "--trigger", "manual"], cwd=str(settings.root), stdout=out, stderr=out, start_new_session=True, env={**os.environ})
    return proc.pid
