#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""QA a finished short-drama shot.

Checks include:
- frame extraction for manual inspection (face consistency, screen text)
- audio stats: mean/peak level, clipping, DC offset, loudness
- optional SRT / speech text speed check
- optional external OCR / face-consistency command hooks

External models are not bundled: pass your own script via --ocr-cmd/--face-cmd.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


def log(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    return proc.returncode, proc.stdout, proc.stderr


def find_tool(name: str, env_key: str) -> str:
    env = os.environ.get(env_key)
    if env:
        return env
    found = shutil.which(name)
    if found:
        return found
    raise RuntimeError(f"{name} not found; pass it via --{name.lower()} or {env_key}")


def first_float(text: str, label: str) -> float | None:
    match = re.search(rf"{re.escape(label)}:\s*([-+0-9.]+)", text)
    return float(match.group(1)) if match else None


def last_float_by_label(text: str, label: str) -> float | None:
    values = re.findall(rf"{re.escape(label)}:\s*([-+0-9.]+)", text)
    return float(values[-1]) if values else None


def spoken_chars(text: str) -> int:
    stripped = re.sub(r"^[^:：]{1,20}[:：]", "", text or "")
    return len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", stripped))


def parse_srt(text: str) -> list[dict[str, Any]]:
    cues: list[dict[str, Any]] = []
    blocks = re.split(r"\n\s*\n", text.strip())
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3:
            continue
        body = "".join(lines[2:])
        cue: dict[str, Any] = {"text": body, "chars": spoken_chars(body)}
        time_match = re.search(
            r"(\d+:\d+:\d+,\d+)\s*-->\s*(\d+:\d+:\d+,\d+)",
            lines[1],
        )
        if time_match:
            start = srt_seconds(time_match.group(1))
            end = srt_seconds(time_match.group(2))
            cue["duration"] = end - start if start is not None and end is not None else None
        cues.append(cue)
    return cues


def srt_seconds(value: str) -> float | None:
    parts = re.split(r"[:,]", value)
    if len(parts) != 4:
        return None
    try:
        h, m, s, ms = (int(part) for part in parts)
        return h * 3600 + m * 60 + s + ms / 1000.0
    except ValueError:
        return None


def probe_with_ffmpeg(ffmpeg: str, video: Path) -> dict[str, Any]:
    rc, _out, err = run([ffmpeg, "-hide_banner", "-i", str(video)])
    duration: float | None = None
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if match:
        h, m, s = (float(match.group(i)) for i in (1, 2, 3))
        duration = h * 3600 + m * 60 + s
    streams: list[dict[str, Any]] = []
    for line in err.splitlines():
        if "Stream #0:" not in line:
            continue
        kind = "audio" if "Audio:" in line else "video" if "Video:" in line else "subtitle"
        item: dict[str, Any] = {"codec_type": kind}
        size = re.search(r",\s*(\d{2,5})x(\d{2,5})\s*[,\[]", line)
        if size:
            item["width"], item["height"] = int(size.group(1)), int(size.group(2))
        fps = re.search(r",\s*([0-9]+(?:\.[0-9]+)?)\s*fps", line)
        if fps:
            item["r_frame_rate"] = fps.group(1)
        if duration is not None:
            item["duration"] = duration
        streams.append(item)
    if rc not in (0, 1):
        streams = []
    if not streams:
        streams = [{"codec_type": "video"}] if "Video: " in err else []
    return {"duration": duration, "streams": streams}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="QA a finished short-drama shot")
    p.add_argument("--video", required=True, help="Path to the final video")
    p.add_argument("--frames", default="1.0,2.5,4.0", help="Comma-separated times")
    p.add_argument("--output", default=None, help="Frame output directory")
    p.add_argument("--duration", default=None, help="Shot duration override (seconds)")
    p.add_argument("--srt", default=None, help="Optional SRT file for subtitle speed check")
    p.add_argument("--speech-text", default=None, help="Optional speech text for speed check")
    p.add_argument("--ocr-cmd", default=None, help="External OCR command; use {frame} for frame path")
    p.add_argument("--face-cmd", default=None, help="External face-consistency command; use {frame}")
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p.add_argument("--ffmpeg", default=None)
    p.add_argument("--ffprobe", default=None)
    p.add_argument("--no-audio", action="store_true", help="Skip audio level check")
    return p.parse_args()


@dataclass
class Finding:
    kind: str
    level: str
    detail: str


