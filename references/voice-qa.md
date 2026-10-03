# 配音、字幕、拟音与质检

## 1. 角色音色固定

每个角色在角色设定 JSON 里锁定：

```json
{
  "speaker": "Aiden",
  "seed": 202,
  "instruct": "青年疲惫男声，语速偏慢，低沉克制，自然口语，不要播音腔",
  "language": "Chinese"
}
```

同一角色所有镜头使用同一个 speaker、seed、instruct 风格。角色之间音色必须拉开：旁白低沉磁性、青年男主疲惫内敛、反派低沉威胁、女主清冷、老人沙哑神秘。

建议先按固定台词生成角色音色试听，用户确认后再量产。

## 2. TTS 参数

使用 `FB_Qwen3TTSCustomVoice`：

- `model_choice: 0.6B`
- `language: Chinese`
- `temperature: 0.8-0.95`
- `top_p: 0.9-0.95`
- `repetition_penalty: 1.05-1.1`
- `seed: 每个角色固定`

`instruct` 才是活人感的关键：写年龄、情绪、语速、气息，不要只写“用中文念”。

旁白可用 edge-tts 等外部 TTS 生成后混入，通过 rate/pitch 控制节奏；试听时给多档语速（如 -20%、-35%）供选择。

## 3. 字幕

工作流：`Add Voice → Apply Whisper → Save SRT → Add Subtitles To Frames → CreateVideo → SaveVideo`。
配音对齐先跑 `scripts/plan_batch.py`，把台词/旁白、字数、预估语速和角色音色表拉齐。

字幕规则：

- 中文 Whisper 识别后必须逐条校对，错别字直接改 SRT。
- 字幕底边居中，白字黑边或黑字白边，字号不要覆盖画面主体。
- 每条字幕短句优先，两个长句拆成两条，不超框、不遮挡人物脸部。
- 角色独白、旁白、弹字分开风格；禁止把整段小说原文做成字幕。
- 单条字幕语速超过 6 字/秒要压缩，超过 8 字/秒必须改稿。

## 4. 拟音与混音

拟音走 `短剧配音字幕拟音综合工作流.json`，包含 HunyuanFoley 的加载与采样节点。再叠加环境底噪、关门/雨声等必要音效和 BGM。

混音参考电平：

- 人声清楚，BGM 压低到人声之下
- 成品 mean 音量约 -24 到 -14 dB，peak 不超过 -3 dB
- 综合响度建议 -20 到 -12 LUFS，真实峰值不超过 -1 dBTP；削波峰必须清零
- 出现电流声、爆音、刺耳高频、环境噪音盖过人声时，必须重新合成或去噪

## 5. 成片质检

交付前执行：

```powershell
python scripts/qa_shot.py --video "成片.mp4" --frames 1.0,2.5,4.0 --srt "字幕.srt"
```

可选外部钩子（不在技能里假装带 OCR/人脸模型）：

```powershell
python scripts/qa_shot.py --video "成片.mp4" `
  --ocr-cmd "python ocr_check.py {frame}" `
  --face-cmd "python face_check.py {frame} --ref 定妆照.png"
```

QA 工具检查：

- 自动：抽帧、音轨 mean/peak、RMS、直流偏移、削波峰、响度、字幕/SRT 语速
- 外部：OCR 文字、人物一致性钩子必须由用户提供，缺失时明确提示人工兜底
- 完整播放一遍，检查旁白语速、口型宽松度、转场是否拖沓
- 有问题的镜头先修对应环节，不直接交付
