#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从分镜 JSON 生成批次表和配音对齐表。

批次按场景/光照/角色相近的镜头归类，避免不同场景混在同一个批次里生成。
配音对齐表把每一条台词/旁白、字数、预估语速、角色音色来源拉在一张表里，
供 TTS 与字幕阶段使用。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable


def read_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("text", data, 0, len(data), "unsupported encoding")


def first_key(obj: dict, keys: Iterable[str]) -> Any:
    for key in keys:
        value = obj.get(key)
        if value not in (None, ""):
            return value
    return None


def parse_duration(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", str(value or ""))
    return float(match.group(1)) if match else None


def spoken_chars(text: str) -> int:
    stripped = re.sub(r"^[^:：]{1,20}[:：]", "", text or "")
    return len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", stripped))


def split_lines(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def group_key(shot: dict) -> str:
    value = first_key(
        shot,
        ("batch_id", "group_id", "scene_id", "scene", "场景", "地点", "location"),
    )
    return str(value).strip() if value not in (None, "") else "未分组"


def shot_roles(shot: dict) -> str:
    value = first_key(
        shot,
        ("characters", "roles", "角色", "role_keys", "shots_persons"),
    )
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item)
    return str(value or "")


def shot_texts(shot: dict) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for keys, label in (
        (("vo_cn", "narration_cn", "旁白", "旁白文本"), "旁白"),
        (("dialogue_cn", "dialogue", "台词", "对白"), "台词"),
    ):
        for line in split_lines(first_key(shot, keys)):
            if line:
                rows.append((label, line))
    return rows


def load_shots(path: Path) -> tuple[dict, list[dict]]:
    data = json.loads(read_text(path))
    if isinstance(data, list):
        return {"shots": data}, [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        raise ValueError("分镜文件顶层必须是对象或数组")
    shots = data.get("shots")
    if not isinstance(shots, list):
        raise ValueError("分镜文件缺少 shots 数组")
    return data, [item for item in shots if isinstance(item, dict)]


def load_roles(path: Path | None) -> list[dict]:
    if path is None or not path.is_file():
        return []
    data = json.loads(read_text(path))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    images = data.get("images")
    if isinstance(images, list):
        return [item for item in images if isinstance(item, dict)]
    if isinstance(images, dict):
        return [dict(item, role=name) for name, item in images.items() if isinstance(item, dict)]
    if "images" not in data:
        return [dict(item, role=name) for name, item in data.items() if isinstance(item, dict)]
    return []


def voice_fields(role: dict) -> tuple[str, str, str]:
    voice = role.get("voice")
    if not isinstance(voice, dict):
        voice = role.get("tts")
    if isinstance(voice, dict):
        speaker = voice.get("speaker") or voice.get("voice") or voice.get("name") or ""
        seed = voice.get("seed") or ""
        instruct = voice.get("instruct") or ""
    else:
        speaker = role.get("speaker") or ""
        seed = role.get("seed") or ""
        instruct = role.get("instruct") or ""
    return str(speaker).strip(), str(seed).strip(), str(instruct).strip()


def rate_flag(rate: float) -> str:
    if rate > 8:
        return "FAIL-改稿"
    if rate > 6:
        return "WARN-注意"
    return "OK"


def build_batches(shots: list[dict]) -> list[tuple[str, list[dict]]]:
    groups: "OrderedDict[str, list[dict]]" = OrderedDict()
    for shot in shots:
        key = group_key(shot)
        groups.setdefault(key, []).append(shot)
    return [(f"B{index:02d}-{key}", items) for index, (key, items) in enumerate(groups.items(), 1)]


def write_csv(path: Path, headers: list[str], rows: list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def write_md(path: Path, title: str, headers: list[str], rows: list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {title}", "", "| " + " | ".join(headers) + " |",
             "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        cells = [str(value).replace("|", "\\|").replace("\n", " ") for value in row]
        lines.append("| " + " | ".join(cells) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def shot_number(shot: dict, index: int) -> int | str:
    return first_key(shot, ("shot", "镜号", "no", "num")) or index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从分镜 JSON 生成批次/配音对齐计划")
    parser.add_argument("--shot-file", required=True, help="分镜 JSON 路径")
    parser.add_argument("--role-file", help="角色设定 JSON 路径（可选，用于音色来源）")
    parser.add_argument("--output-dir", default=None, help="输出目录，默认分镜文件所在目录")
    parser.add_argument("--prefix", default=None, help="文件名前缀，默认取分镜文件名")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写文件")
    args = parser.parse_args(argv)

    shot_path = Path(args.shot_file).resolve()
    if not shot_path.is_file():
        print(f"分镜文件不存在: {shot_path}", file=sys.stderr)
        return 2
    try:
        meta, shots = load_shots(shot_path)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        print(f"分镜文件不可解析: {exc}", file=sys.stderr)
        return 2

    role_file = Path(args.role_file).resolve() if args.role_file else None
    roles: list[dict] = []
    if role_file is not None:
        if not role_file.is_file():
            print(f"角色文件不存在: {role_file}", file=sys.stderr)
            return 2
        try:
            roles = load_roles(role_file)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            print(f"角色文件不可解析: {exc}", file=sys.stderr)
            return 2

    batches = build_batches(shots)
    prefix = args.prefix or shot_path.stem.replace("分镜_", "计划_")
    out_dir = Path(args.output_dir).resolve() if args.output_dir else shot_path.parent

    batch_rows: list[list[Any]] = []
    voice_rows: list[list[Any]] = []
    for batch_id, items in batches:
        for shot_index, shot in enumerate(items, 1):
            duration = parse_duration(first_key(shot, ("duration", "时长"))) or 0.0
            batch_rows.append([
                batch_id,
                shot_number(shot, shot_index),
                first_key(shot, ("title", "标题", "镜头名")) or "",
                group_key(shot),
                first_key(shot, ("shot_size", "景别", "景别机位")) or "",
                first_key(shot, ("camera", "camera_move", "机位", "运镜")) or "",
                f"{duration:.1f}s",
                shot_roles(shot),
                first_key(shot, ("tail_frame_desc", "尾帧", "尾帧描述")) or "",
            ])
            for label, text in shot_texts(shot):
                chars = spoken_chars(text)
                rate = chars / duration if duration > 0 else 0.0
                voice_rows.append([
                    batch_id,
                    shot_number(shot, shot_index),
                    label,
                    text,
                    chars,
                    f"{duration:.1f}s",
                    f"{rate:.1f}字/秒",
                    rate_flag(rate),
                    "",
                    "",
                    "",
                ])

    role_rows: list[list[Any]] = []
    for index, role in enumerate(roles, 1):
        speaker, seed, instruct = voice_fields(role)
        role_rows.append([
            role.get("role") or role.get("name") or role.get("key") or f"角色#{index}",
            speaker,
            seed,
            instruct,
        ])

    summary = {
        "shot_file": str(shot_path),
        "role_file": str(role_file) if role_file else None,
        "prefix": prefix,
        "batches": len(batches),
        "shots": len(shots),
        "groups": [
            {"batch_id": batch_id, "key": group_key(items[0]) if items else "", "shots": len(items)}
            for batch_id, items in batches
        ],
        "voice_by_role": [
            {"role": row[0], "speaker": row[1], "seed": row[2], "instruct": row[3]}
            for row in role_rows
        ],
    }

    if args.dry_run:
        print(f"计划: {prefix}, 批次 {len(batches)} 个, 镜头 {len(shots)} 条")
        for batch_id, items in batches:
            print(f"  {batch_id}: {len(items)} 镜, 场景={group_key(items[0]) if items else ''}")
        for row in role_rows:
            print(f"  音色 {row[0]}: {row[1]} / seed={row[2]}")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / f"{prefix}_批次表.csv", ["批次", "镜头", "标题", "场景", "景别", "运镜", "时长", "角色", "尾帧接力"], batch_rows)
    write_md(out_dir / f"{prefix}_批次表.md", "批次表", ["批次", "镜头", "标题", "场景", "景别", "运镜", "时长", "角色", "尾帧接力"], batch_rows)
    write_csv(out_dir / f"{prefix}_配音对齐表.csv", ["批次", "镜头", "类型", "文本", "字数", "镜头时长", "预估语速", "检查", "音色", "seed", "instruct"], voice_rows)
    write_md(out_dir / f"{prefix}_配音对齐表.md", "配音对齐表", ["批次", "镜头", "类型", "文本", "字数", "镜头时长", "预估语速", "检查", "音色", "seed", "instruct"], voice_rows)
    if role_rows:
        write_csv(out_dir / f"{prefix}_音色表.csv", ["角色", "speaker", "seed", "instruct"], role_rows)
        write_md(out_dir / f"{prefix}_音色表.md", "音色表", ["角色", "speaker", "seed", "instruct"], role_rows)
    summary_path = out_dir / f"{prefix}_计划汇总.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"批次表: {out_dir / (prefix + '_批次表.csv')}")
    print(f"配音对齐表: {out_dir / (prefix + '_配音对齐表.csv')}")
    print(f"计划汇总: {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
