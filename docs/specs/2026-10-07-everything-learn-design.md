# everything-learn 设计文档（Design Spec）

> 状态：待用户评审
> 日期：2026-10-07
> 上游：brainstorming 会议（本仓库首份文档）
> 关联：`bilibili-learn`（轻量版，保持原样、独立仓库）

---

## 1. 背景与定位

`everything-learn` 是 `bilibili-learn` 的全量进化版：

- **bilibili-learn**：只做 B 站，轻量版，作为参考实现保留，仓库与技能行为**不再改动**（bugfix 按需手工搬运）。
- **everything-learn**：新技能、独立配置、独立 GitHub 仓库（本仓库），覆盖**全形态**内容（C 决策）：视频 / 音频 / 图文 / 社媒短内容聚合，**包含 B 站**（作为首个参考适配器随核心迁移）。
- 复用策略 = **方案 1（Fork 演进 + 内部 core/providers 分层）**：以 bilibili-learn v3.4 成熟代码为起点直接复制，重构为「与平台无关的核心」+「平台适配器」；两仓库通过 git 独立演进，不做共享包。

## 2. 目标与范围

### 2.1 内容形态（三种）

| 形态 | 数据 | 来源示例 | 报告形态 |
|---|---|---|---|
| `timeline` | `[{start, end, text}]` | B站、YouTube、播客 | 时间轴分点报告（复用现有规则） |
| `document` | `[{heading?, text, images?}]` | 知乎、Medium、公众号 | 章节式报告 |
| `collection` | 多条 document/timeline | X、微博、小红书 | 主题聚合式报告 |

### 2.2 实施顺序（用户决策）

**YouTube（M1）→ 播客（M2）→ 知乎（M3）→ 其余平台（M4+）**；B 站适配器随 M0 核心迁移。

### 2.3 成功标准（v0.1 = M0+M1）

> 独立仓库/独立配置：输入一个 YouTube 链接 → 自动获取字幕（自动字幕优先，音轨 Whisper 兜底，复用多模态复检）→ 产出与 bilibili-learn 同规格的**中文学习报告**并落盘到用户自选的学习笔记根目录；B 站适配器随核心迁移并通过回归验证。

## 3. 架构

### 3.1 分层

- `core/`：平台无关流水线——内容契约、配置、缓存/限流重试、转写（Whisper）、多模态复检、报告骨架渲染、术语库、doctor。
- `providers/`：平台适配器（一个平台一个模块）；B 站为参考实现。

### 3.2 适配器接口

```python
class SourceAdapter(Protocol):
    name: str            # "bilibili" / "youtube" / "podcast" / "zhihu"
    kinds: set[str]      # {"video"} / {"audio"} / {"article"} / {"thread"}
    def match(self, url: str) -> bool: ...               # URL 路由
    def resolve(self, q: str) -> ItemRef: ...            # 链接/名称 → 稳定标识（无法定位抛 SearchNeeded）
    def list_items(self, ref: ItemRef) -> list[ItemRef]: ...  # 多P/列表展开（单项返回 [ref]）
    def fetch_meta(self, ref: ItemRef) -> ItemMeta: ...  # 标题/作者/时长/日期/统计
    def fetch_content(self, ref: ItemRef) -> ContentResult: ...  # timeline / document（含分级状态）
    def fetch_media_url(self, ref: ItemRef, media: str = "audio", lowest: bool = False) -> str | None  # 音/视频直链（无则 None）
```

### 3.3 目录骨架

```
everything-learn/
├── SKILL.md / README.md
├── scripts/
│   ├── el.py                  # CLI：run / report / export / doctor / frames…
│   ├── core/  adapter.py  models.py  pipeline.py  artifacts.py
│   │          config.py  cache.py  retry.py
│   ├── providers/  registry.py  bilibili.py  youtube.py  podcast.py  zhihu.py …
│   ├── transcriber.py  vision.py  report.py  glossary.py   # 自 bilibili-learn 迁移
├── references/glossary.json
├── docs/  specs/  plans/  ADAPTER_SPEC.md  ADAPTER_TEMPLATE.md
└── tests/ (fixtures/ contract/ …)
```

## 4. 数据流与产物契约

### 4.1 端到端数据流（单个 item）

