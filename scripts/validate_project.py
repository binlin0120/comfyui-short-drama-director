#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""质量门校验脚本（comfyui-short-drama-director）。"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

EXCLUDE_DIR_PARTS = {"_旧版", "备份", "backup", "_缓存", "预览工作区", "镜头预览"}
ROLE_PATTERNS = ("角色设定*.json", "角色_*.json", "角色*.json")
SHOT_PATTERNS = ("分镜_*.json", "分镜*.json")
MD_PATTERNS = ("分镜脚本*.md", "分镜*.md")
FINAL_MARKERS = ("最终", "终版", "定稿")

SIZE_TERMS = ("特写", "近景", "中景", "全景", "远景")
CAMERA_TERMS = (
    "推", "拉", "摇", "移", "跟", "俯", "仰", "航拍", "手持", "固定",
    "升降", "滑动", "环绕", "斯坦尼康", "转场",
)
STYLE_TOKENS = (
    "3d next-generation cg",
    "chinese anime semi-realistic style",
    "16:9 widescreen cinematic composition",
    "high quality 8k",
)
NEG_GROUPS = (
    ("文字", ("文字", "乱码", "字幕", "文本")),
    ("水印logo", ("水印", "logo", "watermark")),
    ("模糊失焦", ("模糊", "失焦", "虚焦")),
    ("变形崩坏", ("变形", "扭曲", "崩坏")),
    ("手部错误", ("手指", "手部", "手掌", "hand")),
    ("低质量", ("低质量", "画质差", "low quality")),
)
HOOK_MARKERS = ("黑场", "钩子", "下一集", "下集", "悬念", "未完待续")
REF_STATES = {"REF", "PLAN", "TODO", "TTV"}


@dataclass
class Finding:
    gate: str
    name: str
    level: str
    detail: str


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", "", str(value))


def normalize_header(text: str) -> str:
    return re.sub(r"[\s/（）()【】\[\]、,，｜|]+", "", text or "")


