"""Entry point of the packaged Windows app (GTA6StreamAI.exe).

Double-click:
  1. First run installs itself: copies the exe to %LOCALAPPDATA%\\GTA6StreamAI\\app and adds
     Desktop + Start Menu shortcuts (per-user, no admin rights needed).
  2. Starts the local server on 127.0.0.1 (first free port from 8765) and opens the browser.
  3. If it is already running, it just opens the browser on the running instance.
Close the console window to stop it. All data stays in %LOCALAPPDATA%\\GTA6StreamAI.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

APP = "GTA6 Stream AI"
HOME = pathlib.Path(os.environ.get("LOCALAPPDATA", pathlib.Path.home())) / "GTA6StreamAI"
PORT_FILE = HOME / "port.txt"


def running_port() -> int | None:
    try:
        port = int(PORT_FILE.read_text().strip())
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/keys", timeout=2) as r:
            return port if r.status == 200 else None
    except Exception:
        return None


def free_port(start: int = 8765) -> int:
    for p in range(start, start + 50):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("no free port")


def shortcut(target: pathlib.Path, link: pathlib.Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
          f"$s.TargetPath='{target}';$s.WorkingDirectory='{target.parent}';$s.IconLocation='{target},0';"
          f"$s.Description='{APP}';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def install() -> None:
    """Per-user install on first run (or when a newer exe is launched from elsewhere)."""
    if not getattr(sys, "frozen", False):
        return
    exe = pathlib.Path(sys.executable).resolve()
    dest_dir = HOME / "app"
    dest = dest_dir / "GTA6StreamAI.exe"
    if exe != dest.resolve() if dest.exists() else True:
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(exe, dest)
            print(f"Installed to {dest}")
        except OSError as e:  # the installed copy is running: keep using this exe
            print(f"(install copy skipped: {e})")
            dest = exe
    desktop = pathlib.Path(os.environ.get("USERPROFILE", pathlib.Path.home())) / "Desktop" / f"{APP}.lnk"
    menu = pathlib.Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP}.lnk"
    for link in (desktop, menu):
        if not link.exists():
            shortcut(dest if dest.exists() else exe, link)
    print("Shortcuts: Desktop + Start Menu")


def main() -> int:
    HOME.mkdir(parents=True, exist_ok=True)
    print(f"{APP} - Catch the Moment. Tell the Story. Grow the Channel.\n")
    port = running_port()
    if port:
        print(f"Already running on port {port}; opening the browser.")
        webbrowser.open(f"http://localhost:{port}/")
        time.sleep(1)
        return 0
    install()
    port = free_port()
    PORT_FILE.write_text(str(port))
    os.environ.setdefault("STUDIO_DATA_DIR", str(HOME / "data"))
    from studio.config import settings
    settings.env.setdefault("STUDIO_DATA_DIR", str(HOME / "data"))
    from studio.db import migrate
    migrate()
    from studio.media import FF
    print(f"FFmpeg: {FF}")
    url = f"http://localhost:{port}/"
    threading.Timer(2.0, lambda: webbrowser.open(url)).start()
    print(f"Dashboard: {url}\nData: {HOME}\nClose this window to stop the app.\n")
    import uvicorn
    from studio.api import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
