"""Start the DeCraft studio: python scripts/serve_ui.py [--port 8765] [--no-browser]."""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.webui import serve  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="DeCraft studio: upload PDFs, generate decoys, download them.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    args = parser.parse_args()
    server = serve(port=args.port)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"DeCraft studio running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
