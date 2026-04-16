from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import uvicorn


def port_is_busy(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return False
        except OSError:
            return True


def main() -> None:
    root = Path(__file__).resolve().parent
    app_dir = root / "pacifica-edge"
    if not app_dir.exists():
        raise SystemExit(f"Missing app directory: {app_dir}")

    host = "127.0.0.1"
    port = 8000

    if port_is_busy(host, port):
        print(f"Dashboard server already appears to be running on http://{host}:{port}")
        return

    os.chdir(app_dir)
    sys.path.insert(0, str(app_dir))

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=False,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
