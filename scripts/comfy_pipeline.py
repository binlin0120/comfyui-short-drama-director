#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""One-click pipeline for three ComfyUI workflows.

Flattens ComfyUI Desktop JSON workflows (including subgraph nodes) into API
prompts and runs them sequentially:

    stage1: ZImage_Turbo_INT8.json          text -> base image
    stage2: Flux2_Klein_4B_Image_Edit.json  base image + edit prompt -> image
    stage3: Wan22_5B_TI2V_GGUF.json         edited image + prompt -> video

Each stage is submitted as an independent /prompt request, so models are loaded
one stage at a time instead of sharing one graph (the 3-in-1 canvas approach
would exceed the available VRAM).
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


BASE_DIR = Path(__file__).resolve().parent
SKILL_ROOT = BASE_DIR.parent
DEFAULT_WF_DIR = Path(
    os.environ.get("COMFYUI_WORKFLOWS_DIR", str(SKILL_ROOT / "assets" / "workflows"))
)
DEFAULT_SHARED = Path(os.environ.get("COMFYUI_SHARED_DIR", "ComfyUI-Shared"))

WF_STAGE1 = DEFAULT_WF_DIR / "ZImage_Turbo_INT8.json"
WF_STAGE2 = DEFAULT_WF_DIR / "Flux2_Klein_4B_Image_Edit.json"
WF_STAGE3 = DEFAULT_WF_DIR / "Wan22_5B_TI2V_GGUF.json"

DEFAULT_POS = (
    "Dramatic black and white high fashion studio portrait, close-up bust shot, "
    "pale platinum blonde woman with sleek low ponytail, head tilted upward, "
    "eyes softly closed, wearing a fitted black turtleneck top. A large "
    "translucent pale white butterfly hovers gently right at her lips, delicate "
    "detailed wing veins visible. Hard rim light creates glowing bright white "
    "halo around her hair and face, deep inky pure black minimalist background, "
    "stark high contrast chiaroscuro lighting, film grain texture, moody "
    "ethereal atmosphere, monochrome, editorial fashion photography"
)
DEFAULT_EDIT = "Change the bag color to blue."
DEFAULT_NEG = (
    "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，"
    "最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，"
    "画得不好的脸部，画得不好的眼睛，变形，模糊，失焦，水印，文字"
)
DEFAULT_VIDEO_POS = (
    "Low contrast. In a retro 1970s-style subway station, a street musician "
    "plays in dim colorful light, camera slowly zooming in."
)

MODE_ALWAYS = 0
MODE_NEVER = 2
MODE_BYPASS = 4
SENTINEL_IN = -10
SENTINEL_OUT = -20


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _request(
    method: str,
    url: str,
    body: Optional[bytes] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: float = 30.0,
) -> Any:
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            detail = json.loads(data)
        except Exception:
            detail = data.decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"HTTP {e.code} {method} {url}: {detail!r}") from e
    if not data:
        return None
    try:
        return json.loads(data)
    except Exception:
        return data.decode("utf-8", errors="replace")


def api(base: str, path: str, timeout: float = 30.0) -> Any:
    return _request("GET", base.rstrip("/") + "/" + path.lstrip("/"), timeout=timeout)


def post_prompt(base: str, prompt: dict[str, Any]) -> str:
    data = json.dumps(
        {"prompt": prompt, "client_id": "comfy_pipeline"}, ensure_ascii=False
    ).encode("utf-8")
    res = _request(
        "POST",
        base.rstrip("/") + "/prompt",
        data,
        {"Content-Type": "application/json"},
    )
    if isinstance(res, dict) and res.get("prompt_id"):
        return str(res["prompt_id"])
    raise RuntimeError(f"/prompt did not return a prompt_id: {res!r}")


def history(base: str, prompt_id: str) -> dict[str, Any]:
    res = api(base, f"history/{prompt_id}")
    return res if isinstance(res, dict) else {}


def wait_prompt(
    base: str,
    prompt_id: str,
    interval: float = 3.0,
    timeout_sec: float = 7200.0,
) -> dict[str, Any]:
    wait = 1.0
    start = time.monotonic()
    while time.monotonic() - start < timeout_sec:
        item = history(base, prompt_id).get(prompt_id)
        if item is not None:
            status = item.get("status", {}) or {}
            if status.get("completed") or status.get("status_str") in (
                "success",
                "completed",
            ):
                return item
            if status.get("status_str") in ("error", "failed"):
                raise RuntimeError(
                    f"Prompt {prompt_id} failed: {json.dumps(item, ensure_ascii=False)}"
                )
        time.sleep(wait)
        wait = min(wait * 1.4, interval)
    raise TimeoutError(f"Prompt {prompt_id} did not finish in {timeout_sec:.0f}s")


