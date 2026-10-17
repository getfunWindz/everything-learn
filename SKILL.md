---
name: everything-learn
description: 将多平台学习内容（视频/音频/图文/帖子）总结为中文学习报告。触发词：「总结这个视频/文章/播客」「学习笔记」「把内容整理成学习报告」等，用户提供链接、名称或平台内容标识。当前支持 B 站、YouTube、播客、知乎、TED、Medium、微博、抖音、微信公众号、X（Twitter）、小红书；遇到没有适配器的平台可按规范现场搭建适配器。
---

# everything-learn：多平台学习内容总结

## 介绍

定位用户指定的内容（链接 / 名称）→ 路由到平台适配器 → 提取内容（字幕来源分级 + 同源校验；AI 字幕以 Whisper 主选；档位窗口复检）→ 撰写**中文详解学习报告** → 保存到用户自选的学习笔记根目录。

> 定位说明：`bilibili-learn` 是只做 B 站的轻量版；本技能是全量版（B 站是其参考适配器），配置与仓库独立。

### 支持的平台与形态

| 平台 | 形态 | 状态 |
|---|---|---|
| bilibili | `timeline`（视频） | ✅ 可用 |
| YouTube | `timeline`（视频） | ✅ 可用（人工字幕优先，自动字幕滚动清洗后标注「字幕(自动)」） |
| 播客（RSS / Apple Podcasts） | `timeline`（音频） | ✅ 可用（有转写稿直接解析，否则 Whisper；小宇宙暂不支持） |
| 知乎 | `document`（图文） | ✅ 可用（专栏文章/回答/问题首屏；cookie 可 `python scripts/el.py cookie zhihu` 一键导出） |
| TED | `timeline`（视频） | ✅ 可用（官网官方字幕：多语含简中，zh-cn 优先；无字幕时 mp4→Whisper 兜底） |
| Medium | `document`（图文） | ✅ 可用（双通道：浏览器 SSR 抽取正文 + feed 解析；书签/列表 scan（`@handle/list/<slug>`，需登录）；CF 平台缓速 10s） |
| 微博 | `document`（帖子） | ✅ 可用（单帖：正文/长文/图片/转发；用户时间线/**我的收藏**/**关注** `scan` 需 cookie：`el.py cookie weibo`） |
| 微信公众号 | `document`（图文） | ✅ 可用（单篇链接直连，无需 cookie；**合集（album）scan**；搜狗搜索实验性：`el.py search --platform wechat`） |
| X（Twitter） | `document`（推文） | ✅ 可用（单推文：正文/图片/口播转写；主页时间线 scan（需登录 + 浏览器代理）；搜索实验性：`el.py search --platform x_twitter`，依赖已登录浏览器） |
| 小红书 | `document`（笔记） | ✅ 可用（单笔记：正文/图片；视频笔记附口播转写；链接需含短时效 token；主页/收藏枚举：`scan`，需登录） |
| 抖音 | `timeline`（视频） | ✅ 可用（单视频：**浏览器上下文取 detail（B1）** + Whisper；主页作品/收藏/喜欢列表：浏览器辅助 scan（收藏/喜欢需登录），不逆向签名） |
| 名单外平台 | — | 走「自进化」流程（见「正常调用工作流 · 自进化」） |

### 核心命令

```bash
python scripts/el.py onboard                  # 首次引导门（状态/四项确认点；--complete/--reset/--json）
python scripts/el.py doctor                   # 环境自检（依赖/GPU/模型/vision/cookie/out_dir）
python scripts/el.py run <链接/名称> [--page N | --pages "1-10" | --all] [--model tiny|small|medium|large-v3] [--budget 1.5] [--max-seconds 600] [--report-format md|doc|docx|txt]
python scripts/el.py search <关键词> --platform <平台> [--limit 5] [--json]   # 11 平台搜索（见 platforms 的 search= 列）
python scripts/el.py scan <列表链接> [--filter "关键,词"] [--priority] [--limit N] [--snapshot]
python scripts/glossary.py add "<术语>" "<解释>"   # 术语库填塞/沉淀（可重复；支持 add/alias/clean/absorb）
python scripts/el.py report <目录> [--force]   # 重建骨架 + 基线报告（默认不覆盖已有报告）
python scripts/el.py export <目录> --format html
```

