# everything-learn 交接文档（HANDOFF）

> 读完本文即可零上下文接手本项目。最后更新：2026-10-08（`c0e913c`→`0504b84` 之后）。
> 建议新会话开场语：**「阅读 skills/everything-learn/HANDOFF.md 接手，然后按第 8 节启动 M3（知乎）」**

---

## 1. TL;DR

- **项目**：`everything-learn` —— 多平台学习内容总结技能（bilibili-learn 的全量进化版），独立仓库/独立配置/独立技能。
- **状态**：M0（核心迁移+B站）、M1（YouTube）、M2（播客）全部完成；**237 tests green**；三次真实冒烟通过。
- **仓库**：`https://github.com/getfunWindz/everything-learn`；本地即技能目录：`C:\Users\getfunWindz\.pi\agent\skills\everything-learn`。
- **下一个里程碑**：M3 知乎（图文 `document` 形态）——**实现前必须先补 `document` 报告骨架**（当前只有 timeline）。
- **重要外部事项**：`bilibili-learn` 的 v3.4 优化仍在分支 `feature/subtitle-recheck-tuning`（main 旧版）；draft PR #1 已被用户关闭（随手关闭，非拒绝）；需要时并入 main。

## 2. 仓库与目录

```
everything-learn/                     # 本目录 = git repo = pi 技能目录
├── SKILL.md                          # 技能入口（agent 规则：首次配置/自进化/报告规则）
├── README.md                         # 面向用户的说明与命令
├── HANDOFF.md                        # 本文档
├── docs/
│   ├── specs/2026-10-07-everything-learn-design.md   # 设计 spec（已评审通过）
│   ├── plans/2026-10-07-m0-core-and-bilibili.md      # M0 计划
│   ├── plans/2026-10-07-m1-youtube.md                # M1 计划
│   ├── plans/2026-10-08-m2-podcast.md                # M2 计划
│   ├── ADAPTER_SPEC.md               # 适配器规范（含可行性尽调清单——自进化核心）
│   └── ADAPTER_TEMPLATE.md           # 适配器开发指南
├── scripts/
│   ├── el.py                         # CLI 入口（prog=el）
│   ├── core/                         # 平台无关层
│   │   ├── models.py                 # ItemRef/ItemMeta/ContentResult(label)/...
│   │   ├── adapter.py                # SourceAdapter 协议 + SearchNeeded
│   │   ├── registry.py               # register/get/all/match_url/sniff_url/load_providers(自愈扫描)
│   │   ├── config.py                 # 配置 + resolve_out_dir（硬性规则）+ ConfigError
│   │   ├── cache.py                  # 平台化缓存键（platform,item_id,sub_id）
│   │   ├── retry.py                  # retry_with_backoff
│   │   ├── subtitles.py              # parse_vtt / parse_srt（共享）
│   │   ├── artifacts.py              # 产物三件套落盘
│   │   └── pipeline.py               # 通用流水线（缓存→内容→复检→Whisper→落盘）
│   ├── providers/
│   │   ├── bilibili.py               # ApiClient（重试/412）+ BilibiliAdapter + resolver
│   │   ├── youtube.py                # yt-dlp 懒加载 + 字幕(json3/vtt) + 媒体选流
│   │   ├── podcast.py                # RSS/Apple + transcript + enclosure
│   │   └── _template.py              # 适配器模板（下划线开头，不会被注册）
│   ├── transcriber.py                # Whisper（transcribe_url；medium 默认；VAD 策略）
│   ├── vision.py                     # 多模态：归属复检（3帧）/内容复检/抽帧
│   ├── report.py                     # 报告骨架渲染（SUPPORTED_KINDS={"timeline"}→M3 加 document）
│   ├── glossary.py / export.py / doctor.py / favs.py / mergeutil.py
│   └── logs/ cache/ config.json .bili_cookie   # 全部 gitignored
└── tests/                            # 237 项，全部 mock 网络、离线可跑
    ├── contract/                     # 契约 harness（run_contract）
    ├── providers/                    # bilibili / youtube / podcast 适配器测试
    └── fixtures/                     # youtube(json3/vtt) / podcast(rss/srt)
```

## 3. 当前状态总览

| 里程碑 | 内容 | 测试 | 真实冒烟证据 |
|---|---|---|---|
| M0 | core/providers 架构迁移、B站适配器、el.py、ADAPTER_SPEC | 199 | B站字幕路径（BV1164d6qEtA→`source=字幕` 234行）+ Whisper 路径（BV1Mo8R6UEEB→`source=whisper` 82行）+ out_dir 硬性规则 |
| M1 | YouTube 适配器（人工优先/自动字幕滚动去重/媒体直链/429 重试） | 219 | TED `arj7oStGLkU`→`source=字幕`（**中文人工字幕自动优先**）316 行 |
| M2 | 播客适配器（RSS/Apple、sniff 兜底、transcript 优先） | 237 | NPR `feeds.npr.org/500005/podcast.xml`→`source=whisper` 190 行；Apple `id121493675`→lookup→feed→列集（预期 no_subtitle） |

