# 项目文件与稳定 ID

本技能以“角色设定 JSON + 分镜 JSON + 分镜 Markdown”为机器校验基座，
不强制改成多文档目录。稳定 ID 和参考图状态作为补充规则，写在现有字段里，
避免改版时“改了哪里、参考的是哪张图、哪版定妆被锁定”全部丢失。

## 1. 稳定 ID 规则

| ID 前缀 | 含义 | 示例 |
| --- | --- | --- |
| `SHOT-NN` | 分镜镜头，编号与 JSON `shots[].shot` 一致 | `SHOT-01` |
| `REF-<key>-f` | 角色锁定定妆照（final portrait） | `REF-lin_yu-f` |
| `REF-<key>-b` | 角色多角度/侧面/背面参考 | `REF-lin_yu-b` |
| `PLAN-<key>-NN` | 用户挂图或生成计划，不可当成品定妆 | `PLAN-lya-01` |
| `IMG-<key>-NN` | 图片提示词条目（用于可选五文档） | `IMG-lin_yu-01` |
| `MOTION-NN` | 视频动作提示词条目，与镜头一一对应 | `MOTION-01` |
| `S01` / `P01` | 场景/道具资产编号，跨镜复用 | 老槐树路灯雨夜 `S01` |

编号写进 JSON：

```json
{
  "shots": [
    {
      "id": "SHOT-01",
      "motion_id": "MOTION-01",
      "shot": 1,
      "scene_id": "S01",
      "batch_id": "S01-雨夜",
      "ref_state": "REF",
      "tail_frame_desc": "大雨中林羽护盒跑进楼道，老槐树路灯在背景右侧"
    }
  ],
  "scene_assets": {
    "S01": "小区西门老槐树，暖黄路灯，雨夜"
  },
  "model_profile": {
    "id": "wan2.2-local",
    "route": "z-image -> flux2-edit -> wan2.2-ti2v"
  }
}
```

角色卡片同样补引用字段：

```json
{
  "ref_id": "REF-lin_yu-f",
  "ref_state": "REF",
  "citation": "原文：林羽猛地合上笔记本...他抓西装外套冲下楼",
  "voice_source": "role_card"
}
```

## 2. 参考图四态

不是所有图都能当一致性证据：

| 状态 | 含义 | 能否量产 |
| --- | --- | --- |
| `REF` | 真实定妆照/参考帧已存在并锁定 | 可以 |
| `PLAN` | 用户挂图或计划图，未锁 | 不可以，先确认 |
| `TODO` | 待补参考图 | 不可以，先补 |
| `TTV` | 该镜头直接文生视频，不依赖参考图 | 可以，但要在分镜里声明 |

量产前逐角色核对：`REF` 存在、路径可读、提示词与图片一致。
分镜里引用了角色却没有 `REF`，按 `references/validation.md` 的
G15 处理，并补状态说明。

`batch_id` 和 `tail_frame_desc` 由 `references/batch-continuity.md` 使用：
`scripts/plan_batch.py` 按 `scene_id`/`batch_id` 归批，尾帧描述进入批量审批表。

## 3. 可选五文档模板

长项目可以拆五份人工交付稿；短项目保留现有 JSON + Markdown 即可。
机器校验始终以 JSON 为准，五文档用于人审和改稿：

```text
剧本.md                  # 场景+时间+人物+动作+台词
视觉设定.md               # 角色卡+定妆照引用+场景/道具资产
分镜.md                  # SHOT-NN 表：景别/时长/动作/台词/尾帧
图片提示词.md             # IMG- 条目：阶段一/阶段二提示词
视频提示词.md             # MOTION-NN 条目：动作+运镜+尾帧
```

每份文档头部注明项目、集数、目标模型 profile、画幅、当前版本。
改稿时更新对应文档，并把版本号写回 JSON 顶层 `meta.version`。

## 4. 引用核对

- 角色姓名、身份、年龄、外貌、性格、关系必须给原文依据（引文）。
- 原文没写清楚的部分可以补充，但必须标“补充设定”，不能冒充原文。
- 台词逐字引用，长台词按句拆分，避免旁白/口播和字幕漂移。
