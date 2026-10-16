# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与语义化版本。

## [0.2.1] - 2026-10-16

**Backlog 清空（B1/B2/B3）**：抖音取数路径修复、视频帖档位复检、缓存 provenance。

### Fixed

- **抖音取数**：detail 直连接口被 Argus 硬拦（`403 Uifid Not Found`）→ 改走浏览器上下文（browserfetch 拦截页面自身请求 + 页内 fetch 兜底；yt-dlp 保留兜底）；媒体选择支持最低码率档（抽帧省带宽）。`scan` 修 SPA 水合时序（点击 tab 前等待），生产复核枚举通过。

### Added

- **视频帖 lite 档位复检**（X / 小红书）：`ContentResult.video_lines/video_duration` → pipeline document 分支复用确定性评分（`gather_signals/decide_tier`）产出 `recheck_report.json` 与 `content_info.recheck`（窗口级复检为后续项，`lite/skipped` 如实标注）；document 报告模板增「复检说明」小节。

### Changed

- **缓存 schema v3**：payload 持久化 `provenance`/`language`/`recheck`/`recheck_report`；命中缓存时产物恢复相关字段并补写 `recheck_report.json`；v2 旧缓存失效。

### 测试

- **707 项离线测试**（B1/B2/B3 新增 16 项）。

## [0.2.0] - 2026-10-16

**字幕保真与复检优先级（M13）**：AI 字幕不再直接入库；人工/官方字幕须过同源校验；复检分档执行且成本透明。

### Breaking（默认行为变化）

- **AI/自动字幕不再直接进入报告**：一律转 Whisper 主选；AI 字幕仅作为双 ASR 交叉比对的「第二意见」，串台/错字不再进入产物。
- **人工 / 官方 / 第三方转录稿先过同源校验**：音频抽查（3 点 × ~12s，Whisper `small`）不通过即丢弃字幕、转 Whisper 全量。
- **旧字幕防线退役**：各适配器内置的字幕分级 / 旧多模态复检原语删除，校验能力统一收敛到 `core/recheck.py`；`vision.subtitle_check` 旧键退役（仅保留 `off` 降级映射到 `recheck.tier_policy`）；缓存 schema 升版（v2），旧缓存条目失效（不绕过新复检）。

### Added（新增）

- **来源分级**：`ContentResult.provenance`（human/official/auto/unknown/none）与 `language`；适配器声明 `language_hint`/`has_video`。
- **同源校验**：验证字幕是否来自该视频音轨（防串台/整段错配）；无音频流时降级为时间对齐多点视觉归属兜底。
- **档位评分引擎**：时长/知识点密度/分散度/空档/画面引导词/来源风险 → 档1（定向）/ 档2（全量）/ 档3（白名单，默认关闭）。
- **预算闸门**：金额区间预测（±30%）+ 免打扰阈值（默认 0.3）+ A/B/C 决策；`--budget` 运行级总额上限、`--yes-recheck` 预授权；非交互默认「帽内尽力」。
- **窗口复检**：3 帧/秒（头/中/尾）、窗口化、静帧去重；输出【画面补充】【画面校正】并保留原句。
- **双 ASR 交叉比对**：auto 字幕 vs Whisper 按窗比对（一致率/分歧区间落盘），分歧区优先进入档1 复检。
- **产物透明**：新增 `recheck_report.json`（档位/评分/预算/同源/交叉比对/不确定项）；`content_info.json` 增 `recheck` 摘要；报告骨架新增「复检说明」小节（SKILL R13）。
- **CLI**：`--recheck auto|1|2|3`、`--no-recheck`、`--budget <金额>`、`--yes-recheck`。

### Fixed（修复）

- vision 真实调用因 `requests.post(trust_env=...)` 崩溃 → 改用 `Session.trust_env`（`0a50b93`）。
- Whisper 兜底未传语言（固定 zh）→ `--lang > 适配器 language_hint > 自动检测`（`7a25184`）。
- 真实性：B 站 AI 字幕串台（实测美食视频拿到「可控核聚变」字幕）被来源分级 + 交叉比对拦截（一致率 0%、分歧覆盖全片）。

### 测试

- **664 项离线测试**（全部 mock 网络，约 2 分钟）；新增复检评分/同源/闸门/产物透明度覆盖。

## [0.1.0] - 2026-10-14

首个产品化版本：**11 个平台适配器 + 账号级扫描 + 反爬基座 + 自进化流程**。

### 平台支持

- **B 站**：视频 / 多 P / 收藏夹 / 稍后再看 / 历史 / 关注 / UP 主视频列表（wbi 签名）
- **YouTube**：人工字幕优先（含中文翻译轨）/ 自动字幕滚动去重 / 媒体直链 / 429 退避
- **播客**（RSS / Apple Podcasts）、**TED**、**知乎**（文章 / 回答 / 问题 / 收藏夹）
- **微博**（单帖 / 时间线 / 我的收藏 / 关注）、**抖音**（单视频 / 主页 / 收藏 / 喜欢）
- **微信公众号**（单篇 / 合集 album）、**X / Twitter**（单推文 / 主页时间线）
- **小红书**（单笔记 / 主页 / 收藏）、**Medium**（文章 / feed / 书签列表）

### 能力

- 统一契约与流水线：`timeline` / `document` 两种内容形态；字幕分级校验（ok/suspect/invalid）+ 多模态 3 帧复检 + Whisper 兜底
- 账号级扫描：`el.py scan`（枚举 → 过滤 → 优先级 → 快照）与 `el.py run --all` 合集报告
- 反爬基座：平台级缓速（throttle）、退避重试、cookie 一键导出（Playwright + 系统浏览器）
- 浏览器辅助取数：Cloudflare / 签名平台（Medium / 抖音 / 小红书 / X）统一走 `browserfetch`（持久化 profile）
- 自进化流程：名单外平台 → 尽调（ADAPTER_SPEC §6）→ 搭建 → fixture / 契约 / 冒烟三件套；`el.py platforms` 查名单
- 产物：`content_info.json` / `content.txt` / `report_template.md`（+ `assets/`），报告可 export HTML / DOCX

### 修复（近轮）

- YouTube：自动翻译轨 429 时回退其它语言轨（Karpathy 长视频复测 2955 行完整覆盖）
- Medium：上游停用 `?format=json` → 文章改走 SSR 块抽取；新增书签/列表 scan
- 微博：收藏 / 关注 scan（桌面域 cookie 通道）；合集根目录命名修正

### 测试

- **475 项离线测试**（全部 mock 网络，约 2 分钟）；契约 harness 覆盖全部适配器
