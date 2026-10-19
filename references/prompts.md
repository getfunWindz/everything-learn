# 典型提示词（5 个主场景）

> 每条提示词都可直接照抄。**不要省略 `everything-learn` 这个锚点**——它是稳定触发本技能、避免被当成「普通总结请求」的关键。
> 完整工作流与报告规则见 `SKILL.md`；用户向演示页见独立仓库 `everything-learn-demo`。

## 场景 1｜找内容（搜索候选）

**定位**：只要候选列表，不抓取、不生成报告。
**提示词（可照抄）**

```text
用 everything-learn 帮我找几个讲 DeepSeek V4.1 Flash 的 B 站视频，要新一点的
```

**期望行为**

1. 判定模式：搜索；不追问报告格式。
2. 执行：`python scripts/el.py search --platform bilibili "DeepSeek V4.1 Flash" --limit 5`。
3. 返回：候选表（标题 / UP 主 / 时长 / 播放 / 链接），说明「搜索按平台执行，换平台再说一句」。
4. 停止点：候选给完即停，等你决定要不要抓取。

**不该发生**：自动 `run`、自动建目录、自动生成报告、追问 md/doc/docx/txt。

**变体说法**

- 用 everything-learn 搜下 B 站有没有讲 DeepSeek V4.1 Flash 的视频，先给我列表
- 用 everything-learn 找几个 DeepSeek V4.1 Flash 的 B 站视频

**真实产物**：`el.py search --platform bilibili ... --json` 的候选表（标题/作者/时长/播放/URL）。

## 场景 2｜抓取中转（先别总结）

**定位**：把内容（字幕/正文）落盘，报告以后再说。
**提示词（可照抄）**

```text
用 everything-learn 把这个抖音视频的字幕存下来，先别总结
```

（后面跟链接或 App 分享文本）

**期望行为**

1. 判定模式：抓取；只确认输出根目录（已配置 `out_dir` 则直接用）。
2. 执行：`python scripts/el.py run "<链接>" --no-recheck`（= 档3 不检，产物标注豁免）。
3. 产物：`<out_dir>/<作者>/<标题>/` 下的 `content_info.json`、`content.txt`、`report_template.md`。
4. 停止点：汇报路径、字数/行数、字幕来源（人工/官方/自动/Whisper）与 `quality_hint`。

**不该发生**：生成 `<标题>学习报告.md`、跑 R1–R13、问报告格式。

**变体说法**

- 用 everything-learn 只要这个视频的字幕和正文，别生成报告
- 用 everything-learn 先把这条内容落盘，报告以后再说

**真实产物**：`content.txt`（timeline 带 `[mm:ss]`）+ `content_info.json`（platform/item_id/source/language/quality）。

## 场景 3｜学习报告（docx）

**定位**：完整报告链路：格式 → 复检档位/预算 → 转写 → R1–R13 → 门禁 → 导出。
**提示词（可照抄）**

```text
用 everything-learn 把这个 B 站课件整理成能复习的学习报告，要 docx
```

**期望行为**

1. 判定模式：报告；格式已给（docx）就不再重复问。
2. 转写与来源分级：AI 字幕 → Whisper 主选 + 双 ASR 交叉比对；人工/官方 → 同源校验。
3. 复检：评分定档（如档2 全量）；金额超阈值先出预算卡（上界 / 实发双口径），确认后执行。
4. 报告：按 R1–R13 写（不设篇幅上限）；`report_check` 通过；`glossary check` 0 遗漏。
5. 导出：`python scripts/el.py export "<产物目录>" --format docx`。
6. 汇报：知识点数/字数/自测题数、复检覆盖（如 43/43 窗口）、【画面校正】/【画面补充】计数、预算、路径。

**不该发生**：跳过复检却不标注；把基线报告当交付；虚构内容；不跑 `report_check`。

**变体说法**

- 用 everything-learn 给这个视频出一份学习报告，Word 格式
- 用 everything-learn 总结这个 B 站课件，我要 docx

**真实产物**：`<标题>学习报告.docx` + `.md`；`recheck_report.json`。

## 场景 4｜账号级批量（收藏 → 合集）

**定位**：列表/收藏 → 枚举筛选 → 批量抓取 → 合集报告。
**提示词（可照抄）**

```text
用 everything-learn 把我微博收藏里跟 AI 相关的都抓下来，整理成一份合集
```

**期望行为**

1. 判定模式：批量；先 `python scripts/el.py scan "<收藏链接>"` 枚举（需登录 cookie；失败先 `python scripts/el.py cookie weibo --auto`）。
2. 筛选：`--filter "AI,大模型,DeepSeek" --priority`；把命中数量与前几条标题给你确认范围与成本。
3. 批量：`python scripts/el.py run "<收藏链接>" --all`（断点续跑 `--resume`；失败不中断，汇总增量）。
4. 产物：`<out_dir>/<作者>/<标题> 合集/`（`P01…` 子目录 + 汇总 `content_info.json` + 合集学习报告）。
5. 停止点：汇总成功/失败/跳过数量与路径。

**不该发生**：未登录硬抓、高频请求触发风控、静默失败不汇总。

**变体说法**

- 用 everything-learn 先看看我微博收藏里有哪些 AI 内容，再批量抓
- 用 everything-learn 把我的微博收藏批量抓成合集报告

**真实产物**：合集目录 + `P01…P41` + `<标题> 合集学习报告.md`。

## 场景 5｜知识库（检索 / 入库）

**定位**：先查记忆，再决定是否入库；不触发抓取。
**提示词（可照抄）**

```text
用 everything-learn 查一下我以前记过光刻相关的内容吗
```

**期望行为**

1. 若 `config.kb.enabled=false`：先问「要开启内建知识库吗？」，不静默抓取、不假装有结果。
2. 检索：`python scripts/el.py kb search "光刻"`（可加 `--platform/--since`），返回 snippet + 路径。
3. 入库分支（用户说「入库」时）：`python scripts/el.py kb index "<产物目录>"`；术语用 `python scripts/glossary.py clean/alias/add`。
4. 完整 wiki（digest/实体/主题/图谱）：路由 `bilibili-to-wiki`，本技能不实现。
5. 停止点：结果或入库完成即止。

**不该发生**：未启用 KB 就编造命中；把 KB 检索当成全网搜索。

**变体说法**

- 用 everything-learn 搜一下我的笔记里有没有 EUV 光刻
- 用 everything-learn 把这期报告入库，顺便更新术语

**真实产物**：`kb search` 的命中（标题 / 片段 / 路径）。
