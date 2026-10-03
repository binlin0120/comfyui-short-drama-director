# 质量门校验

## 使用时机

- 角色设定 JSON 与分镜 JSON 完成后、批量生成视频前，必须跑一次。
- 每集成片交付前可再跑一次，确认结构没有在后期改动中回退。
- FAIL 表示禁止批量生成；WARN 表示可放行，但建议在交付前处理。

## 命令

```powershell
python scripts/validate_project.py --project "项目目录"
```

默认从项目目录自动发现 `角色设定*.json`、`分镜*.json`、`分镜脚本*.md`，
优先选择文件名含“最终/终版/定稿”的版本，并跳过 `_旧版`、`备份`、
`预览工作区`、`镜头预览` 等目录。也可以用显式参数指定文件：

```powershell
python scripts/validate_project.py --project "项目目录" `
  --role-file "角色设定_最终版.json" --shot-file "分镜_第1集.json" `
  --storyboard-file "分镜脚本_第1集.md" --aspect 16:9
```

机器可读输出：

```powershell
python scripts/validate_project.py --project "项目目录" --json
```

## 退出码

- `0`：无 FAIL，WARN 可放行。
- `1`：存在 FAIL，先修复再生成。
- `2`：输入缺失、目录不存在或文件不可解析。

## 门清单

| 门 | 检查内容 | 不通过级别 |
| --- | --- | --- |
| G01 | 文件存在且可解析（CLI 本身） | 退出码 2 |
| G02 | 角色卡片必填字段齐全，状态标记最终/锁定 | FAIL/WARN |
| G03 | 角色名、key、seed 唯一，key 建议 snake_case | FAIL/WARN |
| G04 | 定妆照 output 存在且非空 | FAIL |
| G05 | 每个角色有音色来源（voice/tts/speaker 或音色方向列） | WARN |
| G06 | 每个角色都有 character_locks 或锁定卡 | FAIL |
| G07 | 镜头编号连续 1..N | FAIL/WARN |
| G08 | 镜头必填字段齐全，fps 为正整数 | FAIL/WARN |
| G09 | 单镜 3-8 秒，6 秒以上提示节奏风险 | FAIL/WARN |
| G10 | 总时长与 total_seconds 对齐，超 120s/180s 风险 | FAIL/WARN |
| G11 | 景别包含 特写/近景/中景/全景/远景 | WARN |
| G12 | 运镜包含 推/拉/摇/移/跟/固定/手持 等 | WARN |
| G13 | 口播密度合理（旁白+台词，默认 6 字/秒预警） | FAIL/WARN |
| G14 | text_on_screen 写入提示词，且不混用 no text | FAIL/WARN |
| G15 | 出场角色必须已锁定；镜头提示词建议引用定妆照 | FAIL/WARN |
| G16 | 画幅与目标一致（默认 16:9） | FAIL/WARN |
| G17 | 每镜提示词带统一画风风格块 | FAIL/WARN |
| G18 | 统一负面提示词覆盖 文字/水印/模糊/变形/手部/低质量 | FAIL |
| G19 | 第一镜有淡入/开场，最后一镜有钩子 | WARN/FAIL |
| G20 | 单镜出场角色不超过默认 3 人 | WARN |

## 兼容的资产格式

- 角色设定 JSON：顶层 `images` 数组，或顶层为角色名到卡片的映射。
- 分镜 JSON：顶层 `shots` 数组，可带 `character_locks`、`style_block`、
  `negative_prompt`、`fps`、`aspect`、`total_seconds`。
- 分镜 Markdown：带“角色/定妆照/固定外观/音色方向”列的角色锁定卡可作为
  角色锁定与音色方向的补充来源。

## 修复顺序

1. 先修 FAIL：角色字段/定妆照、角色锁定、镜头字段、时长范围、文字画面、
   画幅、风格块、负面提示词、结尾钩子。
2. 再按 WARN 优化：补音色显式字段、压缩 6 秒以上镜头、降低口播密度、
   给提及角色的镜头补 `from final portrait reference`、减少单镜角色数。
