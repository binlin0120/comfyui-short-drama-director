# 环境搭建

本文件描述从零搭建本技能所需环境。所有路径均可通过环境变量或参数替换，不含本机私有路径。

## 1. 安装 ComfyUI Desktop

从 ComfyUI 官方发布渠道下载 ComfyUI Desktop 安装包并安装。建议安装在磁盘根目录下的独立目录，例如：

```text
D:\Comfy-Desktop\
|-- ComfyUI-Installs\ComfyUI\ComfyUI\   # 服务端、custom_nodes、内置 Python
|-- ComfyUI-Shared\                     # input / output / models
`-- ComfyUI-Cache\                      # 模型缓存
```

安装完成后确认：

```powershell
$env:COMFYUI_INSTALL_DIR = "D:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI"
Test-Path "$env:COMFYUI_INSTALL_DIR\main.py"
```

## 2. 共享模型目录

创建共享目录并记录路径：

```powershell
$env:COMFYUI_SHARED_DIR = "D:\Comfy-Desktop\ComfyUI-Shared"
New-Item -ItemType Directory -Force -Path "$env:COMFYUI_SHARED_DIR\models"
```

本项目使用以下模型（名称可替换为同系列官方模型，但不要混用不兼容版本）：

| 用途 | 建议目录 | 参考文件 | 说明 |
| --- | --- | --- | --- |
| 文本生成基图 | `models/diffusion_models` | `z_image_turbo_int8_convrot.safetensors` | 快速出图，INT8 适配低显存 |
| 图像精修 | `models/diffusion_models` | `flux-2-klein-4b-fp8.safetensors` | 在基图上按编辑提示词改画面 |
| 视频生成 | `models/unet` | `Wan2.2-TI2V-5B-Q8_0.gguf` | 图生视频，GGUF 格式 |
| 文本编码 | `models/text_encoders` | `qwen_3_4b_fp8_mixed.safetensors` | Z-Image / Flux 中文理解 |
| 文本编码 | `models/text_encoders` | `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | Wan2.2 视频文本编码 |
| VAE | `models/vae` | `ae.safetensors`、`flux2-vae.safetensors`、`wan2.2_vae.safetensors` | 对应解码 |
| TTS | `models/qwen-tts` | Qwen3-TTS 0.6B 模型 | 角色配音 |
| Whisper | `models/stt` | Whisper small 或更大 | 语音转文字生成 SRT |
| 拟音 | `models/hunyuanfoley` | `hunyuanvideo_foley.pth` + `vae_128d_48k.pth` + `synchformer_state_dict.pth` | 视频自动拟音 |

模型体积大，建议从对应官方或 ComfyUI 社区模型卡下载，并用哈希校验。不要提交模型文件到开源仓库。

## 3. 自定义节点

在 `ComfyUI-Installs\ComfyUI\ComfyUI\custom_nodes` 安装：

- `ComfyUI-GGUF`：加载 GGUF 视频模型
- `ComfyUI-Qwen-TTS`：`FB_Qwen3TTSCustomVoice` 角色配音
- `ComfyUI-Whisper`：`Apply Whisper`、`Save SRT`、`Add Subtitles To Frames`
- `Comfyui-HunyuanFoley`：`HunyuanModelLoader`、`HunyuanFoleySampler` 视频拟音

推荐使用 ComfyUI Manager 搜索安装；手动安装时把 zip 解压到 `custom_nodes`，重启服务。

## 4. 共享模型路径配置

在 `extra_model_paths.yaml` 中写入共享目录映射（本技能流程默认 `ComfyUI-Shared` 结构）：

```yaml
shared_models:
    base_path: ${COMFYUI_SHARED_DIR}
    diffusion_models: models/diffusion_models
    unet: models/unet
    text_encoders: models/text_encoders
    vae: models/vae
```

启动时把该文件传给 ComfyUI；`scripts/start_comfyui.py` 会自动加上该参数。

## 5. 启动与检查

```powershell
python ..\scripts\start_comfyui.py --install-dir $env:COMFYUI_INSTALL_DIR --shared-dir $env:COMFYUI_SHARED_DIR --detach
python ..\scripts\start_comfyui.py --health-only
```

健康检查通过后，服务默认地址为 `http://127.0.0.1:8188`。
