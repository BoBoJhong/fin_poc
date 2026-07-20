from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from dotenv import dotenv_values

from app.config import Settings


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
    parser = argparse.ArgumentParser(description="Run the complete Verified RAG product stack.")
    parser.add_argument("--no-frontend", action="store_true")
    args = parser.parse_args()

    env = os.environ.copy()
    file_env = {key: value for key, value in dotenv_values(PROJECT_ROOT / ".env").items() if value}
    env.setdefault("PYTHONUNBUFFERED", "1")
    settings = Settings()
    python = sys.executable
    processes = [
        command("knowledge-mcp", [python, "-m", "mcp_servers.knowledge"], BACKEND_ROOT, env),
        command("finance-mcp", [python, "-m", "mcp_servers.finance"], BACKEND_ROOT, env),
    ]
    try:
        wait_for_port(settings.knowledge_mcp_port, processes)
        wait_for_port(settings.finance_mcp_port, processes)
        processes.append(
            command("verified-rag-mcp", [python, "-m", "mcp_servers.rag"], BACKEND_ROOT, env)
        )
        wait_for_port(settings.rag_mcp_port, processes)
        processes.append(
            command(
                "verified-transcript-mcp",
                [python, "-m", "mcp_servers.transcript"],
                BACKEND_ROOT,
                env,
            )
        )
        wait_for_port(settings.transcript_mcp_port, processes)
        processes.append(
            command(
                "api",
                [
                    python,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    settings.api_host,
                    "--port",
                    str(settings.api_port),
                    "--workers",
                    env.get("API_WORKERS", file_env.get("API_WORKERS", "1")),
                ],
                BACKEND_ROOT,
                env,
            )
        )
        wait_for_port(settings.api_port, processes)
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
    endpoints = (
        f"API: http://127.0.0.1:{settings.api_port}/docs  "
        f"Financial MCP: http://127.0.0.1:{settings.rag_mcp_port}/mcp  "
        f"Transcript MCP: http://127.0.0.1:{settings.transcript_mcp_port}/mcp"
    )
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