@dataclass
class Scope:
    """One root graph or one expanded subgraph instantiation."""

    node_map: dict[str, dict[str, Any]]
    links_index: dict[int, dict[str, Any]]
    subgraph_id: Optional[str] = None
    parent: Optional["Scope"] = None
    instance_id: Optional[str] = None
    subgraph_def: Optional[dict[str, Any]] = None
    defs_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def scope_key(self) -> str:
        if self.parent is None:
            return "root"
        return (
            f"{self.parent.scope_key}:{self.instance_id}"
            if self.parent.scope_key != "root"
            else str(self.instance_id)
        )

    def get_node(self, node_id: Any) -> Optional[dict[str, Any]]:
        return self.node_map.get(str(node_id))

    def get_child(self, node_id: Any) -> Optional["Scope"]:
        node = self.get_node(node_id)
        if not node:
            return None
        subgraph_id = str(node.get("type"))
        subgraph_def = self.defs_by_id.get(subgraph_id)
        if not subgraph_def:
            return None
        return scope_from_subgraph(subgraph_def, self, str(node_id))


def normalize_link(raw: Any) -> Optional[dict[str, Any]]:
    if isinstance(raw, dict):
        return {
            "id": raw.get("id"),
            "origin_id": raw.get("origin_id"),
            "origin_slot": raw.get("origin_slot"),
            "target_id": raw.get("target_id"),
            "target_slot": raw.get("target_slot"),
            "type": raw.get("type"),
        }
    if isinstance(raw, (list, tuple)) and len(raw) >= 6:
        return {
            "id": raw[0],
            "origin_id": raw[1],
            "origin_slot": raw[2],
            "target_id": raw[3],
            "target_slot": raw[4],
            "type": raw[5],
        }
    return None


def node_is_active(node: dict[str, Any]) -> bool:
    mode = node.get("mode")
    return mode is None or int(mode) == MODE_ALWAYS


def collect_subgraph_defs(root_defs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}

    def walk(defs: Iterable[dict[str, Any]]) -> None:
        for d in defs:
            if not isinstance(d, dict):
                continue
            sid = str(d.get("id"))
            if sid and sid not in out:
                out[sid] = d
                walk(d.get("definitions", {}).get("subgraphs", []) or [])

    walk(root_defs)
    return out


def scope_from_subgraph(
    subgraph_def: dict[str, Any], parent: Scope, instance_id: str
) -> Scope:
    nodes = {}
    for n in subgraph_def.get("nodes", []) or []:
        nodes[str(n.get("id"))] = n
    links_index = {}
    for raw in subgraph_def.get("links", []) or []:
        link = normalize_link(raw)
        if link and link.get("id") is not None:
            try:
                links_index[int(link["id"])] = link
            except (TypeError, ValueError):
                pass
    return Scope(
        node_map=nodes,
        links_index=links_index,
        subgraph_id=str(subgraph_def.get("id")),
        parent=parent,
        instance_id=str(instance_id),
        subgraph_def=subgraph_def,
        defs_by_id=parent.defs_by_id,
    )


def iter_scopes(root: dict[str, Any]) -> Iterable[Scope]:
    """Depth-first expansion of active subgraph instances."""

    nodes = {}
    for n in root.get("nodes", []) or []:
        nodes[str(n.get("id"))] = n
    links_index = {}
    for raw in root.get("links", []) or []:
        link = normalize_link(raw)
        if link and link.get("id") is not None:
            try:
                links_index[int(link["id"])] = link
            except (TypeError, ValueError):
                pass
    defs_by_id = collect_subgraph_defs(
        root.get("definitions", {}).get("subgraphs", []) or []
    )
    root_scope = Scope(node_map=nodes, links_index=links_index, defs_by_id=defs_by_id)
    yield root_scope

    def emit(scope: Scope) -> Iterable[Scope]:
        ordered = sorted(
            scope.node_map,
            key=lambda x: (
                not str(x).lstrip("-").isdigit(),
                int(str(x)) if str(x).lstrip("-").isdigit() else 0,
            ),
        )
        for node_id in ordered:
            node = scope.node_map[node_id]
            if not node_is_active(node):
                continue
            child = scope.get_child(node_id)
            if child:
                yield child
                yield from emit(child)

    yield from emit(root_scope)