```
输入（URL / 名称 / 列表）
 → core.pipeline.run(input)
   1) 路由   providers 按 match(url) 选中适配器；无匹配 → 自进化引导（见 §6）
   2) 解析   resolve → ItemRef（平台 + 稳定 ID + 子项定位）
   3) 元信息 fetch_meta → ItemMeta
   4) 取内容 fetch_content →
        timeline 源：平台字幕/自动字幕优先 → 分级校验（ok/suspect/invalid）
                    → 可疑：多模态 3 帧归属复检（一致采信 → 内容完整性复检）
                    → 不一致/不可信/无字幕：音轨 → Whisper
        播客类：通常无平台字幕 → 音轨 → Whisper
        document 源：正文 + 图片提取 → 段落块
   5) 落盘三件套 + 写缓存
 → agent 按 SKILL.md 写报告 → glossary check → 交付
```

**批量**：一个输入可展开为多个 item（B站多 P / YouTube 播放列表 / 播客 RSS 全集），逐项处理 + 增量汇总 `content_info.json`，沿用「失败不中断 + `--resume`」。

### 4.2 产物三件套

```
<out_dir>/<作者>/<YYYY-MM-DD>_<标题>[/P##]/
├── content_info.json    # 元信息 + 状态 + 形态 + source 标记
├── content.txt          # timeline → "[起-止] 文本" 行；document → Markdown 原文结构
├── report_template.md   # 按形态选择的报告骨架
└── assets/              # document 源正文图片（按需；兜底 content_raw.html 亦存放于此）
```

`content_info.json` 草案：

```json
{
  "platform": "youtube", "item_id": "abc123", "url": "…",
  "title": "…", "author": "…", "published": "2026-10-01",
  "duration_seconds": 1234, "kind": "timeline",
  "page": "1/12", "source": "字幕+vision复核",
  "stats": {"view": 0}, "fetched_at": "…", "tool": "everything-learn v0.1"
}
```

合集/批量：外加 `"items": [...]`，每项带 `page/status/source/error`（沿用现有汇总格式）。

- **source 标记**：`字幕 / 字幕+vision复核 / whisper / vision补充 / document / raw_html / cache`。
- **缓存键**：`(platform, item_id[, sub_id])`。

### 4.3 输出路径（硬性规则）

- `out_dir` **不作为技能默认值**。`el.py` 检测到 `out_dir` 为空时**拒绝运行**，提示用户选择（`--out` 可临时覆盖）。
- `SKILL.md` 硬性要求：**配置技能或首次使用时，agent 必须询问用户选择一个存放报告的根路径**；先创建总文件夹（如「学习笔记」），写入配置后才可运行。
- 本机（作者环境）实例取 `C:\data\BaiduSyncdisk\学习笔记\`，与 `bilibili学习笔记` 分开存放，避免新旧混放。

## 5. 报告形态与 SKILL 规则分层

### 5.1 共用规则（全形态）

| 编号 | 规则 |
|---|---|
| R1 | 知识点完整性（最高优先级：覆盖全部知识点，不设篇幅/成本上限） |
| R4 | 浓缩去水（删寒暄/过渡/重复） |
| R8 | 官方+通俗双版本 |
| R9 / R9.1 | 延伸标注与深度 |
| R11 | 名词注释（glossary 库；`glossary check` 零遗漏） |
| R6 | 自测（每 3~5 个知识点一题） |
| R7 | 趣味彩蛋默认关闭 |
| R10 | 交付命名 `<标题>_学习报告.md`，按作者归档 |
| — | 金句与重要观点 / 关键概念表 |

### 5.2 形态专属

- **timeline**（B站/YouTube/播客）：R2/R3 时间戳分点（`### 段N｜[起-止] 主题`），原话摘录带时间点；多模态复检（抽帧读图补公式/代码/图表）为固定路径。
- **document**（知乎/Medium/公众号）：按原文章节切段（`### 段N｜章节名`）；引用定位用`（见「小节标题」）`；原话摘录带章节定位；正文图片入 `assets/`，agent 按需多模态读图并入知识点；增加「延伸阅读」区。
- **collection**（X/微博/小红书）：跨条目主题聚类、观点对比、来源标注（作者+链接+日期）；骨架为 主题总览 → 分主题聚合 → 共识与争议 → 关键概念 → 自测 → 元信息；不适用时间戳。

### 5.3 报告骨架（report_template.md 按形态生成）

```
timeline:  一句话总结 → 知识点全览 → 知识详解（段+知识点）→ 关键概念表
           → 金句与观点 → 原话摘录（带时间点）→ 学习路径与自测 → 元信息
document:  一句话总结 → 知识地图（章节树）→ 知识详解（按章节）
           → 关键概念表 → 金句与观点 → 原文摘录（章节定位）
           → 学习路径与自测 → 延伸阅读 → 元信息
collection:主题总览 → 分主题聚合（观点对比+来源）→ 共识与争议
           → 关键概念表 → 自测 → 元信息
```