<!-- ⛳ ONBOARDING: 0 -->

> **标签位①（引导状态）**：`1` = 首次引导**未完成** → 必须先执行文末「初次调用逻辑」（四项确认 + `el.py onboard --complete` 置位）；
> `0` = 已完成 → 跳过文末章节，直接按下方工作流正常使用（省 token）。由 `el.py onboard --complete` 自动 1→0。

## 正常调用工作流（每次使用）

### 九步链路

1. **交付格式确认（每次调用必问，必须早于抓取/转写/复检）**：先问用户要哪种报告格式：
   - `md`：Markdown，便于阅读/编辑/入库；
   - `doc`：Word 可直接打开的 `.doc`（Word 兼容 HTML）；
   - `docx`：Word 标准格式；
   - `txt`：纯文本。
   用户回答后再开工；**不得跳过询问、不得默认 md 直接开始转写**（用户明确说「随便/默认」或自动化流水线除外）。
   记录选择并在 `run` 时传 `--report-format <fmt>`；若用户改主意，交付前用 `el.py export <目录> --format <fmt>` 补导。
2. **读标签位**：=1 → 先走文末「初次调用逻辑」；=0 → 继续。
3. **识别平台**：链接/名称 → `el.py resolve` 确认平台与标识（未识别 URL → 自进化流程询问）。
4. **切换适配器与参数**：按平台特性（cookie/代理/缓速）与需求（run/scan/`--all`/页码范围）选定。
5. **引导用户**：内容与范围（多 P/合集/问题回答/页面范围）+ 复检档位与预算（超阈值必现预算卡）+ 凭据需求（cookie）。
6. **获得授权**：用户确认后开工（首次/大额/高风控必须确认；小额自动档按 config）。
7. **执行**：`run`/`scan` → 产物三件套 + `assets/` + `recheck_report.json`。
8. **复检核查**：核对 `recheck_report.json`（档位/覆盖/同源/不确定项）与 `source`/`provenance`/`quality_hint`；必要时抽帧补漏。
9. **撰写报告 + 收工总结与输出核验**：按「报告规则 R1-R13」成稿；按用户选择的格式交付（`run` 已自动导出非 md 格式）；交付前核对产物（文件齐全/字段/命名/glossary 校验），向用户汇报摘要。

### 平台定位与内容获取

- 链接 / 平台标识：`python scripts/el.py resolve <输入>` 确认平台与标识；
- 名称类输入：先用 `el.py search <关键词> --platform <平台>` 找候选（bilibili / wechat / youtube / podcast / 知乎 / 微博 / X / 小红书 / 抖音 / Medium / TED）；
  `run "关键词"` 默认只列候选并退出，确认后用 `--pick N`；非交互场景可 `--yes` 显式取第一条；
  `--json` 输出机读结果；浏览器型平台（知乎/微博/X/小红书/抖音/Medium/TED）依赖本机已登录 profile，会与其它浏览器任务串行。
- **公众号搜索（实验性）**：`el.py search --platform wechat "<关键词>"` 列出候选；直接抓取用 `el.py run --platform wechat "<关键词>" --pick N`（搜狗结果链接**会话绑定**，必须同进程立即抓取，不能跨进程复用）；工具会记录结果指纹，列表漂移时要求 `--yes` 复核。
- **账号级扫描（M11/M12）**：用户主页/收藏夹/频道/我的收藏等列表链接 → `el.py scan <链接> [--filter] [--priority] [--limit] [--snapshot]` 先枚举与筛选（命中项可 `--snapshot` 存档）；批量抓取用 `el.py run <链接> --all`（或 `--pages "1-10"`）；微博收藏入口 `https://weibo.com/u/favorites`（需 cookie）；微博关注 `https://weibo.com/u/page/follow/<uid>`；公众号合集（album 链接）；B站 UP主 `https://space.bilibili.com/<mid>`；Medium 列表/书签：`scan "https://medium.com/@<handle>/list/<slug>"`（需登录）；B站账号数据另有 `el.py toview|history|following`（需 cookie）。