平台矩阵：B站 ✅ / YouTube ✅ / 播客(RSS/Apple) ✅ / 知乎 🚧 M3 / 小宇宙 ❌（尽调不可行） / TED·Medium·公众号·X·微博·小红书 ⏳ 待排期。

## 4. 架构速览

- **分层**：`core/`（平台无关）+ `providers/`（每平台一个适配器）。新平台=只加 `providers/<name>.py`，core 不改（除个例）。
- **三形态契约**：`timeline`（视频/音频，`segments=[{start,end,text}]`）/ `document`（图文，`blocks=[{heading,text}]`）/ `collection`（社媒聚合）。**document 报告骨架尚未实现**（M3 前置）。
- **适配器接口**：`match`（纯字符串，不联网）→ `sniff(url)`（可选，网络探测，仅 match 全未命中时）→ `resolve`（可抛 `SearchNeeded`）→ `list_items`（多集展开）→ `fetch_meta`（**title 用内容级标题用于命名**，分集名放 `page_part`）→ `fetch_content`（`ContentResult`，status: ok/suspect/invalid/empty，`label` 标注来源如 `字幕(自动)`）→ `fetch_media_url(ref, media="audio"|"video", lowest=)`。
- **流水线**：缓存命中 → `fetch_content` → suspect 时多模态 3 帧归属复检（vision 配置时）→ 不通过/无内容则 Whisper → 内容完整性复检 → 产物三件套 + 缓存。
- **产物**：`content_info.json` / `content.txt` / `report_template.md`（+ `assets/`）。**旧名 `video_info.json`/`subtitle.txt` 已全面废弃**。
- **路由**：`registry.match_url` → `registry.sniff_url` → `registry.default()`（bilibili，供名称搜索）。
- **注册表自愈**：`load_providers()` 用扫描注册（不依赖 import 副作用），`registry.reset()` 后仍可恢复；注意模块内 `all` 被 `registry.all` 遮蔽的坑（已用 `builtins.all` 规避）。

## 5. 关键决策（及其缘由，勿轻易推翻）

1. **方案 1：Fork 演进**——bilibili-learn 原样保留为轻量版；两个仓库独立演进，不做共享包（用户明确「有 git，不该多花力气」）。
2. **独立配置/独立仓库**：一切与 bilibili-learn 解耦；`scripts/config.json` 独立。
3. **输出路径硬性规则**：`out_dir` **没有默认值**，未配置时 `el.py` 拒绝运行；SKILL.md 要求 agent 首次必须询问用户选择报告根目录（先建总文件夹）。本机实例用 `C:\data\BaiduSyncdisk\学习笔记\`。
4. **自进化机制（用户核心需求）**：遇到无适配器的平台 → 询问用户 → **先可行性尽调**（`ADAPTER_SPEC.md` §6 六项清单：形态/获取/鉴权/风控/可得性/结论）→ 用户确认后再按规范搭建 → 测试三件套（fixture/契约/冒烟）。
5. **字幕策略**：平台字幕优先（人工 > 自动）；自动字幕滚动去重；转写稿（podcast:transcript）优先于 Whisper；防线分级（ok/suspect/invalid）+ 3 帧归属复检是 B站 v3.4 的成果，已泛化进 core。
6. **Whisper 默认 medium**；长视频（≥`vad_long_sec`=1800s）默认禁 VAD，`--vad` 手动开。
7. **B站重试默认 4 次**（`api_retries`），412 刷新 cookie；YouTube/播客字幕下载 429 用统一 `retry_with_backoff`（3 次）。
8. **Git 习惯（用户）**：直接推 main（main-only 工作流），不建议开诊断/临时分支（曾因 GitHub 服务端 5xx 排障试过，被拒且无残留）；提交用 Conventional Commits 中文 subject。

## 6. 已完成明细（关键提交）

- M0：`6e94310` 基线拷贝 → `293e8b2`..`2972214` core 四件套 → `131a447` transcriber → `38a7b83` B站适配器 → `396f618` 流水线/产物 → `c571e5f` el.py 迁移 → `579472a` 规范/SKILL → `f96e157` cookie 路径修复 → `a06f675`。
- M1：`c0ca1a1` ContentResult.label → `669ec27` YouTube 骨架 → `386f4a8` 字幕+fixtures → `cf2703d` 媒体直链 → `a358668` 429 重试 → `c0e913c` 文档。
- M2：`4f94253` 计划 → `7ec3206` 共享解析 → `c97922d` sniff 兜底 → `6999d2d` 播客适配器 → `4358265` 测试修正 → `0504b84` 文档。

## 7. 已知问题 / 坑 / 回归警示

- **document 报告骨架未实现**：`report.SUPPORTED_KINDS` 目前只有 `timeline`；document 源会被流水线明确判失败。M3 第一步就是实现它（章节式骨架 + `SUPPORTED_KINDS` 增补）。**不要手工拼凑**。
- **YouTube 429**：Karpathy 长视频（2.2MB 自动字幕）在反复冒烟时被 timedtext 限流；适配器直调此前已成功（2758 段），退避重试已加。想复测：`python -X utf8 scripts/el.py run "https://www.youtube.com/watch?v=kCc8FmEb1nY" --out <tmp> --no-whisper`。
- **小宇宙不可行**（已尽调）：首页是 Next.js、无 sitemap、API 未公开；如用户坚持，走自进化流程重新尽调。
- **测试隔离坑**：podcast 的 `match` 会命中含 `/feed`、`.rss`、`.xml`、`podcast` 的路径——写测试用的 URL 要避开这些特征（否则会被真实网络调用）；另有两个只有全量跑才会暴露的问题历史：注册表 resets、`all()` 遮蔽（都已修复，见 §4）。
- **Windows 环境**：脚本用 `python -X utf8` 跑（GBK 控制台 emoji 已在 doctor 修复）；yt-dlp 会提示 Python 3.10 弃用（无害）；tqdm 进度输出偶尔混入（无害）。
- **GitHub MCP 的交互表单**：创建/编辑 PR 类工具会弹 UI 表单，必须用户在界面点击才会生效；agent 侧不要重复调用（会重复弹窗）。
- **bilibili cookie**：`scripts/.bili_cookie`（gitignored，已就位）；失效时 `el.py doctor` 会提示。

## 8. M3 推进 SOP（知乎 / document 形态）

1. **尽调**（照 M1/M2 的表格）：知乎的内容形态（问答/专栏/文章）、获取方式（页面解析 or 公开接口 + cookie）、鉴权（cookie：`auth.zhihu.cookies_file`）、风控（中等偏高）、可得性（正文/图片可提取；评论可选）、结论。
2. **计划**：`docs/plans/2026-xx-xx-m3-zhihu.md`（writing-plans 格式：TDD 任务 + 验收清单）。
3. **实现顺序（重要）**：
   a. **先实现 document 报告骨架**：`report.py` 增加 document 模板（按章节 `### 段N｜章节名`，引用用「小节标题」定位，替代时间戳），`SUPPORTED_KINDS` 加 `"document"`，补测试；
   b. 再实现 `providers/zhihu.py`（`fetch_content` 返回 `ContentResult(kind="document", blocks=[{heading,text}])`）；
   c. 契约 + fixture + 真实链接冒烟（正文提取成功率、图片入 assets/）。
