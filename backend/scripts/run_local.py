from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"


def command(name: str, argv: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen:
    print(f"[{name}] {' '.join(argv)}")
    return subprocess.Popen(argv, cwd=cwd, env=env)


def wait_for_port(port: int, processes: list[subprocess.Popen], timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        failed = next((process for process in processes if process.poll() is not None), None)
        if failed is not None:
            raise RuntimeError(f"Local service exited with code {failed.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for 127.0.0.1:{port}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the complete PoC without Docker.")
    parser.add_argument("--no-frontend", action="store_true")
    args = parser.parse_args()

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    python = sys.executable
    processes = [
        command("knowledge-mcp", [python, "-m", "mcp_servers.knowledge"], BACKEND_ROOT, env),
        command("finance-mcp", [python, "-m", "mcp_servers.finance"], BACKEND_ROOT, env),
    ]
    try:
        wait_for_port(8001, processes)
        wait_for_port(8002, processes)
        processes.append(
            command(
                "api",
                [
                    python,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8000",
                ],
                BACKEND_ROOT,
                env,
            )
        )
        wait_for_port(8000, processes)
        if not args.no_frontend:
            processes.append(
                command(
                    "frontend",
                    ["npm", "run", "dev", "--", "--host", "127.0.0.1"],
                    PROJECT_ROOT / "frontend",
                    env,
                )
            )
    except Exception:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        raise

    stopping = False

    def stop(*_: object) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        for process in processes:
            if process.poll() is None:
                process.terminate()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    endpoints = "API: http://127.0.0.1:8000/docs"
    if not args.no_frontend:
        endpoints = "UI: http://127.0.0.1:5173  " + endpoints
    print(f"Local services started. {endpoints}")
    try:
        while not stopping:
            failed = next((p for p in processes if p.poll() is not None), None)
            if failed is not None:
                raise SystemExit(f"A local service exited with code {failed.returncode}")
            time.sleep(0.25)
    finally:
        stop()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    main()
