# 短剧提示词库

本文件把提示词知识从“写的时候随手编”升级为可复用词库。规则与 `production.md`
共同生效：角色一致性永远优先，镜头提示词必须在角色定妆块不变的前提下组装。

## 1. 提示词组装公式

```text
[角色定妆块] + [场景/时间/天气] + [主体动作/表情] + [镜头语言] + [光线氛围] + [画质/画幅] + [文字处理]
```

角色定妆块必须来自已锁定角色卡的 `appearance + style_block`，并保留
`from final portrait reference`。不同镜头只改动作、场景、光线、镜头运动，
不改已确认的五官、服饰、年龄、气质。

## 2. 景别速查

| 景别 | 主体占比 | 用途 | 典型时长 |
| --- | --- | --- | --- |
| 特写 | 面部/眼部/手部局部 | 情绪爆发、反转、关键道具 | 2-4s |
| 近景 | 胸部以上 | 人物反应、台词 | 3-5s |
| 中景 | 腰部以上 | 动作与对话的常见主镜头 | 4-6s |
| 全景 | 全身 | 关系建立、入场、环境交代 | 4-7s |
| 远景 | 人物很小 | 城市、雨夜、压迫感、转场 | 3-5s |

短剧节奏优先：情绪冲突用特写/近景，环境交代用全景/远景；同景别连续不超过两个镜头。

## 3. 运镜速查

| 运镜 | 效果 | 短剧用法 |
| --- | --- | --- |
| 推 | 进入人物内心 | 发现青铜盒、倒计时出现 |
| 拉 | 拉开空间关系 | 反派入场、真相揭晓 |
| 摇 | 展示环境 | 从雨夜街道扫到路灯下摊主 |
| 移 | 跟随人物运动 | 主角冲下楼、追逐 |
| 跟 | 保持人物在画面中 | 动作戏、走廊搜查 |
| 升降 | 改变观感高度 | 从窗外俯拍、进入办公室 |
| 环绕 | 强化对峙 | 男女主对峙、人物博弈 |
| 手持 | 慌乱、临场感 | 停电、爆炸、逃跑 |
| 固定 | 稳定叙事 | 台词戏、静态氛围 |
| 微推/微摇 | 避免呆板 | 长台词镜头补呼吸感 |

单个镜头只写一种主运镜，最多补一个“轻微”副词：`缓慢推近`、`轻微手持晃动`；
不要一镜堆叠 推+摇+跟 三个动作。

## 4. 光线氛围词库

- 暴雨夜室外：`heavy rain, wet asphalt reflections, warm street lamp, cool night ambient`
- 办公室冷光：`cold fluorescent ceiling light, monitor glow on face, shallow shadows`
- 应急灯/停电：`emergency greenish light, single key light, deep shadow`
- 警灯红蓝：`red and blue police light sweep, rain haze`
- 暖黄旧书摊：`warm tungsten lamp, cardboard boxes, humid night`
- 威胁/压抑：`low-key lighting, hard shadow, desaturated color grade`
- 希望/反转：`soft rim light, subtle warm highlight, clean bokeh`

光线词必须和剧情情绪一致，不能把悲伤镜头写成阳光软光。

## 5. 文字画面处理

- 短信/通知/LOGO/倒计时先走后期叠加；必须 AI 生成时，把确切文字写入提示词，
  并加 `precise readable text layout`，不能同时写 `no text`。
- 角色口播字幕、旁白字幕一律后期烧录，不进画面提示词。
- 末尾统一处理：无文字画面的镜头结尾写 `no text, no watermark, no logo`。

## 6. 统一负面提示词

基础块（所有镜头统一）：

```text
文字，字幕，水印，logo，乱码，UI，手机界面，聊天框文字，模糊，失焦，
变形，扭曲，崩坏，多余手指，手指错误，手部错误，面部错误，五官错位，
多余肢体，低质量，画质差，噪点，JPEG伪影，过曝，欠曝
```

短剧一致性附加块（视频阶段按需）：

```text
换脸，变老，换发型，换服装，服饰突变，场景突变，多张人脸，角色漂移，
关节扭曲，动作僵硬，表情崩坏，穿帮，电线杆穿头，手指穿物，比例失调
```

反面示例：人物在同一集里换了发型，说明镜头提示词改了角色定妆块；
修复方法是把该镜从“换发型描述”改回角色卡原词，而不是新增更复杂的形容。

## 7. 画面风格块

当前实机默认：

```text
3D next-generation CG, Chinese anime semi-realistic style, refined PBR material,
delicate skin and fabric texture, cinematic filmic lighting, realistic environment
integration, 16:9 widescreen cinematic composition, high quality 8k render,
AI animated short-drama key visual
```

换画风只改 `style_block` 和 `negative_prompt`，不改人物身份块；
风格切换要在定妆照阶段完成并重新锁定，不能在镜头阶段偷偷改。
