"""Cross-platform, cross-process offline smoke test."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    port = free_port()
    with tempfile.TemporaryDirectory(prefix="agent-smoke-") as directory:
        root = Path(directory)
        ready = root / "ready"
        summary = root / "summary.json"
        output = root / "episodes.jsonl"
        trace = root / "trace.jsonl"
        server = subprocess.Popen([
            sys.executable, "-m", "tests.mock_sim_server",
            "--port", str(port), "--ready-file", str(ready), "--summary-file", str(summary),
        ])
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError(f"mock server exited early with {server.returncode}")
                time.sleep(0.05)
            if not ready.exists():
                raise RuntimeError("mock server did not become ready")
            client = subprocess.run([
                sys.executable, "-m", "agent.run_agent",
                "--backend", "fake", "--sim-url", f"ws://127.0.0.1:{port}",
                "--seeds", "0-1", "--output", str(output), "--trace-output", str(trace), "--overwrite",
            ], check=False)
            server_code = server.wait(timeout=10)
            stats = json.loads(summary.read_text(encoding="utf-8"))
            results = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            if client.returncode != 0 or server_code != 0:
                raise RuntimeError(f"client={client.returncode}, server={server_code}")
            if len(results) != 2 or any(item["outcome"] != "success" for item in results):
                raise RuntimeError(f"unexpected results: {results}")
            expected = {"connections": 1, "episodes": 2, "actions": 4, "all_finish_count": 1, "invalid_messages": 0}
            if stats != expected:
                raise RuntimeError(f"unexpected server stats: {stats}")
            print("mock smoke passed: 2 successful episodes, 1 connection, 1 all_finish")
            return 0
        finally:
            if server.poll() is None:
                server.terminate()
                server.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())