def exec_path(scope: Scope, node_id: Any) -> str:
    """Full unique execution id, e.g. root '9' -> '9', inner '64' -> '75:64'."""
    if scope.parent is None:
        return str(node_id)
    return f"{scope.instance_id}:{node_id}"


def instance_widget_value(scope: Scope, input_index: int) -> tuple[bool, Any]:
    """Resolve a promoted widget value on the subgraph's parent instance.

    Parent ``widgets_values`` only contains values for widget-driven inputs
    that are not connected at the parent, so positions are counted after
    skipping connected input names.
    """
    parent = scope.parent
    if parent is None or not scope.subgraph_def:
        return False, None
    instance = parent.get_node(scope.instance_id)
    if not instance:
        return False, None
    inputs_def = scope.subgraph_def.get("inputs", []) or []
    name = None
    if 0 <= input_index < len(inputs_def):
        name = str(inputs_def[input_index].get("name"))
    named = instance.get("widgets_values_named")
    if isinstance(named, dict) and name is not None and name in named:
        return True, named[name]

    connected = set()
    for inp in instance.get("inputs", []) or []:
        if isinstance(inp, dict) and inp.get("name") and inp.get("link") is not None:
            connected.add(str(inp.get("name")))

    vals = instance.get("widgets_values") or []
    widget_index = -1
    for i, inp_def in enumerate(inputs_def):
        if not isinstance(inp_def, dict):
            continue
        nm = str(inp_def.get("name"))
        if nm in connected:
            continue
        widget_index += 1
        if i == input_index:
            if widget_index < len(vals):
                return True, vals[widget_index]
            return False, None
    return False, None


def resolve_boundary_input(
    scope: Scope, input_index: int, seen: Optional[set[str]] = None
) -> Optional[tuple[bool, Any]]:
    """Resolve a promoted subgraph input to a widget value or parent link."""
    parent = scope.parent
    if parent is None or not scope.subgraph_def:
        return None
    inputs_def = scope.subgraph_def.get("inputs", []) or []
    name = None
    if 0 <= input_index < len(inputs_def):
        name = str(inputs_def[input_index].get("name"))
    instance = parent.get_node(scope.instance_id)
    if instance is not None and name is not None:
        for inp in instance.get("inputs", []) or []:
            if not isinstance(inp, dict) or str(inp.get("name")) != name:
                continue
            link_id = inp.get("link")
            if link_id is None:
                break
            link = parent.links_index.get(int(link_id))
            if link is not None:
                return resolve_link(parent, link, seen)
            break
    found, value = instance_widget_value(scope, input_index)
    return (False, value) if found else None


def resolve_link(
    scope: Scope, link: dict[str, Any], seen: Optional[set[str]] = None
) -> Optional[tuple[bool, Any]]:
    """Tunnel a link to a concrete (exec_id, slot) or widget value."""
    origin_id = link.get("origin_id")
    origin_slot = int(link.get("origin_slot", 0))
    if origin_id is None:
        return None
    if int(origin_id) == SENTINEL_IN:
        return resolve_boundary_input(scope, origin_slot, seen)
    if int(origin_id) == SENTINEL_OUT:
        return resolve_subgraph_output(scope, origin_slot, seen)
    child = scope.get_child(origin_id)
    if child is not None:
        return resolve_subgraph_output(child, origin_slot, seen)
    return True, (exec_path(scope, origin_id), origin_slot)


def resolve_subgraph_output(
    scope: Scope, slot: int, seen: Optional[set[str]] = None
) -> Optional[tuple[bool, Any]]:
    """Resolve a subgraph output slot to the concrete producer node.

    Returns (is_link, value). For widget outputs value is the promoted value;
    for node outputs value is (execution_id, slot_index).
    """
    seen = seen or set()
    key = f"{scope.scope_key}:o{slot}"
    if key in seen:
        raise RuntimeError(f"Circular subgraph output resolution at {key}")
    seen.add(key)

    d = scope.subgraph_def
    if d is None:
        return None
    outputs = d.get("outputs", []) or []
    if slot >= len(outputs):
        return None
    for raw in d.get("links", []) or []:
        link = normalize_link(raw)
        if not link:
            continue
        if int(link.get("target_id", 0)) != SENTINEL_OUT:
            continue
        if int(link.get("target_slot", 0)) != slot:
            continue
        return resolve_link(scope, link, seen)
    return None


