#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Extract frames and audio levels from a shot for manual QA."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def log(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    return proc.returncode, proc.stdout, proc.stderr


def find_tool(name: str, env_key: str) -> str:
    env = __import__("os").environ.get(env_key)
    if env:
        return env
    found = shutil.which(name)
    if found:
        return found
    raise RuntimeError(f"{name} not found; pass it via --{name.lower()} or {env_key}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="QA a finished short-drama shot")
    p.add_argument("--video", required=True, help="Path to the final video")
    p.add_argument("--frames", default="1.0,2.5,4.0", help="Comma-separated times")
    p.add_argument("--output", default=None, help="Frame output directory")
    p.add_argument("--ffmpeg", default=None)
    p.add_argument("--ffprobe", default=None)
    p.add_argument("--no-audio", action="store_true", help="Skip audio level check")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    video = Path(args.video).resolve()
    if not video.is_file():
        print(f"video not found: {video}", file=sys.stderr)
        return 2

    ffmpeg = args.ffmpeg or find_tool("ffmpeg", "FFMPEG")
    ffprobe = args.ffprobe or find_tool("ffprobe", "FFPROBE")
    out_dir = Path(args.output) if args.output else video.parent / "qa_frames"
    out_dir.mkdir(parents=True, exist_ok=True)

    rc, stdout, stderr = run([ffprobe, "-v", "error", "-show_entries", "stream", "-of", "json", str(video)])
    info = json.loads(stdout or "{}")
    streams = info.get("streams", [])
    for s in streams:
        log(
            f"stream: {s.get('codec_type')} {s.get('width', '')}x{s.get('height', '')} "
            f"{s.get('r_frame_rate', '')} {s.get('duration', '')}s"
        )

    for t in args.frames.split(","):
        t = t.strip()
        if not t:
            continue
        frame = out_dir / f"{video.stem}_@{t.replace('.', '_')}.png"
        rc, _out, err = run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                t,
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-y",
                str(frame),
            ]
        )
        if rc != 0 or not frame.is_file():
            log(f"frame {t}s: FAIL {err.strip()}")
        else:
            log(f"frame {t}s: {frame}")

    if not args.no_audio:
        rc, _out, err = run(
            [
                ffmpeg,
                "-hide_banner",
                "-i",
                str(video),
                "-map",
                "0:a:0?",
                "-af",
                "volumedetect",
                "-f",
                "null",
                "-",
            ]
        )
        if rc != 0:
            log(f"audio: skipped or unavailable ({err.strip().splitlines()[-1] if err.strip() else 'no audio'})")
        else:
            for key, label in (("mean_volume", "mean"), ("max_volume", "peak")):
                m = re.search(rf"{re.escape(key)}: ([-+0-9.]+) dB", err)
                if m:
                    log(f"audio {label}: {m.group(1)} dB")

    log("QA done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