```bash
python scripts/el.py run <链接/名称> [--page N | --pages "1-10" | --all] --out <根目录>

# 复检策略与预算（M13）
python scripts/el.py run <链接> --recheck auto|1|2|3   # auto 评分 / 1 定向 / 2 全量 / 3 不检
python scripts/el.py run <链接> --no-recheck           # = --recheck 3（显式豁免，产物标注）
python scripts/el.py run <链接> --budget 1.5           # 本次运行复检金额总上限（超出自动转「帽内尽力」）
```

- **timeline 源**（B站/YouTube/播客）：取字幕并声明来源分级（`human/official/auto/unknown/none`）→ AI/自动字幕转 Whisper 主选（保留作双 ASR 交叉比对）→ 人工/官方/转录稿先过同源校验（失败转 Whisper）→ 评分引擎定档（档1 定向 / 档2 全量 / 档3 白名单默认关闭）→ 预算闸门 → 窗口复检（3 帧/秒，标注【画面补充】【画面校正】）→ 音轨 Whisper 兜底（GPU 自适应，语言自动/提示透传，长视频默认禁 VAD）；
  - **YouTube 细节**：人工字幕优先（含中文翻译轨）；自动字幕为滚动式，已做重复清洗并标注「字幕(自动)」；自动翻译轨 429 时自动回退其它语言轨；媒体直链带时效（调用时即取）
  - **播客细节**：RSS/Apple Podcasts 链接（Apple 经 iTunes 查找换成 feed；`?i=` 单集用 trackName/slug 在 feed 中定位，避免转写最新一集）；有 `podcast:transcript` 先过同源校验（通过采信，失败转 Whisper）；纯音频无画面层，档位复检记 `n/a`
- **document 源**（知乎/公众号/微博）：正文提取（章节/段落/公式/图片）→ 图片入 `assets/`；问题链接用 `--all` 展开首屏 5 条回答（深分页需 API 签名，本期不做）；
  - **公众号细节**：单篇链接直连（无需 cookie；正文服务端渲染、图片 `data-src` 自动入 `assets/`）；搜狗搜索为实验性路径（验证码/限流风险 + 签名链接会话绑定）；
  - **X（Twitter）细节**：单推文直连（无需 cookie，syndication 公开接口）；图片入 `assets/`；视频推文自动取 mp4 并 Whisper 转写为「视频口播内容」小节（多语自动检测）；缓速 10s（限流会升级，遇 429/连接异常时给可读提示，不要高频重试）；
  - **小红书细节**：单笔记直连（`xhslink` 短链自动展开；也可直接粘贴 App 分享文本，自动提取其中的链接）；正文/标签/图片入 `assets/`；视频笔记自动取 mp4 并 Whisper 转写「视频口播内容」；**依赖 `xsec_token`（短时效）**——分享链自带优先，缺失/失效时 explore 兜底现取（实验性，命中率低）；都没有则提示「App → 分享 → 复制链接」；缓速 12s；
- 产物：三件套（`content_info.json`（元信息+来源标记+`recheck`/`quality`/`glossary` 摘要）/ `content.txt`（timeline 带时间戳，含【画面补充】【画面校正】标注）/ `report_template.md`（按形态的报告骨架））+ timeline 的 `recheck_report.json`（档位/评分/预算/同源校验/交叉比对/不确定项全量留痕）；
- 批量：失败不中断，汇总增量写入，`--resume` 断点续跑；
- 抽帧供读图复检（agent 执行时）：`python scripts/el.py frames <视频> --range "起-止" --fps 1 --out <目录>`。

### 自进化：无适配器平台

当输入的 URL **没有被任何适配器匹配**时（路线表内的平台由项目按里程碑接入；名单外平台走本流程）：

1. **不要直接拒绝**，先询问用户：「这个平台还没有适配器，是否按规范现场搭建一个？」
2. 用户确认后，按 `docs/ADAPTER_SPEC.md` 完成**可行性尽调**（内容形态/获取方式/鉴权/风控/可得性/结论），把结论表展示给用户；
3. 用户确认「可自动化」后，按 `docs/ADAPTER_TEMPLATE.md` + `scripts/providers/_template.py` 现场实现，并完成测试三件套（fixture 单测 + 契约测试 + 真实链接冒烟）；其中**多模态复检通道为必接项**（实现 `fetch_media_url(media="video", lowest=True)`，复检逻辑复用 core，禁止适配器内自建；契约测试会校验签名）。
4. 冒烟不通过不合入；尽调判定不可达则停止并说明原因与替代方案。