class ShotQA:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.findings: list[Finding] = []

    def add(self, kind: str, level: str, detail: str) -> None:
        self.findings.append(Finding(kind=kind, level=level, detail=detail))

    def info(self, kind: str, detail: str) -> None:
        self.findings.append(Finding(kind=kind, level="INFO", detail=detail))

    def run(self) -> int:
        args = self.args
        video = Path(args.video).resolve()
        if not video.is_file():
            self.add("video", "FAIL", f"video not found: {video}")
            return self.finish(2)

        try:
            self.ffmpeg = args.ffmpeg or find_tool("ffmpeg", "FFMPEG")
        except RuntimeError as exc:
            self.add("tool", "FAIL", str(exc))
            return self.finish(2)
        self.ffprobe = args.ffprobe or os.environ.get("FFPROBE") or shutil.which("ffprobe")
        if not self.ffprobe:
            self.info("tool", "ffprobe 未找到，使用 ffmpeg 探测流信息；需要精确流信息时可显式传 --ffprobe")

        out_dir = Path(args.output) if args.output else video.parent / "qa_frames"
        out_dir.mkdir(parents=True, exist_ok=True)
        self.extract_frames(video, out_dir)
        self.external_hooks(out_dir)
        duration = self.video_audio(video)
        self.check_speech(video, duration)
        return self.finish(0)

    def extract_frames(self, video: Path, out_dir: Path) -> None:
        for t in self.args.frames.split(","):
            t = t.strip()
            if not t:
                continue
            frame = out_dir / f"{video.stem}_@{t.replace('.', '_')}.png"
            rc, _out, err = run(
                [
                    self.ffmpeg,
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
                self.add("frame", "FAIL", f"frame {t}s: {err.strip()}")
            else:
                self.info("frame", f"{t}s -> {frame}")

    def external_hooks(self, out_dir: Path) -> None:
        frames = sorted(out_dir.glob("*.png"))
        if not frames:
            self.add("external", "WARN", "没有可检查的抽帧")
            self.info("external", "未配置 OCR/人脸一致性外部钩子，画面文字与崩脸需人工目检")
            return
        for name, flag in (("OCR", "--ocr-cmd"), ("face", "--face-cmd")):
            cmd_text = getattr(self.args, "ocr_cmd" if name == "OCR" else "face_cmd")
            if not cmd_text:
                self.info("external", f"{name} 未配置（{flag}），{name} 检查需人工兜底")
                continue
            for frame in frames:
                rendered = str(cmd_text).replace("{frame}", str(frame))
                try:
                    cmd_list = shlex.split(rendered)
                except ValueError as exc:
                    self.add(name.lower(), "FAIL", f"命令解析失败: {exc}")
                    continue
                rc, stdout, stderr = run(cmd_list)
                detail = (stdout or stderr or "").strip().replace("\n", " ")
                if rc != 0:
                    self.add(name.lower(), "FAIL", f"{frame.name} rc={rc} {detail[:200]}")
                else:
                    self.info(name.lower(), f"{frame.name} ok {detail[:200]}")

    def video_audio(self, video: Path) -> float | None:
        if self.ffprobe:
            rc, stdout, stderr = run(
                [
                    self.ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "stream",
                    "-of",
                    "json",
                    str(video),
                ]
            )
            if rc != 0:
                self.add("probe", "FAIL", stderr.strip())
                return None
            try:
                info = json.loads(stdout or "{}")
            except json.JSONDecodeError:
                info = {}
        else:
            info = probe_with_ffmpeg(self.ffmpeg, video)
        duration: float | None = None
        has_audio = False
        for stream in info.get("streams", []):
            kind = stream.get("codec_type")
            self.info(
                "stream",
                f"{kind} {stream.get('width', '')}x{stream.get('height', '')} "
                f"{stream.get('r_frame_rate', '')} {stream.get('duration', '')}s",
            )
            if kind == "video":
                try:
                    duration = float(stream.get("duration"))
                except (TypeError, ValueError):
                    duration = None
            elif kind == "audio":
                has_audio = True
        if self.args.duration:
            try:
                duration = float(self.args.duration)
            except ValueError:
                self.add("speech", "WARN", f"--duration 无法解析: {self.args.duration}")
        if not has_audio or self.args.no_audio:
            if not self.args.no_audio:
                self.add("audio", "WARN", "视频没有音轨或未启用音频检查")
            return duration
        self.audio_stats(video)
        return duration

    def audio_stats(self, video: Path) -> None:
        rc, _out, err = run(
            [
                self.ffmpeg,
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
        mean = last_float_by_label(err, "mean_volume")
        peak = last_float_by_label(err, "max_volume")
        if rc != 0 and mean is None:
            self.add("audio", "WARN", "音轨不可用")
            return
        if mean is not None:
            self.info("audio", f"mean {mean} dB, peak {peak} dB")
            if peak is not None and peak > -1.0:
                self.add("audio", "FAIL", f"峰值 {peak} dB 超过 -1 dB，存在过载风险")
            elif peak is not None and peak > -3.0:
                self.add("audio", "WARN", f"峰值 {peak} dB 建议压到 -3 dB 以下")
            if mean is not None and not (-24.0 <= mean <= -14.0):
                self.add("audio", "WARN", f"平均电平 {mean} dB，建议控制在 -24 到 -14 dB")

        rc2, _out2, err2 = run(
            [
                self.ffmpeg,
                "-hide_banner",
                "-i",
                str(video),
                "-map",
                "0:a:0?",
                "-af",
                "astats=metadata=1:reset=1",
                "-f",
                "null",
                "-",
            ]
        )
        if rc2 == 0:
            rms = last_float_by_label(err2, "RMS level dB")
            dc = last_float_by_label(err2, "DC offset")
            peak_count = last_float_by_label(err2, "Peak count")
            if rms is not None:
                self.info("audio", f"RMS {rms} dB, DC offset {dc}, Peak count {peak_count}")
            if dc is not None and abs(dc) > 0.01:
                self.add("audio", "WARN", f"直流偏移 {dc}，建议高通或重新混音")
            if peak_count is not None and peak_count > 0:
                self.add("audio", "FAIL", f"检测到 {int(peak_count)} 个削波峰")

        rc3, _out3, err3 = run(
            [
                self.ffmpeg,
                "-hide_banner",
                "-i",
                str(video),
                "-map",
                "0:a:0?",
                "-af",
                "loudnorm=print_format=summary",
                "-f",
                "null",
                "-",
            ]
        )
        if rc3 == 0:
            integrated = first_float(err3, "Input Integrated")
            true_peak = first_float(err3, "Input True Peak")
            if integrated is not None:
                self.info("audio", f"loudnorm integrated {integrated} LUFS, true peak {true_peak} dBTP")
                if not (-20.0 <= integrated <= -12.0):
                    self.add("audio", "WARN", f"综合响度 {integrated} LUFS，建议 -20 到 -12 LUFS")
            if true_peak is not None and true_peak > -1.0:
                self.add("audio", "FAIL", f"真实峰值 {true_peak} dBTP 超限")

    def check_speech(self, video: Path, duration: float | None) -> None:
        args = self.args
        if args.srt:
            srt_path = Path(args.srt)
            if not srt_path.is_file():
                self.add("speech", "FAIL", f"SRT 不存在: {srt_path}")
            else:
                text = srt_path.read_text(encoding="utf-8-sig", errors="replace")
                cues = parse_srt(text)
                if not cues:
                    self.add("speech", "WARN", "SRT 没有解析到字幕")
                for index, cue in enumerate(cues, 1):
                    cue_duration = cue.get("duration")
                    self.check_rate(f"SRT#{index}", cue["chars"], cue_duration or duration)
                total_chars = sum(cue["chars"] for cue in cues)
                if duration and total_chars:
                    self.check_rate("SRT-total", total_chars, duration)
                else:
                    self.add("speech", "WARN", "SRT 已检查，缺视频时长无法算总语速")
        speech_text = args.speech_text
        if speech_text:
            chars = spoken_chars(speech_text)
            if duration and chars:
                self.check_rate("speech-text", chars, duration)
            else:
                self.add("speech", "INFO", f"speech text {chars} 字，缺时长未算语速")

    def check_rate(self, label: str, chars: int, duration: float | None) -> None:
        if not duration or duration <= 0:
            self.add("speech", "WARN", f"{label}: {chars} 字，时长不可用无法算语速")
            return
        rate = chars / duration
        if rate > 8:
            self.add("speech", "FAIL", f"{label}: {rate:.1f} 字/秒（{chars}字/{duration:.1f}s）")
        elif rate > 6:
            self.add("speech", "WARN", f"{label}: {rate:.1f} 字/秒（{chars}字/{duration:.1f}s）")
        else:
            self.info("speech", f"{label}: {rate:.1f} 字/秒")

    def finish(self, base_code: int) -> int:
        args = self.args
        fails = [item for item in self.findings if item.level == "FAIL"]
        warns = [item for item in self.findings if item.level == "WARN"]
        if args.json:
            payload = {
                "video": str(Path(args.video).resolve()),
                "findings": [asdict(item) for item in self.findings],
                "summary": {"fail": len(fails), "warn": len(warns)},
                "ok": not fails,
                "exit_code": 1 if fails else base_code,
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return payload["exit_code"]
        for item in self.findings:
            if item.level != "INFO":
                log(f"  [{item.kind}] {item.level}: {item.detail}")
        for item in self.findings:
            if item.level == "INFO":
                log(f"  [{item.kind}] info: {item.detail}")
        log(f"FAIL: {len(fails)}  WARN: {len(warns)}")
        return 1 if fails else base_code


def main() -> int:
    args = parse_args()
    return ShotQA(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
