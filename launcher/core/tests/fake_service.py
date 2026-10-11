"""Stand-ins for the supervisor tests, run as real processes.

    python fake_service.py service --port N [--ready-after S] [--exit-after S --code N] [--child]
                                   [--health-file F] [--pidfile F] [--ignore-term] [--journal JSON]...
    python fake_service.py game --check URL... [--run S] [--code N] [--pidfile F]

A service answers GET /health on its port (the body is --health-file's content when given) and prints a
line. --child keeps a sleeping child in its process group, as `uv run` keeps the service. The game checks
that every url answers, exits 3 if one does not, and otherwise exits with --code after --run seconds.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


def service(args: argparse.Namespace) -> None:
    pids = [os.getpid()]
    if args.child:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        pids.append(child.pid)
    if args.pidfile:
        Path(args.pidfile).write_text(" ".join(map(str, pids)))
    if args.ignore_term:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    print(f"fake service {os.getpid()} starting", flush=True)
    if args.exit_after is not None:
        threading.Timer(args.exit_after, lambda: (print("boom", flush=True), os._exit(args.code))).start()
    time.sleep(args.ready_after)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/health":
                self.send_error(404)
                return
            if args.health_file:
                body = Path(args.health_file).read_bytes()
            else:
                body = json.dumps({"ok": True, "service": "fake", "providers": [{"name": "fake"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"fake service listening on {args.port}", flush=True)
    for entry in args.journal:
        print(f"JOURNAL {entry}", flush=True)
    server.serve_forever()


def game(args: argparse.Namespace) -> None:
    if args.pidfile:
        Path(args.pidfile).write_text(str(os.getpid()))
    for url in args.check:
        try:
            urllib.request.urlopen(url, timeout=1).read()
        except OSError as err:
            print(f"not ready: {url}: {err}", flush=True)
            sys.exit(3)
    print(f"game started, server {os.environ.get('GOTHIC_AI_URL')}", flush=True)
    time.sleep(args.run)
    sys.exit(args.code)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="kind", required=True)
    s = sub.add_parser("service")
    s.add_argument("--port", type=int, required=True)
    s.add_argument("--ready-after", type=float, default=0.0)
    s.add_argument("--exit-after", type=float)
    s.add_argument("--code", type=int, default=0)
    s.add_argument("--child", action="store_true")
    s.add_argument("--health-file")
    s.add_argument("--pidfile")
    s.add_argument("--ignore-term", action="store_true")
    s.add_argument("--journal", action="append", default=[], help="a journal entry (JSON) to print once up")
    g = sub.add_parser("game")
    g.add_argument("--check", nargs="*", default=[])
    g.add_argument("--run", type=float, default=0.0)
    g.add_argument("--code", type=int, default=0)
    g.add_argument("--pidfile")
    args = parser.parse_args()
    service(args) if args.kind == "service" else game(args)


if __name__ == "__main__":
    main()