> CLI 行为：`run`/`scan` 遇到**未识别 URL** 会直接给出本流程提示（不再兜底成 B 站名称搜索）；`python scripts/el.py platforms` 可查当前已注册名单。

### 报告规则（R1-R13）

读取产物目录中的三件套，补全 `report_template.md` → 另存为 `<标题>学习报告.md`。**以下为硬性规则**：

#### 共用规则（所有形态）
- **R1 知识点完整性（最高优先级，不可协商）**：覆盖内容中**每一个知识点**。
  撰写前通读全部内容（长内容分段读完，**不得跳读**），边读边提取知识点清单；撰写后对照 `content.txt` 逐段核对；
  **不设篇幅上限**，禁止以「控制上下文/成本」为由省略、概括或跳过；长内容宁可分多轮、多会话完成。
- **R4 浓缩去水**：删除寒暄、过渡语、重复解释、与知识无关的闲聊，只保留知识本身。
- **R5 学习地图**：报告的「知识点全览」= 按出现顺序编号的全部知识点清单（学习前预览覆盖范围），不遗漏。
- **R6 自测**：学习路径与自测 = 按知识点维度的收获总结 + 每 3~5 个知识点一道自测题（附参考答案位置提示）。
- **R7 趣味彩蛋（默认关闭）**：仅当用户明确要求（幽默点/彩蛋/有趣的点）时才从内容中摘录趣味素材；未提及一律省略。
- **R8 官方+通俗双版本**：每个知识点先给「定义」（标准表述）再给「通俗」（一句话新手视角）。
- **R9 延伸标注（含 R9.1 深度）**：讲解中出现的新概念用「延伸」字段标注解释；主流/高级知识点深入 2~4 条（概念+使用场景），简单知识点 1 条或省略；宁少而深，不浅而泛。
- **R10 交付命名**：报告文件名含内容标题（`<标题>学习报告.md`），按作者归档。
- **R11 名词注释（书籍式脚注）**：每个知识点末尾附「**名词注释**」，对前置未提及的专业名词逐条注释；
  先查 `references/glossary.json`（命中直接引用），未命中新写并 `python scripts/glossary.py add` 沉淀；
  交付前运行 `python scripts/glossary.py check <报告.md> <content.txt>` 校验到 0 遗漏。
- **R12 复检执行者选择**：默认由流水线自动执行（配置 vision 后按档位复检，产物 `recheck_report.json` 留痕）；未配置 vision 时——① 当前模型可读图 → 用 `python scripts/el.py frames <视频> --range "起-止" --fps 1 --out <目录>` 抽帧自行核对，遗漏补入并标注「画面补充」；② 否则询问用户是否配置 vision API；③ 都不行则跳过（不阻塞），按 R13 如实标注。
- **R13 复检透明（必守）**：撰写报告前核对产物目录 `recheck_report.json`；
  - 保留正文中的【画面补充】【画面校正】标记并在报告中解释来源；
  - 对 `uncertain`/未复检区间如实说明，不得把「不确定」静默写成结论；
  - 档位/覆盖/同源校验结论写入报告「复检说明」小节。
- 各形态均保留：关键概念/术语表、金句与重要观点。

#### timeline 专属（B站/YouTube/播客）
- **R2 时间戳分点**：按时间轴切段（每段 3~10 分钟），`### 段N｜[起-止] 主题`；
- **R3 知识点分点**：段内每个知识点独立分点（`#### 知识点N：名称（[时间]）`），含要点 → 细节（2~4 条）→ 例子（如有）；
- **原话摘录**：挑 5~10 条有教学价值的原话，逐条附时间点；
- **多模态复检（配置 vision 后固定路径）**：按档位窗口抽帧（3 帧/秒、静帧去重）核对画面遗漏（白板公式/PPT/代码/图表）与口误/同音字，补充/校正以【画面补充】【画面校正】写入转写；
  经验证据：Whisper 转写 + 未复检 = 报告可能缺失关键公式与结论（M13 冒烟：美食视频 5 处画面校正、教育类视频复检覆盖不足已记录）；复检执行者选择见 R12。