def resolve_input(
    scope: Scope, node_id: Any, slot: int, seen: Optional[set[str]] = None
) -> Optional[tuple[bool, Any]]:
    """Return (is_link, value); is_link True means (origin_exec_id, origin_slot)."""
    seen = seen or set()
    key = f"{scope.scope_key}:{node_id}:i{slot}"
    if key in seen:
        raise RuntimeError(f"Circular link resolution at {key}")
    seen.add(key)

    node = scope.get_node(node_id)
    if not node:
        return None
    inputs = node.get("inputs", []) or []
    if slot >= len(inputs):
        return None
    inp = inputs[slot]
    link_id = inp.get("link")
    if link_id is None:
        return None
    link = scope.links_index.get(int(link_id))
    if link is None:
        return None
    return resolve_link(scope, link, seen)


def skip_widget_name(name: str) -> bool:
    n = str(name)
    return (
        n.startswith("control_after_")
        or n == "upload"
        or n.startswith("control-after")
    )


WIDGET_TYPES = ("COMBO", "INT", "FLOAT", "STRING", "BOOLEAN")
WIDGET_TYPE_PREFIXES = ("COMFY_DYNAMICCOMBO",)


def _is_widget_type(spec: Any) -> bool:
    if not isinstance(spec, list) or not spec:
        return False
    wtype = spec[0]
    if isinstance(wtype, str):
        return wtype in WIDGET_TYPES or wtype.startswith(WIDGET_TYPE_PREFIXES)
    if isinstance(wtype, list):
        return all(isinstance(x, (str, int, float, bool)) for x in wtype)
    return False


def ordered_widget_names(
    object_info: Optional[dict[str, Any]], class_type: str
) -> Optional[list[str]]:
    """Ordered widget input names from /object_info for a node class."""
    if not object_info:
        return None
    info = object_info.get(str(class_type))
    if not isinstance(info, dict):
        return None
    input_def = info.get("input", {}) or {}
    names: list[str] = []
    for group in ("required", "optional"):
        group_def = input_def.get(group) or {}
        for name, spec in group_def.items():
            if _is_widget_type(spec):
                names.append(str(name))
    return names or None


