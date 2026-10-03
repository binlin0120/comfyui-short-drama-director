# ComfyUI 三段式生成管线

本技能把成片拆成三个独立 API 请求，避免一张大图同时载入多个大模型导致显存不足。
本章描述当前默认档案 `wan2.2-local`；要切换 Seedance/MiniMax/Wan 3.0 等可选档案时，
先看 `references/model-profiles.md`，不要直接沿用本页文件名与节点号。

## 工作流模板

`assets/workflows/` 提供：

| 文件 | 作用 | 关键节点 |
| --- | --- | --- |
| `ZImage_Turbo_INT8.json` | 文本 → 基图 | 子图节点 `57`（text/width/height/steps），`9` SaveImage |
| `Flux2_Klein_4B_Image_Edit.json` | 基图 + 编辑提示词 → 精修图 | `76` LoadImage，子图 `75`（编辑词/seed），`9` SaveImage |
| `Wan22_5B_TI2V_GGUF.json` | 精修图 + 动作提示词 → 视频 | `56` LoadImage，`6` 正向词，`7` 反向词，`55` 图生视频潜空间，`3` KSampler，`57` CreateVideo，`58` SaveVideo |
| `短剧配音字幕拟音综合工作流.json` | 配音/字幕/拟音合成 | 见 `voice-qa.md` |

这些 JSON 是 ComfyUI Desktop 工作流格式，包含子图；`scripts/comfy_pipeline.py` 会拉平子图并生成 `/prompt` API 负载。

## 模型方言

- 当前默认：ZImage → Flux2 编辑 → Wan2.2 图生视频，三段独立 `/prompt`。
- 视频提示词按 `prompt-library.md` 组装，负面包不能省。
- 切换模型 profile 时，工作流文件名、节点号、分辨率、`model_profile.id` 要一起换，
  并用 `plan_batch.py --dry-run` 先出一版批次计划确认。

## 运行方式

```powershell
python scripts/comfy_pipeline.py `
  --shared-dir $env:COMFYUI_SHARED_DIR `
  --text "16:9 电影感，夜雨城市，林羽在旧书摊蹲下，暖黄路灯，俯拍" `
  --width 1280 --height 720 `
  --edit "人物表情更紧张，雨滴清晰，电影感柔光" `
  --pos "镜头缓慢下摇，雨水滴落，人物轻微屏息" `
  --neg "文字，字幕，水印，模糊，变形，多余手指，画质差" `
  --length 121 --fps 30
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--shared-dir` | `ComfyUI-Shared` 路径，默认读 `COMFYUI_SHARED_DIR` |
| `--workflow-dir` | 工作流目录，默认读取技能自带 `assets/workflows` |
| `--text` | 阶段一图片提示词 |
| `--edit` | 阶段二编辑提示词 |
| `--pos` / `--neg` | 阶段三视频正/反向提示词 |
| `--width` / `--height` | 阶段一图片尺寸 |
| `--video-width` / `--video-height` | 视频尺寸，16:9 常用 `1280x720` |
| `--length` | 视频帧数，3-8 秒按 `length = 秒数 * fps` 计算 |
| `--dry-run` | 只生成 API 负载不执行，适合改提示词前验证 |

## 执行机制

1. 拉平工作流，按 `/object_info` 恢复 widget 名称，缓存到 `logs/object_info.json`。
2. `POST /prompt` 提交，轮询 `/history/{prompt_id}` 直到完成。
3. 阶段一图片上传到 `/api/upload/image`，供阶段二 `LoadImage` 使用；阶段二输出再喂阶段三。
4. 输出自动收集到 `<shared-dir>/output`。

## 常见失败

- `/prompt` 返回 400：先 `--dry-run` 检查提示词、节点参数和对象信息。
- 找不到模型：确认模型文件放在正确的 `models` 子目录，且 `extra_model_paths.yaml` 映射正确。
- 视频无输出：检查 `SaveVideo` 历史输出；换更小 `length` 或更低分辨率重试。
- 显存不足：降低分辨率、缩短帧数，或先卸载 TTS/拟音模型再跑视频。