#### document / collection（规划中）
- `document`：按原文章节切段（`### 段N｜章节名`），引用用「小节标题」定位；报告骨架已实现（一句话总结 → 知识地图 → 知识详解 → 概念表 → 金句 → 原文摘录 → 自测 → 延伸阅读）；正文图片在 `assets/`，agent 按需多模态读图并入知识点；
- `collection`（社媒聚合）：跨条目主题聚类 + 观点对比 + 来源标注——M6 起实现。

### 交付

- 单条：`<out_dir>/<作者>/<标题>[ P##]/<标题>学习报告.md`（`run` 自动生成基线；按用户选择的格式同时导出 `.doc/.docx/.txt`；agent 可覆盖深化；目录不带日期前缀，标题只保留文字/数字/空格）；
- 合集：`<out_dir>/<作者>/<标题> 合集/`（`P01…` 子目录 + 汇总 `content_info.json` + `<标题> 合集学习报告.md` 基线）；
- 交付前完成 R11 的 glossary 校验；向用户汇报报告摘要（覆盖知识点数/来源/路径），有【画面补充】【画面校正】或未复检区间时一并说明（R13）；
- timeline 产物含 `recheck_report.json`（档位/评分理由/预算/同源校验/交叉比对/复检明细与不确定项），交付前核对。

### 注意事项

