#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Start a ComfyUI server and wait for its API health check.

Paths come from --install-dir / --shared-dir, or COMFYUI_INSTALL_DIR /
COMFYUI_SHARED_DIR, so the same script can be used on any machine.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def log(msg: str) -> None:
    print(msg, flush=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Start and health-check a ComfyUI server")
    p.add_argument(
        "--install-dir",
        default=os.environ.get("COMFYUI_INSTALL_DIR"),
        help="Directory containing main.py",
    )
    p.add_argument(
        "--shared-dir",
        default=os.environ.get("COMFYUI_SHARED_DIR"),
        help="ComfyUI shared root with input/output/models",
    )
    p.add_argument("--port", type=int, default=8188)
    p.add_argument("--listen", default="127.0.0.1")
    p.add_argument(
        "--extra-model-paths",
        default=os.environ.get("COMFYUI_EXTRA_MODEL_PATHS"),
        help="extra_model_paths.yaml to pass to the server",
    )
    p.add_argument("--health-only", action="store_true", help="Only check the API")
    p.add_argument(
        "--detach",
        action="store_true",
        help="Start in background and return after health check",
    )
    p.add_argument("--timeout", type=float, default=120.0, help="Health check timeout")
    return p.parse_args()


def find_python(install_dir: Path) -> str:
    candidates = [
        install_dir / ".venv" / "Scripts" / "python.exe",
        install_dir / "standalone-env" / "python.exe",
        "python",
    ]
    for c in candidates:
        if isinstance(c, Path) and c.is_file():
            return str(c)
        if isinstance(c, str):
            return c
    raise RuntimeError(f"no python interpreter found under {install_dir}")


def health_ok(base: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base + "/system_stats", timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, dict) and "system" in data:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def main() -> int:
    args = parse_args()
    base = f"http://{args.listen}:{args.port}"

    if args.health_only:
        ok = health_ok(base, args.timeout)
        log(f"health {'OK' if ok else 'FAIL'} {base}")
        return 0 if ok else 1

    if not args.install_dir:
        print("--install-dir (or COMFYUI_INSTALL_DIR) is required", file=sys.stderr)
        return 2
    install_dir = Path(args.install_dir)
    main_py = install_dir / "main.py"
    if not main_py.is_file():
        print(f"main.py not found: {main_py}", file=sys.stderr)
        return 2

    cmd = [
        find_python(install_dir),
        str(main_py),
        "--listen",
        args.listen,
        "--port",
        str(args.port),
    ]
    if args.extra_model_paths:
        cmd += ["--extra-model-paths-config", args.extra_model_paths]

    if args.shared_dir:
        shared = Path(args.shared_dir)
        (shared / "input").mkdir(parents=True, exist_ok=True)
        (shared / "output").mkdir(parents=True, exist_ok=True)
        cmd += ["--input-directory", str(shared / "input")]
        cmd += ["--output-directory", str(shared / "output")]

    if args.detach:
        log_dir = Path(__file__).resolve().parent.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout = (log_dir / "comfyui_stdout.log").open("a", encoding="utf-8")
        stderr = (log_dir / "comfyui_stderr.log").open("a", encoding="utf-8")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            cmd,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
        )
    else:
        proc = subprocess.Popen(cmd)

    log(f"starting ComfyUI: {' '.join(cmd)}")
    ok = health_ok(base, args.timeout)
    if proc.poll() is not None and not ok:
        print("ComfyUI exited before health check passed", file=sys.stderr)
        return 1
    log(f"health {'OK' if ok else 'FAIL'} {base}")
    log(f"pid={proc.pid}")
    if not args.detach:
        try:
            return proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            return 130
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