def build_prompt(
    root: dict[str, Any],
    widget_overrides: Optional[dict[str, dict[str, Any]]] = None,
    object_info: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Flatten root + subgraphs into a ComfyUI /prompt payload."""
    widget_overrides = widget_overrides or {}
    scopes = list(iter_scopes(root))

    prompt: dict[str, Any] = {}
    emitted: set[str] = set()

    for scope in scopes:
        for node_id, node in scope.node_map.items():
            if not node_is_active(node):
                continue
            exec_id = exec_path(scope, node_id)
            type_name = node.get("type")
            if not isinstance(type_name, str) or type_name in scope.defs_by_id:
                continue
            if type_name in ("MarkdownNote", "Note"):
                continue
            if not (node.get("inputs") or node.get("outputs")):
                continue

            inputs: dict[str, Any] = {}
            named = node.get("widgets_values_named")
            if not isinstance(named, dict):
                named = None
            value_list = node.get("widgets_values") or []
            inp_list = node.get("inputs", []) or []
            connected = {
                str(inp.get("name"))
                for inp in inp_list
                if isinstance(inp, dict) and inp.get("link") is not None
            }

            ordered = ordered_widget_names(object_info, type_name)
            if ordered is not None:
                if named is not None:
                    for wname in ordered:
                        if skip_widget_name(wname) or wname in connected:
                            continue
                        if named.get(wname) is not None:
                            inputs[wname] = named[wname]
                else:
                    for i, wname in enumerate(ordered):
                        if skip_widget_name(wname) or wname in connected:
                            continue
                        if i < len(value_list) and value_list[i] is not None:
                            inputs[wname] = value_list[i]
            else:
                widget_slots: list[tuple[int, str, str]] = []
                for slot_idx, inp in enumerate(inp_list):
                    if not isinstance(inp, dict):
                        continue
                    wname = None
                    if isinstance(inp.get("widget"), dict):
                        wname = inp["widget"].get("name") or inp.get("name")
                    if wname:
                        widget_slots.append((slot_idx, str(inp.get("name")), str(wname)))

                def widget_value(slot_idx: int, wname: str) -> Any:
                    if named is not None:
                        if wname in named:
                            return named[wname]
                        return None
                    for pos, (sidx, _name, _w) in enumerate(widget_slots):
                        if sidx == slot_idx:
                            return value_list[pos] if pos < len(value_list) else None
                    return None

                # Widget-only values for unconnected widget inputs.
                for slot_idx, _name, wname in widget_slots:
                    if skip_widget_name(wname):
                        continue
                    if inp_list[slot_idx].get("link") is not None:
                        continue
                    value = widget_value(slot_idx, wname)
                    if value is not None:
                        inputs[wname] = value

            # Connected inputs (widget and connectable).
            for slot_idx, inp in enumerate(inp_list):
                if not isinstance(inp, dict):
                    continue
                name = str(inp.get("name"))
                resolved = resolve_input(scope, node_id, slot_idx, set())
                if not resolved:
                    continue
                is_link, value = resolved
                if is_link:
                    origin_id, origin_slot = value
                    inputs[name] = [str(origin_id), int(origin_slot)]
                else:
                    inputs[name] = value

            override = widget_overrides.get(exec_id) or widget_overrides.get(str(node_id))
            if override:
                for k, v in override.items():
                    inputs[str(k)] = v

            prompt[exec_id] = {
                "inputs": inputs,
                "class_type": node.get("comfyClass") or type_name,
                "_meta": {"title": node.get("title") or type_name},
            }
            emitted.add(exec_id)

    # Drop references to nodes that were muted/bypassed and not emitted.
    for entry in prompt.values():
        for k, v in list(entry["inputs"].items()):
            if isinstance(v, list) and len(v) == 2 and str(v[0]) not in emitted:
                del entry["inputs"][k]

    return prompt


def deep_copy_wf(wf: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(wf))


def apply_root_overrides(
    root: dict[str, Any], overrides: dict[str, dict[str, Any]]
) -> None:
    """Overwrite widgets_values_named keys on root nodes (in place on a copy)."""
    nodes = {str(n.get("id")): n for n in root.get("nodes", []) or []}
    for node_id, kw in (overrides or {}).items():
        node = nodes.get(str(node_id))
        if node is None:
            raise RuntimeError(f"Override target node {node_id} not found")
        named = node.get("widgets_values_named")
        if not isinstance(named, dict):
            raise RuntimeError(
                f"Node {node_id} has no widgets_values_named; update it directly"
            )
        for k, v in kw.items():
            named[str(k)] = v


def load_wf(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fetch_object_info(
    base: str,
    cache_path: Optional[Path] = None,
    required: bool = True,
    refresh: bool = False,
) -> dict[str, Any]:
    if cache_path is not None and cache_path.is_file() and not refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    try:
        data = api(base, "object_info")
    except Exception:
        if required:
            raise
        return {}
    if not isinstance(data, dict):
        if required:
            raise RuntimeError(f"object_info returned {type(data).__name__}")
        return {}
    if cache_path is not None:
        cache_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return data


def _multipart_field(boundary: str, name: str, value: str) -> bytes:
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
        f"{value}\r\n"
    ).encode("utf-8")


def upload_image(
    base: str, local_path, subfolder: str = "", overwrite: bool = True
) -> str:
    """Upload an image into ComfyUI input and return the LoadImage value."""
    path = Path(local_path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    boundary = uuid.uuid4().hex
    data = path.read_bytes()
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = bytearray()
    body += f"--{boundary}\r\n".encode("utf-8")
    body += (
        f'Content-Disposition: form-data; name="image"; '
        f'filename="{path.name}"\r\nContent-Type: {ctype}\r\n\r\n'
    ).encode("utf-8")
    body += data
    body += b"\r\n"
    body += _multipart_field(boundary, "overwrite", "true" if overwrite else "false")
    if subfolder:
        body += _multipart_field(boundary, "subfolder", subfolder)
    body += f"--{boundary}--\r\n".encode("utf-8")
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    res = _request(
        "POST",
        base.rstrip("/") + "/api/upload/image",
        bytes(body),
        headers,
        timeout=180.0,
    )
    if isinstance(res, dict) and res.get("name"):
        name = str(res["name"])
        sub = str(res.get("subfolder") or "")
        return f"{sub}/{name}" if sub else name
    raise RuntimeError(f"/api/upload/image unexpected response: {res!r}")


VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".gif"}


def collect_output(
    history_item: dict[str, Any], output_dir: Path
) -> list[tuple[str, Path, str]]:
    """Return (kind, absolute_path, relative_path) tuples from a history item."""
    output_dir = Path(output_dir)
    files: list[tuple[str, Path, str]] = []
    outputs = history_item.get("outputs", {}) or {}
    for node_out in outputs.values():
        if not isinstance(node_out, dict):
            continue
        for slot_name, value in node_out.items():
            if not isinstance(value, list):
                continue
            for item_dict in value:
                if not isinstance(item_dict, dict):
                    continue
                filename = item_dict.get("filename")
                if not filename:
                    continue
                sub = str(item_dict.get("subfolder") or "")
                rel = Path(sub) / filename if sub else Path(filename)
                lower_slot = str(slot_name).lower()
                is_video = (
                    lower_slot in ("video", "videos", "gifs", "audio")
                    or rel.suffix.lower() in VIDEO_EXTS
                )
                kind = "video" if is_video else "image"
                files.append((kind, output_dir / rel, str(rel)))
    return files


def stage1_prompt(
    wf: dict[str, Any],
    *,
    text: str,
    width: int,
    height: int,
    seed: Optional[int],
    steps: int,
    prefix: str = "z-image-turbo",
    object_info: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    root = deep_copy_wf(wf)
    overrides = {"text": text, "width": width, "height": height, "steps": steps}
    if seed is not None:
        overrides["seed"] = seed
    apply_root_overrides(root, {"57": overrides, "9": {"filename_prefix": prefix}})
    return build_prompt(root, object_info=object_info)


def stage2_prompt(
    wf: dict[str, Any],
    *,
    image_name: str,
    edit: str,
    seed: Optional[int],
    prefix: str = "Flux2-Klein",
    object_info: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    root = deep_copy_wf(wf)
    nodes = {str(n.get("id")): n for n in root.get("nodes", []) or []}
    node76 = nodes.get("76")
    if node76 is None:
        raise RuntimeError("Stage2 LoadImage node 76 not found")
    node81 = nodes.get("81")
    if node81 is not None and node81.get("type") == "LoadImage":
        # Node 81 only feeds the bypassed reference-image node 92.
        node81["mode"] = MODE_NEVER
    vals76 = node76.get("widgets_values") or []
    if vals76:
        vals76[0] = image_name
    named76 = node76.get("widgets_values_named")
    if isinstance(named76, dict):
        named76["image"] = image_name

    node75 = nodes.get("75")
    if node75 is None:
        raise RuntimeError("Stage2 subgraph node 75 not found")
    vals75 = list(node75.get("widgets_values") or [])
    unet = vals75[0] if len(vals75) > 0 else None
    clip = vals75[1] if len(vals75) > 1 else None
    vae = vals75[2] if len(vals75) > 2 else None
    old_seed = vals75[4] if len(vals75) > 4 else None
    final_seed = seed if seed is not None else old_seed
    node75["widgets_values"] = [unet, clip, vae, edit, final_seed]
    node75["widgets_values_named"] = {
        "unet_name": unet,
        "clip_name": clip,
        "vae_name": vae,
        "text": edit,
        "noise_seed": final_seed,
    }

    node9 = nodes.get("9")
    if node9 is not None:
        vals9 = node9.get("widgets_values") or []
        if vals9:
            vals9[0] = prefix
    return build_prompt(root, object_info=object_info)


def stage3_prompt(
    wf: dict[str, Any],
    *,
    image_name: str,
    pos: str,
    neg: str,
    width: int,
    height: int,
    length: int,
    fps: int,
    seed: Optional[int],
    steps: int,
    cfg: float,
    prefix: str = "video/ComfyUI",
    object_info: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    root = deep_copy_wf(wf)
    nodes = {str(n.get("id")): n for n in root.get("nodes", []) or []}
    node56 = nodes.get("56")
    if node56 is None:
        raise RuntimeError("Stage3 LoadImage node 56 not found")
    node56["mode"] = MODE_ALWAYS
    vals56 = node56.get("widgets_values") or []
    if vals56:
        vals56[0] = image_name
    named56 = node56.get("widgets_values_named")
    if isinstance(named56, dict):
        named56["image"] = image_name

    apply_root_overrides(
        root,
        {
            "55": {"width": width, "height": height, "length": length},
            "6": {"text": pos},
            "7": {"text": neg},
            "57": {"fps": fps},
            "58": {"filename_prefix": prefix},
        },
    )
    node3 = nodes.get("3")
    if node3 is not None:
        update = {"steps": steps, "cfg": cfg}
        if seed is not None:
            update["seed"] = seed
        named3 = node3.get("widgets_values_named")
        if isinstance(named3, dict):
            named3.update(update)
        else:
            node3["widgets_values_named"] = update
    return build_prompt(root, object_info=object_info)


def run_stage(
    base: str,
    name: str,
    prompt: dict[str, Any],
    output_dir: Path,
    timeout_sec: float,
) -> list[tuple[str, Path, str]]:
    log(f"{name}: submitting {len(prompt)} nodes")
    prompt_id = post_prompt(base, prompt)
    log(f"{name}: prompt_id={prompt_id}")
    item = wait_prompt(base, prompt_id, timeout_sec=timeout_sec)
    files = collect_output(item, output_dir)
    if files:
        log(f"{name}: " + ", ".join(f"[{kind}] {rel}" for kind, _p, rel in files))
    else:
        log(f"{name}: completed with no collected files")
    return files


def save_prompt(directory: Path, name: str, prompt: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    out = directory / name
    out.write_text(
        json.dumps(prompt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="One-click ComfyUI pipeline: image -> edit -> video"
    )
    p.add_argument("--base", default="http://127.0.0.1:8188")
    p.add_argument(
        "--workflow-dir",
        default=os.environ.get("COMFYUI_WORKFLOWS_DIR", str(DEFAULT_WF_DIR)),
        help="Directory containing the three stage workflow JSON files",
    )
    p.add_argument(
        "--shared-dir",
        default=os.environ.get("COMFYUI_SHARED_DIR", None),
        help="ComfyUI shared root containing input/output/models",
    )
    p.add_argument("--dry-run", action="store_true", help="Build and save API prompts only")
    p.add_argument(
        "--dry-run-image",
        default="handbag_white.png",
        help="Input filename used when --dry-run (stage2/stage3 placeholder)",
    )
    p.add_argument(
        "--output-dir",
        default=None,
        help="ComfyUI output directory (default: <shared-dir>/output)",
    )
    p.add_argument("--prompt-dir", default=str(SKILL_ROOT / "logs" / "api_prompts"))
    p.add_argument(
        "--object-info-cache",
        default=str(SKILL_ROOT / "logs" / "object_info.json"),
    )
    p.add_argument("--no-object-info", action="store_true")
    p.add_argument(
        "--refresh-object-info",
        action="store_true",
        help="Ignore the object_info cache and query ComfyUI again",
    )
    p.add_argument("--timeout", type=float, default=7200.0)

    p.add_argument("--text", default=DEFAULT_POS, help="Stage1 image prompt")
    p.add_argument("--width", type=int, default=1024)
    p.add_argument("--height", type=int, default=1024)
    p.add_argument("--seed", type=int, default=None, help="Stage1 seed (default: workflow seed)")
    p.add_argument("--steps", type=int, default=8, help="Stage1 steps")
    p.add_argument("--prefix1", default="z-image-turbo", help="Stage1 filename prefix")

    p.add_argument("--edit", default=DEFAULT_EDIT, help="Stage2 edit prompt")
    p.add_argument("--seed2", type=int, default=None, help="Stage2 noise seed")
    p.add_argument("--prefix2", default="Flux2-Klein", help="Stage2 filename prefix")

    p.add_argument("--video-width", type=int, default=1280, dest="video_width")
    p.add_argument("--video-height", type=int, default=704, dest="video_height")
    p.add_argument("--length", type=int, default=121, help="Stage3 frame count")
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--seed3", type=int, default=None, help="Stage3 seed")
    p.add_argument("--video-steps", type=int, default=20, dest="video_steps")
    p.add_argument("--cfg", type=float, default=5.0)
    p.add_argument("--pos", default=DEFAULT_VIDEO_POS, help="Stage3 positive prompt")
    p.add_argument("--neg", default=DEFAULT_NEG, help="Stage3 negative prompt")
    p.add_argument("--prefix3", default="video/ComfyUI", help="Stage3 filename prefix")

    p.add_argument(
        "--small",
        action="store_true",
        help="Fast low-res run: 512x512 image, 640x384 video, 49 frames",
    )
    return p.parse_args()


def first_of_kind(files, kind: str):
    for f in files:
        if f[0] == kind:
            return f
    return None


def main() -> None:
    args = parse_args()
    shared_dir = Path(args.shared_dir or DEFAULT_SHARED)
    wf_dir = Path(args.workflow_dir)
    output_dir = Path(args.output_dir or shared_dir / "output").resolve()
    prompt_dir = Path(args.prompt_dir).resolve()

    wf1 = load_wf(wf_dir / WF_STAGE1.name)
    wf2 = load_wf(wf_dir / WF_STAGE2.name)
    wf3 = load_wf(wf_dir / WF_STAGE3.name)

    width = args.width
    height = args.height
    video_width = args.video_width
    video_height = args.video_height
    length = args.length
    video_steps = args.video_steps
    if args.small:
        width, height = 512, 512
        video_width, video_height = 640, 384
        length = min(length, 49)
        video_steps = min(video_steps, 20)

    object_info: Optional[dict[str, Any]] = None
    if not args.no_object_info:
        try:
            object_info = fetch_object_info(
                args.base,
                Path(args.object_info_cache),
                required=not args.dry_run,
                refresh=args.refresh_object_info,
            )
        except Exception as e:
            if args.dry_run:
                log(f"WARNING: object_info unavailable ({e}); widget-only values may be lost")
            else:
                raise

    placeholder = args.dry_run_image
    p1 = stage1_prompt(
        wf1,
        text=args.text,
        width=width,
        height=height,
        seed=args.seed,
        steps=args.steps,
        prefix=args.prefix1,
        object_info=object_info,
    )

    if args.dry_run:
        p2 = stage2_prompt(
            wf2,
            image_name=placeholder,
            edit=args.edit,
            seed=args.seed2,
            prefix=args.prefix2,
            object_info=object_info,
        )
        p3 = stage3_prompt(
            wf3,
            image_name=placeholder,
            pos=args.pos,
            neg=args.neg,
            width=video_width,
            height=video_height,
            length=length,
            fps=args.fps,
            seed=args.seed3,
            steps=video_steps,
            cfg=args.cfg,
            prefix=args.prefix3,
            object_info=object_info,
        )
        for stage, prompt in (("1", p1), ("2", p2), ("3", p3)):
            out = save_prompt(prompt_dir, f"stage{stage}.api.json", prompt)
            log(f"dry-run stage{stage}: {len(prompt)} nodes -> {out}")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    save_prompt(prompt_dir, "stage1.api.json", p1)
    files1 = run_stage(args.base, "Stage1 image", p1, output_dir, args.timeout)
    image1 = first_of_kind(files1, "image")
    if image1 is None:
        raise RuntimeError("Stage1 produced no image output")
    log(f"Uploading stage1 image for stage2: {image1[2]}")
    name2 = upload_image(args.base, image1[1])

    p2 = stage2_prompt(
        wf2,
        image_name=name2,
        edit=args.edit,
        seed=args.seed2,
        prefix=args.prefix2,
        object_info=object_info,
    )
    save_prompt(prompt_dir, "stage2.api.json", p2)
    files2 = run_stage(args.base, "Stage2 edit", p2, output_dir, args.timeout)
    image2 = first_of_kind(files2, "image")
    if image2 is None:
        raise RuntimeError("Stage2 produced no image output")
    log(f"Uploading stage2 image for stage3: {image2[2]}")
    name3 = upload_image(args.base, image2[1])

    p3 = stage3_prompt(
        wf3,
        image_name=name3,
        pos=args.pos,
        neg=args.neg,
        width=video_width,
        height=video_height,
        length=length,
        fps=args.fps,
        seed=args.seed3,
        steps=video_steps,
        cfg=args.cfg,
        prefix=args.prefix3,
        object_info=object_info,
    )
    save_prompt(prompt_dir, "stage3.api.json", p3)
    files3 = run_stage(args.base, "Stage3 video", p3, output_dir, args.timeout)
    video = first_of_kind(files3, "video")

    log("Pipeline finished")
    log(f"  stage1 image: {image1[2]}")
    log(f"  stage2 image: {image2[2]}")
    if video is not None:
        log(f"  stage3 video: {video[2]}")
    else:
        log("  stage3 video: no file collected (check SaveVideo history)")


if __name__ == "__main__":
    main()