- 本文件不写死任何本机路径；一切以用户配置的 `out_dir` 为准。
- **文件名规范**：目录/文件名只保留文字、数字、空格（连字符/下划线/斜杠转空格，emoji/中英文标点去除）；空标题回退「未命名」。
- **报告本体**：`run` 会生成机械基线报告；交付前应按 R1-R13 深化（基线不替代知识点完整性要求）。
- 平台接口偶发错乱（实测 B 站 AI 字幕串台：美食视频拿到「可控核聚变」字幕）：来源分级丢弃 AI 字幕转 Whisper 主选、双 ASR 交叉比对量化分歧、人工/官方字幕过同源校验会自动处理，不要绕过。
- **YouTube 提取失败**（反爬更新/限流）先升级依赖：`pip install -U yt-dlp`（`el.py doctor` 会显示当前版本）；字幕接口 429 已内置退避重试；媒体下载已内置多候选 + 断点续传 + 时长预算（弱网/代理抖动下自动降级，不阻塞主流程）；yt-dlp 提取设 `socket_timeout=20` + 重试上限。**YouTube 自动字幕默认直接采信并标注「未交叉校验」**（媒体预算 45s）；需强制 Whisper 校验用 `--whisper-auto`。
- **转写语言**：默认自动检测（B 站/抖音不再硬编码 zh）；英文/多语视频会按实际语言转写，`content_info.language` 回填检测结果；需要强制时用 `--lang`。
- **视觉复检可观测**：逐窗打印 `复检窗口 i/N`；`recheck_report.checked_windows` 区分「已检查」与「未覆盖」；推理型 vision 模型（如 deepseek-flash）把 max_tokens 吃满时会自动加倍重试（默认 8192，最多 3 次）。
- **校正置信度分级**：每条校正带「高/中/低」；低置信只留痕（`windows.kind=low_confidence`）不改正文，避免图表多行数字串行误改。
- **预算口径**：`budget.estimate` 是闸门上界（3fps 计划帧），`budget.actual` 按去重后实发帧重算（常低 5~10 倍）；`spent_total` 按上界保守累计。
- **断点续跑**：`--resume` 会复用 `.recheck_ckpt/` 中已完成窗口（区间+转写哈希匹配），中断后只补未完成窗口。
- **浏览器串行**：知乎/微博/Medium/X/小红书/抖音共用一个 profile，工具用文件锁串行化；另一任务占用时会等待/提示，不要并发跑浏览器型平台。
- **文档质量提示**：正文过短 / 图片为主 / 结尾截断 / Medium 会员墙会写 `content_info.quality_hint`（报告需说明覆盖率不足）；Medium CF 命中 403/429 会冷却重试 1 次。
- **知乎风控**：短时间大量请求会触发临时限流（40362，通常几十分钟自行恢复）；适配器会给出可读提示。日常使用用浏览器 cookie 路径即可，不要高频循环抓取。
- **抖音**：detail 接口已对非浏览器调用硬拦（直连 403 Argus/Uifid）→ 取数走真实浏览器上下文（本机需 Playwright + 持久化 profile：`el.py cookie douyin` 打开窗口一次）；风控严格勿高频；scan 已内置 SPA 水合等待。
- 搜索接口偶发风控：失败时提示用户改用链接。
- **X（Twitter）**：单推文无需 cookie；限流会跨宿主升级（429 → SSL EOF，含图片/视频 CDN）；缓速默认 10s，被拒时退避一次即给可读提示，不要循环重试；主页时间线 scan 与搜索为实验性（依赖已登录浏览器 + 代理），失败时改用链接。
- **小红书**：依赖短时效 `xsec_token`（旧 token 失效 → sec-404）；连发 note 请求会 sec-404，缓速 12s；**搜索结果为 token 化链接，可直接 run**；token 过期时用 App 分享文本（会自动提取）或重新搜索；explore 兜底命中率低（如实提示）；账号级能力（主页/收藏）列为后续方向（M11）。
- **公众号**：单篇链接无需 cookie；搜狗搜索为实验性（验证码/antispider 风险，结果链接会话绑定、不可跨进程复用）；**requests 被拦时自动走浏览器兜底**（搜狗搜索 + 文章渲染，`#js_content`/`#js_image_desc`）；页面「参数错误/系统出错」等异常会给可读提示。
- **低配模式（2026-10-17）**：仅必要依赖（无 faster-whisper / yt-dlp / playwright 等）时——字幕平台自动降级采信并标注「低配：未经同源校验」+ `quality_hint` 强制推荐；whisper 平台给出安装引导；画面复检依赖（Pillow/PyAV）缺失时自动提前跳过。转写档位低于 medium 时产物带 `quality.tier_note` 常驻建议（实机验证 medium 效果最佳）。
- **术语库反哺链（2026-10-17）**：每次转写按 `references/glossary.json` 的勘误别名自动筛验术语名（`content_info.glossary` 留痕；存疑项不替换、如实标注）；画面校正对命中术语库 → 自动沉淀别名；报告写作 R11 沉淀新术语；`glossary.py absorb <wiki>` 吸收知识库。初启可手动填塞术语；推荐配套 `bilibili-to-wiki`。

<!-- ⛔ READ-GATE: ONBOARDING: 0 -->

> **标签位②（阅读门）**：上方 ONBOARDING = `0` → **到此为止，不要阅读下方章节**（已引导完成，直接正常使用）；
> = `1` → **必须读完下方并执行**「初次调用逻辑」。

## 初次调用逻辑（首次部署 / 引导未完成时）

### A. 自动环境检查

先运行 `python scripts/el.py doctor`（或 `el.py onboard`），向用户汇总：依赖（faster-whisper / yt-dlp / playwright）、GPU、whisper 模型缓存、vision、cookie、out_dir。
（doctor 的 cookie 为逐平台状态：B 站真实探针；知乎/微博/抖音按文件龄期标「未验证/过期风险」，不要当成已登录。）

### B. 四项确认点（必须逐项向用户输出并确认）

1. **产出目录（硬性，必须由用户自选）**
   - 建议新建一个总文件夹（如「学习笔记」）；**未配置 `out_dir` 时 `el.py run` 会拒绝运行**——不要替用户决定路径，也不要有默认路径；
   - 用户选定后写入 `scripts/config.json` 的 `out_dir`（运行时可用 `--out` 临时覆盖）。
2. **Whisper 档位（质量/速度取舍；须解释并给出建议）**
   - `fast = small`（**出厂默认**，速度快、资源小）｜ `medium`（**强烈推荐：实机验证效果最佳**）｜ `large-v3`（最高，资源要求高）；
   - 根据用户硬件给出选择；**强烈建议 medium**；用户可 `--model tiny/small/medium/large-v3` 随时调整。
