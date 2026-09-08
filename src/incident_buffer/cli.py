"""incident-buffer CLI. Serves the dashboard + runs the demo."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("dashboard extra not installed. Install with:\n"
              "  pip install 'incident-buffer[dashboard]'", file=sys.stderr)
        return 2
    os.environ["INCIDENT_BUFFER_DATA_DIR"] = str(Path(args.data_dir).resolve())
    if args.ingest_token:
        os.environ["INCIDENT_BUFFER_INGEST_TOKEN"] = args.ingest_token
    if args.viewer_token:
        os.environ["INCIDENT_BUFFER_VIEWER_TOKEN"] = args.viewer_token
    uvicorn.run(
        "incident_buffer.dashboard.app:app",
        host=args.host, port=args.port,
        log_level="info",
    )
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    from incident_buffer.demo import run_demo
    return run_demo(Path(args.data_dir))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="incident-buffer")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", help="run dashboard server")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--data-dir", default="./incidents")
    p_serve.add_argument("--ingest-token", default=None,
                         help="required to accept POST /api/ingest")
    p_serve.add_argument("--viewer-token", default=None,
                         help="if set, dashboard UI requires this bearer token")
    p_serve.set_defaults(func=_cmd_serve)

    p_demo = sub.add_parser("demo", help="write synthetic demo incidents")
    p_demo.add_argument("--data-dir", default="./incidents")
    p_demo.set_defaults(func=_cmd_demo)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
