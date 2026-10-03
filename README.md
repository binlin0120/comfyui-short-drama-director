# comfyui-short-drama-director

一个可移植的 Codex 技能：让 Codex 以 ComfyUI 导演总指挥的身份，把小说或短故事做成 AI 短剧成片。技能自动发现后，用户只需贴原文或下命令。

## 特性

- 故事 → 剧本 → 分镜 → 角色设定 → 提示词 → ComfyUI 生成 → 配音字幕拟音 → 质检交付
- 角色一致性、固定音色、16:9 默认、文字质检等硬规则内置
- 批量生成前用 20 个质量门校验角色设定与分镜，FAIL 项拦截生成
- 不写死本机绝对路径，通过环境变量和命令行参数适配不同电脑
- 附带三段式生成工作流模板与适配脚本

## 目录

```text
comfyui-short-drama-director/
|-- SKILL.md
|-- references/
|   |-- setup.md                 # 安装 ComfyUI、模型、自定义节点
|   |-- pipeline.md              # 三段式 API 生成管线
|   |-- production.md            # 导演全流程与提示词规则
|   |-- voice-qa.md              # 配音、字幕、拟音、质检
|   `-- validation.md            # 质量门校验规则与门清单
|-- scripts/
|   |-- comfy_pipeline.py        # 图 -> 编辑图 -> 视频 的 API 调度
|   |-- start_comfyui.py         # 启动并健康检查
|   |-- qa_shot.py               # 抽帧与音频电平检查
|   `-- validate_project.py      # 20 个质量门，批量生成前拦截 FAIL
`-- assets/
    `-- workflows/               # 可复用工作流模板
```

## 本地安装（个人使用）

把整个目录复制到 `~/.codex/skills/comfyui-short-drama-director`（Windows 为 `C:\Users\<你>\.codex\skills\...`），然后设置环境变量。下面的 `D:\Comfy-Desktop\...` 只是本机示例路径，请按你电脑上的实际位置替换：

```powershell
$env:COMFYUI_SHARED_DIR = "D:\Comfy-Desktop\ComfyUI-Shared"
$env:COMFYUI_INSTALL_DIR = "D:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI"
```

然后按 `references/setup.md` 完成模型与自定义节点准备。

## 基本命令

```powershell
# 启动服务（后台）
python scripts/start_comfyui.py --shared-dir $env:COMFYUI_SHARED_DIR --install-dir $env:COMFYUI_INSTALL_DIR --detach

# 批量生成前校验角色与分镜（FAIL 需先修复）
python scripts/validate_project.py --project "项目目录" --aspect 16:9

# 生成一镜
python scripts/comfy_pipeline.py --shared-dir $env:COMFYUI_SHARED_DIR --text "画面提示词" --width 1280 --height 720 --edit "精修描述" --pos "视频动作提示词" --neg "负面提示词"
```

开源发布时请忽略模型文件和缓存，只发布本目录内的文档、脚本与工作流模板。

## 从 GitHub 安装

```powershell
git clone https://github.com/binlin0120/comfyui-short-drama-director.git "$HOME\.codex\skills\comfyui-short-drama-director"
```

## License

MIT