4. **回归与文档**：`python -m pytest tests/ -q` 全绿 → SKILL/README 更新 → `wf_check(execute)` → 推送。

## 9. 环境与依赖

- Python 3.10.11（Windows）；`python -X utf8` 执行更稳。
- 已装：requests、pytest、faster-whisper v1.2.1、yt-dlp 2026.08.19（pip 最新）、GPU CUDA（cuBLAS/cuDNN 就绪）；**无 ffmpeg（不需要）**。
- 首次装 Whisper 模型可设 `HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1`；本地已缓存 medium/small/tiny。
- 配置：`scripts/config.json`（本地已存在，`out_dir=""`、`whisper_model=medium`）；`scripts/.bili_cookie`。

## 10. 常用命令速查

```bash
cd "C:/Users/getfunWindz/.pi/agent/skills/everything-learn"

python -m pytest tests/ -q                     # 237 passed（离线）

python -X utf8 scripts/el.py doctor            # 环境自检（cookie/GPU/whisper/vision/yt-dlp/out_dir）
python -X utf8 scripts/el.py run "<链接>" --out "D:/学习笔记"          # 单条
python -X utf8 scripts/el.py run "<链接>" --all --out "D:/学习笔记"    # 合集/播放列表
python -X utf8 scripts/el.py run "<链接>" --pages "1-3" --resume      # 断点续跑
python -X utf8 scripts/el.py report "<目录>"                          # 重生成报告骨架
python -X utf8 scripts/el.py export "<目录>" --format html|docx

# 冒烟样例（真实链接）：
#   B站：     任何带字幕 BV（如 BV1164d6qEtA）
#   YouTube： https://www.youtube.com/watch?v=arj7oStGLkU（TED，带中文人工字幕）
#   播客：    https://feeds.npr.org/500005/podcast.xml  （短片，Whisper 路径）
#   Apple：   https://podcasts.apple.com/us/podcast/npr-news-now/id121493675
```

## 11. 外部待办

- [ ] `bilibili-learn`：分支 `feature/subtitle-recheck-tuning`（v3.4：字幕多模态归属复检/限流重试/VAD 策略/medium 默认，160 tests）→ main 仍是旧版；draft PR #1 已关闭。**由用户决定**：并 main / 重开 PR / 维持分支。
- [ ] 可选复测：Karpathy 长视频自动字幕（429 退避已就绪）。
- [ ] M3 起按 §8 SOP 推进；更远：TED → Medium → 公众号 → X/微博/小红书（每个都先尽调）。