### 5.4 SKILL.md 结构

```
# everything-learn
├── 支持平台表（URL 模式 → 适配器 → 形态）
├── 首次配置（硬性：用户自选报告根路径；vision 复检模式自选）
├── 工作流（定位 → 获取 → 复检/转写 → 落盘 → 撰写 → 交付）
├── 自进化引导（无适配器平台 → 询问用户 → 尽调 → 现场搭建，见 §6）
├── 共用规则 + 形态专属规则
└── 交付（命名/目录/汇报/glossary 校验）
```

## 6. 自进化适配器机制（与 bilibili-learn 的核心差异化）

### 6.1 触发与询问

当用户输入一个**没有匹配适配器**的平台/链接时：

1. **不直接失败**：agent 识别「无适配器」状态；
2. **主动询问用户**：「这个平台还没有适配器，是否按 everything-learn 规范现场搭建一个？」
3. 用户确认 → 进入尽调流程；用户拒绝 → 退出并说明当前支持列表。

### 6.2 可行性尽调清单（先尽调、后搭建）

**在写任何代码之前**，agent 必须完成并输出以下尽调结论（防止"先搭建才发现不可达"）：

| # | 尽调项 | 要回答的问题 |
|---|---|---|
| 1 | 内容形态 | 视频 / 音频 / 图文 / 帖子？入口 URL 模式是什么？ |
| 2 | 获取方式 | 官方 API（需 key？）/ 公开接口 / HTML 解析 / RSS？ |
| 3 | 鉴权需求 | 无 / cookie / OAuth / 付费 API？（影响配置项设计） |
| 4 | 风控强度 | 无 / 限流 / 签名参数 / 验证码 / 设备指纹？（影响稳定性与维护成本） |
| 5 | 内容可得性 | 原生字幕？自动字幕？音视频可下载？正文可提取？需浏览器渲染？ |
| 6 | 结论 | **可自动化**（进入搭建）/ **半自动**（仅手动导出内容后处理）/ **不可行**（说明原因并给替代方案） |

### 6.3 适配器接入规范（`docs/ADAPTER_SPEC.md`）

强制要求（契约测试会逐项校验）：

1. **接口**：实现 §3.2 的 `SourceAdapter` 全部方法；`kinds` 必须与实际形态一致。
2. **归一化**：`fetch_content` 必须返回 timeline / document / collection 之一，字段符合 §4.2 契约。
3. **错误处理**：可重试异常（限流/网络）走 `core.retry`；不可达/解析失败返回明确状态（`failed`/`no_content`），不得抛裸异常穿透流水线。
4. **凭据**：只从 `config.auth.<platform>` 或环境变量读取；**禁止硬编码**；cookie 文件路径进 `.gitignore`。
5. **注册**：经 `providers/registry.py` 的 `@register` 自动发现，无需改动 core。
6. **测试（必须齐全）**：
   - 单元：fixture 离线解析测试（HTML/JSON/字幕样例入库 `tests/fixtures/<platform>/`）；
   - 契约：共享 contract 测试（形状 / 归一化 / 异常路径）全过；
   - 冒烟：真实链接手动验证一次，记录链接与结果（不进 CI）。
7. **依赖**：新增第三方依赖需在 README 注明（如 yt-dlp），保持可选/懒加载，未安装时给出明确安装提示。

### 6.4 模板与注册

- `docs/ADAPTER_TEMPLATE.md`：逐步接入指南（对着写即可）。
- `providers/_template.py`：骨架代码（含注释与占位实现）。
- `providers/registry.py`：适配器注册表 + 按 URL 路由。

### 6.5 生成流程与验收

```
无匹配 → 询问用户 → 尽调结论（用户确认）
      → 生成 providers/<name>.py + tests/fixtures + contract 测试
      → 离线测试全绿 → 真实链接冒烟 → 注册生效
      → 提交（一次提交只做"新增一个适配器"这件事）
失败回滚：冒烟不通过不允许合并；尽调判定不可达则停止并说明。
```

## 7. 错误处理