3. **依赖清单（推荐全部安装以获得完整体验）**
   - `el.py onboard` 会列出缺失项与安装命令；逐项解释用途：
     `faster-whisper`（Whisper 转写；时间线平台需要）｜ `yt-dlp`（YouTube/抖音）｜ `playwright`（cookie 一键导出 / browserfetch 平台）｜ `vision`（多模态画面复检，配置后启用）；
   - 推荐全部安装；装不了的按低配模式使用（见「注意事项 · 低配模式」）。
4. **使用说明总结（用途 + 典型用法 + 产物）**
   - 用途：多平台学习内容 → 中文详解学习报告（11 平台，见「介绍」表）；
   - 典型用法：`el.py run <链接> [--all/--pages/--model/--budget]`、`el.py scan <列表链接>`、`el.py report/export`；
   - 产物：三件套 + `assets/` + `recheck_report.json`；报告规则见「正常调用工作流 · 报告规则」。
5. **术语库（可选填塞 + 自动反哺告知 + 配套推荐）**
   - 询问用户是否愿意填入自己的常用术语（领域/项目名词）→ 逐条 `python scripts/glossary.py add "<术语>" "<解释>"`（可选）；
   - **告知自动反哺机制**：每次转写按勘误库筛验术语名（画面校正自动沉淀别名）；报告写作沉淀新术语；知识库 `absorb` 吸收——术语库随使用逐渐丰富；
   - 推荐配套 skill `bilibili-to-wiki`（个人知识库填充接口，已适配本技能产物：任意支持平台链接 → 知识库）。

### C. 配置要点（引导时解释；按用户选择写入 config）

- **字幕保真与多模态复检（建议配置）**：配置 `vision`（OpenAI 兼容端点）后启用；
  - **来源策略（默认行为，不可绕过）**：AI/自动字幕不再直接入库 → Whisper 主选（AI 字幕留作双 ASR 交叉比对第二意见）；人工/官方/转录稿 → 先过同源校验（音频抽查），通过才采信、失败转 Whisper；`none` → 直接 Whisper；
  - **复检档位**由 `recheck.tier_policy` 控制（`auto` 评分 / `off`=档3 不检 / `tier1` 定向 / `tier2` 全量），**以用户选择为准**；
  - **预算闸门**：预估金额超过免打扰阈值（`recheck.auto_approve_amount`，默认 0.3）或超长视频（`confirm_over_sec`）时展示预算卡（完整档2 / 帽内尽力 / 降档1）；可用 `--budget <金额>` 设本次运行复检总上限、`--yes-recheck` 预授权；
  - 未配置 vision 或调用失败时：档位复检自动跳过/降级（异常窗口记录后继续；同源校验不受影响），不阻塞原流程。
- **VAD 策略**：长视频（≥ `vad_long_sec`，默认 1800 秒）默认禁用 VAD；`--vad` 手动开启、`--no-vad` 强制关闭。
- **需要 cookie 的平台（知乎 / B 站 / 微博 / 抖音）**：优先用内置导出器
  `python scripts/el.py cookie zhihu|bilibili`（等价入口 `scripts/cookie_export.py`；Playwright + 系统 Edge/Chrome，首次在窗口内登录一次；需 `pip install playwright`）；
  也可手动把浏览器的 cookie 整行写入 `scripts/.zhihu_cookie` / `scripts/.bili_cookie`。cookie 失效时适配器会给出明确提示。
  - **新平台接入（标配）**：在 `providers/<name>.py` 声明 `COOKIE_EXPORT`（字段表见 `docs/ADAPTER_SPEC.md` §4），导出器自动发现（`python scripts/el.py cookie --list` 可查），无需改工具。

### D. 置位（引导完成）

四项确认全部完成后，运行：

```bash
python scripts/el.py onboard --complete
```

→ 标签位① 与标签位② 同步 `1 → 0`（SKILL.md）+ `config.onboarded = true`；此后不再触发引导，直接进入「正常调用工作流」。
（需要重新引导：`python scripts/el.py onboard --reset`。）