def read_text_smart(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("text", data, 0, len(data), "unsupported encoding")


def parse_duration(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", str(value or ""))
    return float(match.group(1)) if match else None


def first_key(shot: dict, keys: Iterable[str]) -> Any:
    for key in keys:
        value = shot.get(key)
        if value not in (None, ""):
            return value
    return None


def discover(project: Path, patterns: tuple[str, ...]) -> Path | None:
    hits: list[Path] = []
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIR_PARTS]
        for name in files:
            if any(fnmatch.fnmatch(name, pat) for pat in patterns):
                hits.append(Path(root) / name)
    if not hits:
        return None

    def score(path: Path) -> tuple[int, int]:
        final = int(any(marker in path.name for marker in FINAL_MARKERS))
        return (final, path.stat().st_mtime_ns)

    return max(hits, key=score)


def load_roles(path: Path) -> list[dict]:
    data = json.loads(read_text_smart(path))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        raise ValueError("角色设定文件顶层必须是对象或数组")
    images = data.get("images")
    if isinstance(images, list):
        return [item for item in images if isinstance(item, dict)]
    if isinstance(images, dict):
        return [dict(item, role=name) for name, item in images.items() if isinstance(item, dict)]
    if "images" not in data:
        return [dict(item, role=name) for name, item in data.items() if isinstance(item, dict)]
    raise ValueError("角色设定缺少 images 数组")


def load_shots(path: Path) -> tuple[dict, list[dict]]:
    data = json.loads(read_text_smart(path))
    if isinstance(data, list):
        return {"shots": data}, [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        raise ValueError("分镜文件顶层必须是对象或数组")
    shots = data.get("shots")
    if not isinstance(shots, list):
        raise ValueError("分镜文件缺少 shots 数组")
    return data, [item for item in shots if isinstance(item, dict)]


def parse_markdown_tables(path: Path) -> list[dict]:
    tables: list[dict] = []
    headers: list[str] | None = None
    for raw in read_text_smart(path).splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            headers = None
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{2,}:?", cell or "-") for cell in cells):
            continue
        if headers is None:
            headers = [normalize_header(cell) for cell in cells]
            continue
        row = {
            header: cells[i] if i < len(cells) else ""
            for i, header in enumerate(headers)
        }
        tables.append(row)
    return tables


def spoken_chars(text: str) -> int:
    stripped = re.sub(r"^[^:：]{1,20}[:：]", "", text or "")
    return len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", stripped))


def key_aliases(key: str) -> set[str]:
    aliases = {str(key).lower()}
    parts = re.split(r"[_\-]+", str(key))
    title = " ".join(part.capitalize() for part in parts if part)
    aliases.add(title.lower())
    aliases.add(title)
    return aliases


class ProjectValidator:
    def __init__(
        self,
        project: Path,
        roles: list[dict],
        shot_meta: dict,
        shots: list[dict],
        md_tables: list[dict],
        expected_aspect: str,
        max_roles_per_shot: int,
        strict_ref: bool = False,
    ) -> None:
        self.project = project
        self.roles = roles
        self.shot_meta = shot_meta
        self.shots = shots
        self.expected_aspect = expected_aspect
        self.max_roles_per_shot = max_roles_per_shot
        self.strict_ref = strict_ref
        self.findings: list[Finding] = []

        self.md_voices: dict[str, str] = {}
        self.md_locks: dict[str, dict[str, str]] = {}
        self._extract_markdown(md_tables)

        self.role_names: set[str] = set()
        self.alias_sets: dict[str, set[str]] = {}
        for role in roles:
            name = role.get("role") or role.get("name") or role.get("key") or ""
            key = role.get("key") or ""
            self.role_names.add(name)
            aliases = {name, str(name).lower(), key}
            if key:
                aliases |= key_aliases(key)
            self.alias_sets[name] = {alias for alias in aliases if alias}

        locks = shot_meta.get("character_locks") or {}
        self.lock_names: set[str] = set(locks.keys()) | set(self.md_locks.keys())
        for name in self.lock_names:
            if name and name not in self.alias_sets:
                self.alias_sets[name] = {name, str(name).lower()}

    def _extract_markdown(self, tables: list[dict]) -> None:
        for row in tables:
            role = (row.get("角色") or "").strip()
            if role and "音色方向" in row:
                self.md_voices[role] = (row.get("音色方向") or "").strip()
            if role and any(key in row for key in ("定妆照", "固定外观")):
                self.md_locks[role] = {
                    "image": (row.get("定妆照") or "").strip(),
                    "appearance": (row.get("固定外观") or "").strip(),
                }

    def _add(self, gate: str, name: str, level: str, detail: str) -> None:
        self.findings.append(Finding(gate=gate, name=name, level=level, detail=detail))

    def _fail(self, gate: str, name: str, detail: str) -> None:
        self._add(gate, name, "FAIL", detail)

    def _warn(self, gate: str, name: str, detail: str) -> None:
        self._add(gate, name, "WARN", detail)

    def _role_name(self, role: dict, index: int) -> str:
        return role.get("role") or role.get("name") or role.get("key") or f"角色#{index}"

    def _shot_prompt(self, shot: dict) -> str:
        return str(first_key(shot, ("prompt", "prompt_en", "画面提示词", "AI提示词")) or "")

    def _shot_text_parts(self, shot: dict) -> list[str]:
        parts: list[str] = []
        for key in ("vo_cn", "vo", "旁白", "narration_cn"):
            value = shot.get(key)
            if isinstance(value, list):
                parts.extend(str(item) for item in value if item)
            elif value:
                parts.append(str(value))
        for key in ("dialogue_cn", "dialogue", "台词", "对白"):
            value = shot.get(key)
            if isinstance(value, list):
                parts.extend(str(item) for item in value if item)
            elif value:
                parts.append(str(value))
        return parts

    def _detect_roles(self, shot: dict) -> list[str]:
        prompt = self._shot_prompt(shot)
        haystack = prompt.lower()
        for value in self._shot_text_parts(shot):
            haystack += " " + clean_text(value).lower()
        haystack += " " + clean_text(shot.get("title")).lower()
        found = []
        for name, aliases in self.alias_sets.items():
            if any(alias and alias.lower() in haystack for alias in aliases):
                found.append(name)
        return found

    def check_g02_role_cards(self) -> None:
        if not self.roles:
            self._fail("G02", "角色表", "未找到任何角色卡片")
            return
        for i, role in enumerate(self.roles, 1):
            name = self._role_name(role, i)
            missing = []
            if not (role.get("role") or role.get("name")):
                missing.append("role/name")
            for key in ("key", "prompt", "negative_prompt", "output"):
                if role.get(key) in (None, ""):
                    missing.append(key)
            seed = role.get("seed")
            seed_ok = (
                isinstance(seed, (int, float)) and float(seed) > 0
            ) or (
                isinstance(seed, str) and bool(re.fullmatch(r"\d+", seed.strip()))
            )
            if not seed_ok:
                missing.append("seed")
            if missing:
                self._fail("G02", f"角色卡片:{name}", f"缺少字段: {', '.join(missing)}")
            status = str(role.get("status") or "").lower()
            if status and "final" not in status and "锁定" not in status:
                self._warn("G02", f"角色状态:{name}", f"status={role.get('status')}，建议标记为最终/锁定")
            elif not status:
                self._warn("G02", f"角色状态:{name}", "未标记 status，建议最终版角色补 final/锁定 标记")

    def check_g03_uniqueness(self) -> None:
        names: dict[str, str] = {}
        keys: dict[str, str] = {}
        seeds: dict[str, str] = {}
        for i, role in enumerate(self.roles, 1):
            name = self._role_name(role, i)
            key = str(role.get("key") or "")
            seed = role.get("seed")
            if name in names:
                self._fail("G03", f"角色名重复:{name}", f"与 {names[name]} 冲突")
            else:
                names[name] = f"角色#{i}"
            if key:
                if key in keys:
                    self._fail("G03", f"角色key重复:{key}", f"与 {keys[key]} 冲突")
                else:
                    keys[key] = name
                if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
                    self._warn("G03", f"角色key格式:{key}", "建议使用 snake_case（如 lin_yu）")
            if seed is not None:
                seed_key = str(seed)
                if seed_key in seeds:
                    self._fail("G03", f"seed重复:{seed}", f"{name} 与 {seeds[seed_key]} 使用相同 seed")
                else:
                    seeds[seed_key] = name

    def check_g04_portraits(self) -> None:
        for i, role in enumerate(self.roles, 1):
            name = self._role_name(role, i)
            raw = role.get("output") or ""
            if not raw:
                self._fail("G04", f"定妆照:{name}", "缺少 output 字段")
                continue
            path = Path(raw)
            if not path.is_absolute():
                path = self.project / path
            if not path.exists():
                self._fail("G04", f"定妆照:{name}", f"文件不存在: {path}")
            elif path.stat().st_size <= 0:
                self._fail("G04", f"定妆照:{name}", f"文件为空: {path}")

    def check_g05_voice(self) -> None:
        seen_pairs: set[tuple[str, str]] = set()
        for i, role in enumerate(self.roles, 1):
            name = self._role_name(role, i)
            voice = role.get("voice")
            tts = role.get("tts")
            speaker = role.get("speaker")
            md_voice = self.md_voices.get(name)
            has_voice = any(value not in (None, "", {}) for value in (voice, tts, speaker))
            if not has_voice and not md_voice:
                self._warn("G05", f"音色:{name}", "缺少 voice/tts/speaker 字段，且分镜表音色方向列为空")
                continue
            explicit_speaker = None
            explicit_seed = None
            if isinstance(voice, dict):
                explicit_speaker = voice.get("speaker") or voice.get("voice") or voice.get("name")
                explicit_seed = voice.get("seed")
            elif isinstance(voice, str) and voice:
                explicit_speaker = voice
            if explicit_speaker is None:
                explicit_speaker = speaker
            if explicit_speaker is not None and explicit_seed is not None:
                pair = (str(explicit_speaker).strip(), str(explicit_seed).strip())
                if pair in seen_pairs:
                    self._warn("G05", f"音色冲突:{name}", f"多个角色共用 speaker={pair[0]} seed={pair[1]}")
                seen_pairs.add(pair)

    def check_g06_character_locks(self) -> None:
        if not self.roles:
            return
        for i, role in enumerate(self.roles, 1):
            name = self._role_name(role, i)
            if name not in self.lock_names:
                self._fail("G06", f"角色锁定:{name}", "角色未出现在分镜 character_locks 或分镜表角色锁定卡")

    def check_g07_shot_numbers(self) -> None:
        numbers: list[int] = []
        for i, shot in enumerate(self.shots, 1):
            value = first_key(shot, ("shot", "镜号", "no", "num"))
            if value is None:
                self._warn("G07", f"镜头#{i}", "缺少 shot 编号，已按数组顺序生成编号")
                value = i
            try:
                numbers.append(int(value))
            except (TypeError, ValueError):
                self._fail("G07", f"镜头#{i}", f"shot 编号不是数字: {value}")
        if not numbers:
            self._fail("G07", "分镜表", "没有任何镜头")
            return
        expected = list(range(1, len(numbers) + 1))
        if numbers != expected:
            self._fail("G07", "分镜编号", f"编号应为 1..{len(numbers)}，实际: {numbers}")

    def check_g08_shot_fields(self) -> None:
        if not self.shots:
            self._fail("G08", "分镜表", "没有任何镜头")
            return
        required = (
            ("title", ("title", "标题", "镜头名"), "标题"),
            ("duration", ("duration", "时长"), "时长"),
            ("shot_size", ("shot_size", "景别", "景别机位"), "景别"),
            ("camera", ("camera", "camera_move", "机位", "运镜"), "机位/运镜"),
            ("action", ("action", "动作", "画面内容", "人物动作"), "动作/画面内容"),
            ("prompt", ("prompt", "prompt_en", "画面提示词", "AI提示词"), "画面提示词"),
        )
        for i, shot in enumerate(self.shots, 1):
            for field, keys, label in required:
                if first_key(shot, keys) is None:
                    self._fail("G08", f"镜头#{i}", f"缺少字段: {label}")
        fps = self.shot_meta.get("fps")
        if fps is None:
            self._warn("G08", "fps", "顶层未声明 fps，建议写明 30fps")
        else:
            try:
                if int(fps) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                self._fail("G08", "fps", f"fps 不是正整数: {fps}")

    def check_g09_duration(self) -> None:
        for i, shot in enumerate(self.shots, 1):
            duration = parse_duration(first_key(shot, ("duration", "时长")))
            if duration is None:
                self._fail("G09", f"镜头#{i}", "时长无法解析")
            elif duration < 3 or duration > 8:
                self._fail("G09", f"镜头#{i}", f"时长 {duration}s 超出 3-8 秒范围")
            elif duration > 6:
                self._warn("G09", f"镜头#{i}", f"时长 {duration}s 建议压缩到 6 秒以内以保持节奏")

    def check_g10_total_duration(self) -> None:
        total = sum(
            parse_duration(first_key(shot, ("duration", "时长"))) or 0
            for shot in self.shots
        )
        if total > 180:
            self._fail("G10", "总时长", f"单集 {total:.0f}s 超过 180 秒，建议拆分")
        elif total > 120:
            self._warn("G10", "总时长", f"单集 {total:.0f}s 超过 120 秒，节奏风险")
        declared = self.shot_meta.get("total_seconds")
        if declared is None:
            self._warn("G10", "总时长", "顶层未声明 total_seconds")
        else:
            declared_num = parse_duration(declared)
            if declared_num is None:
                self._warn("G10", "总时长", f"total_seconds 无法解析: {declared}")
            elif abs(total - declared_num) > max(2.0, declared_num * 0.05):
                self._warn("G10", "总时长", f"镜头时长合计 {total:.1f}s 与 total_seconds={declared_num} 偏差过大")

    def check_g11_shot_size(self) -> None:
        for i, shot in enumerate(self.shots, 1):
            value = str(first_key(shot, ("shot_size", "景别", "景别机位")) or "")
            if not value:
                continue
            if not any(term in value for term in SIZE_TERMS):
                self._warn("G11", f"镜头#{i}", f"景别无法识别: {value}，建议写 特写/近景/中景/全景/远景")

    def check_g12_camera(self) -> None:
        for i, shot in enumerate(self.shots, 1):
            value = str(first_key(shot, ("camera", "camera_move", "机位", "运镜")) or "")
            if not value:
                continue
            if not any(term in value for term in CAMERA_TERMS):
                self._warn("G12", f"镜头#{i}", f"运镜无法识别: {value}，建议写 推/拉/摇/移/跟/固定/手持 等")

    def check_g13_voice_speed(self) -> None:
        for i, shot in enumerate(self.shots, 1):
            duration = parse_duration(first_key(shot, ("duration", "时长"))) or 0.0
            parts = self._shot_text_parts(shot)
            chars = sum(spoken_chars(part) for part in parts if part)
            if not chars or duration <= 0:
                continue
            rate = chars / duration
            if rate > 8:
                self._fail("G13", f"镜头#{i}", f"口播密度 {rate:.1f} 字/秒（{chars}字/{duration}s），语速太快")
            elif rate > 6:
                self._warn("G13", f"镜头#{i}", f"口播密度 {rate:.1f} 字/秒（{chars}字/{duration}s），注意自然语速")

    def check_g14_text_on_screen(self) -> None:
        for i, shot in enumerate(self.shots, 1):
            text = str(first_key(shot, ("text_on_screen", "文字画面", "屏幕文字")) or "")
            prompt = self._shot_prompt(shot).lower()
            no_text_marker = "no text" in prompt
            if clean_text(text):
                if clean_text(text) not in clean_text(prompt):
                    self._fail("G14", f"镜头#{i}", f"屏幕文字未写入提示词: {text}")
                if no_text_marker:
                    self._fail("G14", f"镜头#{i}", "有 text_on_screen 的镜头提示词里不能同时出现 no text")
            elif not no_text_marker:
                self._warn("G14", f"镜头#{i}", "无文字画面的镜头建议提示词末尾加 no text")

    def check_g15_character_consistency(self) -> None:
        for i, shot in enumerate(self.shots, 1):
            prompt = self._shot_prompt(shot)
            detected = self._detect_roles(shot)
            for name in detected:
                if name not in self.lock_names:
                    self._fail("G15", f"镜头#{i}", f"出现未锁定角色: {name}")
            if detected and "from final portrait" not in prompt.lower():
                self._warn(
                    "G15", f"镜头#{i}",
                    f"涉及角色 {', '.join(detected)}，提示词建议补 from final portrait reference",
                )

    def check_g16_aspect(self) -> None:
        declared = self.shot_meta.get("aspect")
        if declared is None:
            self._warn("G16", "画幅", "顶层未声明 aspect")
            return
        declared_text = str(declared)
        expected = self.expected_aspect
        if expected not in declared_text and declared_text not in expected:
            self._fail("G16", "画幅", f"设定 {declared_text}，与目标 {expected} 不一致")

    def check_g17_style_block(self) -> None:
        for i, shot in enumerate(self.shots, 1):
            prompt = self._shot_prompt(shot).lower()
            if not prompt:
                continue
            missing = [token for token in STYLE_TOKENS if token not in prompt]
            if len(missing) == len(STYLE_TOKENS):
                self._fail("G17", f"镜头#{i}", "提示词没有统一画风风格块")
            elif missing:
                self._warn("G17", f"镜头#{i}", "风格块不完整，缺少: " + ", ".join(missing))

    def check_g18_negative_prompt(self) -> None:
        negative = self.shot_meta.get("negative_prompt")
        if not negative and self.roles:
            negative = self.roles[0].get("negative_prompt")
        if not negative:
            self._fail("G18", "负面提示词", "未找到统一负面提示词")
            return
        lower = str(negative).lower()
        for group, terms in NEG_GROUPS:
            if not any(term.lower() in lower for term in terms):
                self._fail("G18", f"负面提示词:{group}", f"缺少 {group} 防护词")

    def check_g19_open_and_hook(self) -> None:
        if not self.shots:
            return
        first_shot = self.shots[0]
        first_text = " ".join(
            str(first_key(first_shot, keys) or "")
            for keys in (("transition", "转场"), ("title", "标题"), ("action", "动作", "画面内容"))
        )
        if not any(term in first_text for term in ("淡入", "淡出", "开场")):
            self._warn("G19", "第一镜", f"开场缺少 淡入/开场 标记: {first_text[:60]}")
        last_shot = self.shots[-1]
        last_text = " ".join(
            str(first_key(last_shot, keys) or "")
            for keys in (
                ("transition", "转场"),
                ("text_on_screen", "文字画面", "屏幕文字"),
                ("action", "动作", "画面内容"),
                ("title", "标题"),
                ("sfx", "sfx_cn", "音效"),
            )
        )
        if not any(marker in last_text for marker in HOOK_MARKERS):
            self._fail("G19", "最后一镜", "结尾缺少钩子标记（黑场/钩子/下一集/悬念/未完待续）")

    def check_g20_roles_per_shot(self) -> None:
        for i, shot in enumerate(self.shots, 1):
            detected = self._detect_roles(shot)
            if len(detected) > self.max_roles_per_shot:
                self._warn(
                    "G20", f"镜头#{i}",
                    f"单镜角色 {len(detected)} 人（{', '.join(detected)}），建议不超过 {self.max_roles_per_shot} 人",
                )

    def check_g21_ref_states(self) -> None:
        for i, role in enumerate(self.roles, 1):
            name = self._role_name(role, i)
            state = role.get("ref_state")
            if state is None:
                if self.strict_ref:
                    self._fail("G21", f"角色:{name}", "缺少 ref_state，参考图未锁不得量产")
                continue
            key = str(state).strip().upper()
            if key not in REF_STATES:
                self._warn("G21", f"角色:{name}", f"ref_state={state}，应为 REF/PLAN/TODO/TTV")
            elif key in ("PLAN", "TODO") and self.strict_ref:
                self._fail(
                    "G21", f"角色:{name}",
                    f"ref_state={state} 未锁定，量产前必须改为 REF 或声明 TTV",
                )
        for i, shot in enumerate(self.shots, 1):
            value = first_key(shot, ("ref_state", "参考状态", "参考图状态"))
            if value is None:
                if self.strict_ref:
                    self._fail("G21", f"镜头#{i}", "缺少 ref_state，参考图未锁不得量产")
                continue
            key = str(value).strip().upper()
            if key not in REF_STATES:
                self._warn("G21", f"镜头#{i}", f"ref_state={value}，应为 REF/PLAN/TODO/TTV")
            elif key in ("PLAN", "TODO") and self.strict_ref:
                self._fail(
                    "G21", f"镜头#{i}",
                    f"ref_state={value} 未锁定，量产前必须改为 REF 或声明 TTV",
                )

    def check_g22_tail_frames(self) -> None:
        for i, shot in enumerate(self.shots, 1):
            tail = first_key(shot, ("tail_frame_desc", "尾帧", "尾帧描述"))
            if tail is None:
                if self.strict_ref:
                    self._warn("G22", f"镜头#{i}", "缺少尾帧描述，批量前建议补尾帧接力信息")
                continue
            if not str(tail).strip():
                self._warn("G22", f"镜头#{i}", "尾帧描述为空")

    def run(self) -> list[Finding]:
        checkers = (
            self.check_g02_role_cards,
            self.check_g03_uniqueness,
            self.check_g04_portraits,
            self.check_g05_voice,
            self.check_g06_character_locks,
            self.check_g07_shot_numbers,
            self.check_g08_shot_fields,
            self.check_g09_duration,
            self.check_g10_total_duration,
            self.check_g11_shot_size,
            self.check_g12_camera,
            self.check_g13_voice_speed,
            self.check_g14_text_on_screen,
            self.check_g15_character_consistency,
            self.check_g16_aspect,
            self.check_g17_style_block,
            self.check_g18_negative_prompt,
            self.check_g19_open_and_hook,
            self.check_g20_roles_per_shot,
            self.check_g21_ref_states,
            self.check_g22_tail_frames,
        )
        for checker in checkers:
            checker()
        return self.findings


def resolve_input(
    project: Path,
    value: str | None,
    patterns: tuple[str, ...],
    kind: str,
) -> Path | None:
    if value:
        path = Path(value)
        if not path.is_absolute():
            path = project / path
        if not path.exists():
            print(f"[G01] {kind} 文件不存在: {path}", file=sys.stderr)
            return None
        return path
    found = discover(project, patterns)
    if found is None:
        print(f"[G01] 在项目目录未找到 {kind} 文件（{patterns[0]} 等）", file=sys.stderr)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="短剧项目质量门校验")
    parser.add_argument("--project", default=".", help="项目目录，默认当前目录")
    parser.add_argument("--role-file", help="角色设定 JSON 路径（默认自动发现）")
    parser.add_argument("--shot-file", help="分镜 JSON 路径（默认自动发现）")
    parser.add_argument("--storyboard-file", help="分镜 Markdown 路径（可选）")
    parser.add_argument("--aspect", default="16:9", help="目标画幅，默认 16:9")
    parser.add_argument("--max-roles", type=int, default=3, help="单镜最多角色，默认 3")
    parser.add_argument(
        "--strict-ref",
        action="store_true",
        help="强制要求角色/镜头 ref_state=REF（或 TTV）并提醒尾帧描述，适合新项目量产门",
    )
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args(argv)

    project = Path(args.project).resolve()
    if not project.is_dir():
        print(f"[G01] 项目目录不存在: {project}", file=sys.stderr)
        return 2

    role_file = resolve_input(project, args.role_file, ROLE_PATTERNS, "角色设定")
    shot_file = resolve_input(project, args.shot_file, SHOT_PATTERNS, "分镜")
    if role_file is None or shot_file is None:
        return 2

    try:
        roles = load_roles(role_file)
        shot_meta, shots = load_shots(shot_file)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        print(f"[G01] 输入文件不可解析: {exc}", file=sys.stderr)
        return 2

    md_file = None
    md_tables: list[dict] = []
    if args.storyboard_file:
        md_file = resolve_input(project, args.storyboard_file, MD_PATTERNS, "分镜Markdown")
        if md_file is None:
            return 2
    else:
        md_file = discover(project, MD_PATTERNS)
        if md_file is None:
            print("[G01] 未找到分镜 Markdown，音色/锁定信息只能依赖 JSON", file=sys.stderr)
    if md_file is not None:
        try:
            md_tables = parse_markdown_tables(md_file)
        except (UnicodeDecodeError, OSError) as exc:
            print(f"[G01] 分镜 Markdown 不可解析: {exc}", file=sys.stderr)
            return 2

    validator = ProjectValidator(
        project=project,
        roles=roles,
        shot_meta=shot_meta,
        shots=shots,
        md_tables=md_tables,
        expected_aspect=args.aspect,
        max_roles_per_shot=args.max_roles,
        strict_ref=args.strict_ref,
    )
    findings = validator.run()
    fails = [item for item in findings if item.level == "FAIL"]
    warns = [item for item in findings if item.level == "WARN"]

    if args.json:
        result = {
            "project": str(project),
            "role_file": str(role_file),
            "shot_file": str(shot_file),
            "storyboard_file": str(md_file) if md_file else None,
            "findings": [asdict(item) for item in findings],
            "summary": {"fail": len(fails), "warn": len(warns)},
            "ok": not fails,
            "exit_code": 1 if fails else 0,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result["exit_code"]

    if findings:
        print("质量门结果：")
        for item in findings:
            print(f"  [{item.gate}] {item.level} {item.name}: {item.detail}")
    else:
        print("质量门结果：未发现任何问题")
    print()
    print(f"FAIL: {len(fails)}  WARN: {len(warns)}")
    if fails:
        print("结论：未通过。修复所有 FAIL 项后再批量生成。")
        return 1
    print("结论：通过（WARN 项可放行，但建议一并修）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
