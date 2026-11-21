"""CLI:  .\\.venv\\Scripts\\python -m studio <command>

  serve [--port 8765]     dashboard + API + workers (http://127.0.0.1:8765)
  demo [--tts windows_sapi|elevenlabs|silent_stub]
                          live end-to-end demo (~3 min), writes studio_data/demo_report.json
  migrate                 create / upgrade the database
  discover                run stream discovery once (needs API keys)
  growth                  write this week's growth report
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="studio")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--host", default="127.0.0.1")
    dm = sub.add_parser("demo")
    dm.add_argument("--tts", default="windows_sapi", choices=["windows_sapi", "elevenlabs", "silent_stub"])
    sub.add_parser("migrate")
    sub.add_parser("discover")
    sub.add_parser("growth")
    a = ap.parse_args(argv)
    from .config import settings
    from .db import migrate
    if a.cmd == "serve":
        import uvicorn
        migrate()
        uvicorn.run("studio.api:app", host=a.host, port=a.port, log_level="info")
    elif a.cmd == "demo":
        from .demo import run_demo
        r = run_demo(settings.data_dir / "demo_report.json", a.tts)
        print(json.dumps({"passed": r.get("passed"), "elapsed_s": r.get("elapsed_s"), "project_id": r.get("project_id")}))
        return 0 if r.get("passed") else 1
    elif a.cmd == "migrate":
        print("schema version", migrate())
    elif a.cmd == "discover":
        migrate()
        from .discovery import run_discovery
        print(json.dumps(run_discovery(), indent=1))
    elif a.cmd == "growth":
        migrate()
        from .growth import weekly_report
        print(json.dumps(weekly_report(), indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