| 环节 | 失败处理 |
|---|---|
| 路由 | 无匹配 → §6 自进化引导；名称多候选 → 列表让用户选 |
| 元信息 | 限流退避重试 → 仍失败：`failed` + 退出码 + 日志 |
| timeline 字幕 | 分级 ok/suspect/invalid → vision 复检 → Whisper；退出码沿用 3/4/5 |
| document 提取 | 重试 → 仍失败：保存 `assets/content_raw.html` 兜底（`source=raw_html`，agent 可直读） |
| 转写 | GPU→CPU 回退、VAD 策略、模型镜像提示（现有） |
| vision | 未配置/失败 → 跳过，不阻塞（现有） |
| 批量 | 失败不中断 + `--resume` + 汇总标记（现有） |

所有降级写入 `content_info.json.source` 与日志。

## 8. 测试策略

- **迁移基线**：bilibili-learn 现有 160 个测试随拆分迁移（改 import 路径），作为回归基线。
- **适配器契约测试**：共享 harness，所有适配器必须通过同一组形状/归一化/异常测试。
- **离线样例**：新平台 fixture 解析测试，不联网；网络层 mock（现行 FakeSession 模式）。
- **真实冒烟**：每个新平台落地手动跑一次真链接（与 bilibili-learn v3.4 的做法一致）。
- 目标：`pytest tests/` 离线全绿。

## 9. 配置项（独立 `scripts/config.json`）

```json
{
  "out_dir": "",
  "whisper_model": "medium",
  "whisper_vad": "auto",
  "vad_long_sec": 1800,
  "api_retries": 4,
  "api_retry_base": 1.5,
  "auth": {"bilibili": {}, "zhihu": {}, "youtube": {}},
  "vision": {
    "enabled": false, "base_url": "", "api_key": "", "model": "gpt-4o-mini",
    "max_frames": 30, "max_rounds": 2, "subtitle_check": "auto"
  }
}
```

`config.json` 与 cookie 文件均 gitignore；`--out` / `--model` 等 CLI 参数优先。

## 10. 里程碑与验收

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| M0 | 仓库骨架 + core 迁移 + B站适配器 + 测试迁移 | 原 160 测试全绿；B站端到端回归通过 |
| M1 | YouTube 适配器（yt-dlp：字幕/自动字幕/音轨） | YouTube 链接 → 同规格中文报告 |
| M2 | 播客适配器（RSS/音频） | 播客 → 时间轴报告 |
| M3 | 知乎适配器（document 形态） | 知乎 → 章节式报告 |
| M4+ | TED / Medium / 公众号 / X / 微博 / 小红书…（逐个走 §6 尽调） | 每平台：契约测试 + 冒烟 + 报告 |

## 11. 平台可行性矩阵（初步，逐里程碑尽调后细化）

| 平台 | 形态 | 获取方式（预期） | 鉴权 | 风险 | 里程碑 |
|---|---|---|---|---|---|
| B站 | 视频 | 官方 API + cookie（已有实现） | cookie | 中（接口偶发错乱，已有防线） | M0 |
| YouTube | 视频 | yt-dlp（字幕/自动字幕/音轨） | 大多无需 | 中（yt-dlp 需跟进更新） | M1 |
| 播客（Apple/小宇宙等） | 音频 | RSS / 网页接口（尽调确认） | 视平台 | 低-中 | M2 |
| 知乎 | 图文 | 网页/接口 + cookie | cookie | 中高（风控） | M3 |
| TED | 视频 | 官网公开字幕 | 无 | 低 | M4 |
| Medium | 图文 | RSS / 公开页 | 无 | 低 | M4 |
| 微信公众号 | 图文 | 需尽调（搜狗/第三方/手动导出） | 视方案 | 高 | M5 |
| X | 帖子 | 官方 API（付费）/ 抓取 | key | 高 | M6 |
| 微博 | 帖子 | 公开接口 + cookie | cookie | 中高 | M6 |
| 小红书 | 帖子/视频 | 签名风控强 | 复杂 | 高 | M7 |

> 任何平台在开工前必须完成 §6.2 尽调；矩阵为预期，不代表已确认可达。

## 12. 风险与开放问题

- **平台风控变化**：适配器需可快速修复；自进化机制同样用于"修复"场景（用户报告平台失效 → 重新尽调）。
- **yt-dlp 依赖**：需声明版本与升级提示；YouTube 改版频繁时属于可接受维护成本。
- **document 提取选择器脆弱**：`raw_html` 兜底 + 尽调时评估页面结构稳定性。
- **社媒聚合报告尚无先例**：M6/M7 前先出小样验证报告形态（沿用本流程重新 brainstorm 该子形态）。
- **开放问题**：待用户评审时确认（本 spec 的 review 环节）。
