---
name: everything-learn
description: 将多平台学习内容（视频/音频/图文/帖子）总结为中文学习报告。触发词：「总结这个视频/文章/播客」「学习笔记」「把内容整理成学习报告」等，用户提供链接、名称或平台内容标识。当前支持 B 站、YouTube、播客、知乎、TED、Medium、微博、抖音、微信公众号、X（Twitter）、小红书；遇到没有适配器的平台可按规范现场搭建适配器。
---

# everything-learn：多平台学习内容总结

## 职责
定位用户指定的内容（链接 / 名称）→ 路由到平台适配器 → 提取内容（字幕来源分级 + 同源校验；AI 字幕以 Whisper 主选；档位窗口复检）→ 撰写**中文详解学习报告** → 保存到用户自选的学习笔记根目录。

> 定位说明：`bilibili-learn` 是只做 B 站的轻量版；本技能是全量版（B 站是其参考适配器），配置与仓库独立。

## 支持的平台与形态

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
| X（Twitter） | `document`（推文） | ✅ 可用（单推文：正文/图片/口播转写；主页时间线 scan（需登录 + 浏览器代理）；搜索不做） |
| 小红书 | `document`（笔记） | ✅ 可用（单笔记：正文/图片；视频笔记附口播转写；链接需含短时效 token；主页/收藏枚举：`scan`，需登录） |
| 抖音 | `timeline`（视频） | ✅ 可用（单视频：**浏览器上下文取 detail（B1）** + Whisper；主页作品/收藏/喜欢列表：浏览器辅助 scan（收藏/喜欢需登录），不逆向签名） |
| 名单外平台 | — | 走「自进化」流程（见 §3） |

## 0. 首次配置（硬性规则）

1. **必须询问用户选择报告根路径**（建议新建一个总文件夹，如「学习笔记」）：
   - **未配置 `out_dir` 时 `el.py run` 会拒绝运行**——不要替用户决定路径，也不要有默认路径；
   - 用户选定后写入 `scripts/config.json` 的 `out_dir`（可在运行时用 `--out` 临时覆盖）。
2. **Whisper 模型**：默认 `medium`；用户可 `--model tiny/small/large-v3` 降级或升级。
3. **字幕保真与多模态复检（建议配置）**：在 `scripts/config.json` 配置 `vision`（OpenAI 兼容端点）后启用；
   - **来源策略（默认行为，不可绕过）**：AI/自动字幕不再直接入库 → Whisper 主选（AI 字幕留作双 ASR 交叉比对第二意见）；人工/官方/转录稿 → 先过同源校验（音频抽查），通过才采信、失败转 Whisper；`none` → 直接 Whisper；
   - **复检档位**由 `recheck.tier_policy` 控制（`auto` 评分 / `off`=档3 不检 / `tier1` 定向 / `tier2` 全量），**以用户选择为准**；
   - **预算闸门**：预估金额超过免打扰阈值（`recheck.auto_approve_amount`，默认 0.3）或超长视频（`confirm_over_sec`）时展示预算卡（完整档2 / 帽内尽力 / 降档1）；可用 `--budget <金额>` 设本次运行复检总上限、`--yes-recheck` 预授权；
   - 未配置 vision 或调用失败时：档位复检自动跳过/降级（异常窗口记录后继续；同源校验不受影响），不阻塞原流程。
4. **VAD 策略**：长视频（≥ `vad_long_sec`，默认 1800 秒）默认禁用 VAD；`--vad` 手动开启、`--no-vad` 强制关闭。
5. **需要 cookie 的平台（知乎 / B 站 / 微博 / 抖音）**：优先用内置导出器
   `python scripts/el.py cookie zhihu|bilibili`（等价入口 `scripts/cookie_export.py`；Playwright + 系统 Edge/Chrome，首次在窗口内登录一次；需 `pip install playwright`）；
   也可手动把浏览器的 cookie 整行写入 `scripts/.zhihu_cookie` / `scripts/.bili_cookie`。cookie 失效时适配器会给出明确提示。
   - **新平台接入（标配）**：在 `providers/<name>.py` 声明 `COOKIE_EXPORT`（字段表见 `docs/ADAPTER_SPEC.md` §4），导出器自动发现（`python scripts/el.py cookie --list` 可查），无需改工具。

## 1. 定位
- 链接 / 平台标识：`python scripts/el.py resolve <输入>` 确认平台与标识；
- 名称类输入：走平台搜索（B 站 `el.py search <关键词>`），**列出候选让用户确认**后再继续；
- **公众号搜索（实验性）**：`el.py search --platform wechat "<关键词>"` 列出候选；直接抓取用 `el.py run --platform wechat "<关键词>" --pick N`（搜狗结果链接**会话绑定**，必须同进程立即抓取，不能跨进程复用）。
- **账号级扫描（M11/M12）**：用户主页/收藏夹/频道/我的收藏等列表链接 → `el.py scan <链接> [--filter] [--priority] [--limit] [--snapshot]` 先枚举与筛选（命中项可 `--snapshot` 存档）；批量抓取用 `el.py run <链接> --all`（或 `--pages "1-10"`）；微博收藏入口 `https://weibo.com/u/favorites`（需 cookie）；微博关注 `https://weibo.com/u/page/follow/<uid>`；公众号合集（album 链接）；B站 UP主 `https://space.bilibili.com/<mid>`；Medium 列表/书签：`scan "https://medium.com/@<handle>/list/<slug>"`（需登录）；B站账号数据另有 `el.py toview|history|following`（需 cookie）。

## 2. 获取内容
```bash
python scripts/el.py run <链接/名称> [--page N | --pages "1-10" | --all] --out <根目录>

# 复检策略与预算（M13）
python scripts/el.py run <链接> --recheck auto|1|2|3   # auto 评分 / 1 定向 / 2 全量 / 3 不检
python scripts/el.py run <链接> --no-recheck           # = --recheck 3（显式豁免，产物标注）
python scripts/el.py run <链接> --budget 1.5           # 本次运行复检金额总上限（超出自动转「帽内尽力」）
```
- **timeline 源**（B站/YouTube/播客）：取字幕并声明来源分级（`human/official/auto/unknown/none`）→ AI/自动字幕转 Whisper 主选（保留作双 ASR 交叉比对）→ 人工/官方/转录稿先过同源校验（失败转 Whisper）→ 评分引擎定档（档1 定向 / 档2 全量 / 档3 白名单默认关闭）→ 预算闸门 → 窗口复检（3 帧/秒，标注【画面补充】【画面校正】）→ 音轨 Whisper 兜底（GPU 自适应，语言自动/提示透传，长视频默认禁 VAD）；
  - **YouTube 细节**：人工字幕优先（含中文翻译轨）；自动字幕为滚动式，已做重复清洗并标注「字幕(自动)」；自动翻译轨 429 时自动回退其它语言轨；媒体直链带时效（调用时即取）
  - **播客细节**：RSS/Apple Podcasts 链接（Apple 经 iTunes 查找换成 feed）；有 `podcast:transcript` 先过同源校验（通过采信，失败转 Whisper）；纯音频无画面层，档位复检记 `n/a`
- **document 源**（知乎/公众号/微博）：正文提取（章节/段落/公式/图片）→ 图片入 `assets/`；问题链接用 `--all` 展开首屏 5 条回答（深分页需 API 签名，本期不做）；
  - **公众号细节**：单篇链接直连（无需 cookie；正文服务端渲染、图片 `data-src` 自动入 `assets/`）；搜狗搜索为实验性路径（验证码/限流风险 + 签名链接会话绑定）；
  - **X（Twitter）细节**：单推文直连（无需 cookie，syndication 公开接口）；图片入 `assets/`；视频推文自动取 mp4 并 Whisper 转写为「视频口播内容」小节（多语自动检测）；缓速 10s（限流会升级，遇 429/连接异常时给可读提示，不要高频重试）；
  - **小红书细节**：单笔记直连（`xhslink` 短链自动展开）；正文/标签/图片入 `assets/`；视频笔记自动取 mp4 并 Whisper 转写「视频口播内容」；**依赖 `xsec_token`（短时效）**——分享链自带优先，缺失/失效时 explore 兜底现取（实验性，命中率低）；都没有则提示重新分享；缓速 12s；
- 产物：三件套（`content_info.json`（元信息+来源标记+`recheck` 摘要）/ `content.txt`（timeline 带时间戳，含【画面补充】【画面校正】标注）/ `report_template.md`（按形态的报告骨架））+ timeline 的 `recheck_report.json`（档位/评分/预算/同源校验/交叉比对/不确定项全量留痕）；
- 批量：失败不中断，汇总增量写入，`--resume` 断点续跑；
- 抽帧供读图复检（agent 执行时）：`python scripts/el.py frames <视频> --range "起-止" --fps 1 --out <目录>`。

## 3. 自进化：无适配器平台
当输入的 URL **没有被任何适配器匹配**时（路线表内的平台由项目按里程碑接入；名单外平台走本流程）：

1. **不要直接拒绝**，先询问用户：「这个平台还没有适配器，是否按规范现场搭建一个？」
2. 用户确认后，按 `docs/ADAPTER_SPEC.md` 完成**可行性尽调**（内容形态/获取方式/鉴权/风控/可得性/结论），把结论表展示给用户；
3. 用户确认「可自动化」后，按 `docs/ADAPTER_TEMPLATE.md` + `scripts/providers/_template.py` 现场实现，并完成测试三件套（fixture 单测 + 契约测试 + 真实链接冒烟）；其中**多模态复检通道为必接项**（实现 `fetch_media_url(media="video", lowest=True)`，复检逻辑复用 core，禁止适配器内自建；契约测试会校验签名）。
4. 冒烟不通过不合入；尽调判定不可达则停止并说明原因与替代方案。

> CLI 行为：`run`/`scan` 遇到**未识别 URL** 会直接给出本流程提示（不再兜底成 B 站名称搜索）；`python scripts/el.py platforms` 可查当前已注册名单。

## 4. 撰写报告（agent 核心工作）

读取产物目录中的三件套，补全 `report_template.md` → 另存为 `<标题>_学习报告.md`。**以下为硬性规则**：

### 共用规则（所有形态）
- **R1 知识点完整性（最高优先级，不可协商）**：覆盖内容中**每一个知识点**。
  撰写前通读全部内容（长内容分段读完，**不得跳读**），边读边提取知识点清单；撰写后对照 `content.txt` 逐段核对；
  **不设篇幅上限**，禁止以「控制上下文/成本」为由省略、概括或跳过；长内容宁可分多轮、多会话完成。
- **R4 浓缩去水**：删除寒暄、过渡语、重复解释、与知识无关的闲聊，只保留知识本身。
- **R5 学习地图**：报告的「知识点全览」= 按出现顺序编号的全部知识点清单（学习前预览覆盖范围），不遗漏。
- **R6 自测**：学习路径与自测 = 按知识点维度的收获总结 + 每 3~5 个知识点一道自测题（附参考答案位置提示）。
- **R7 趣味彩蛋（默认关闭）**：仅当用户明确要求（幽默点/彩蛋/有趣的点）时才从内容中摘录趣味素材；未提及一律省略。
- **R8 官方+通俗双版本**：每个知识点先给「定义」（标准表述）再给「通俗」（一句话新手视角）。
- **R9 延伸标注（含 R9.1 深度）**：讲解中出现的新概念用「延伸」字段标注解释；主流/高级知识点深入 2~4 条（概念+使用场景），简单知识点 1 条或省略；宁少而深，不浅而泛。
- **R10 交付命名**：报告文件名含内容标题（`<标题>_学习报告.md`），按作者归档。
- **R11 名词注释（书籍式脚注）**：每个知识点末尾附「**名词注释**」，对前置未提及的专业名词逐条注释；
  先查 `references/glossary.json`（命中直接引用），未命中新写并 `python scripts/glossary.py add` 沉淀；
  交付前运行 `python scripts/glossary.py check <报告.md> <content.txt>` 校验到 0 遗漏。
- **R12 复检执行者选择**：默认由流水线自动执行（配置 vision 后按档位复检，产物 `recheck_report.json` 留痕）；未配置 vision 时——① 当前模型可读图 → 用 `python scripts/el.py frames <视频> --range "起-止" --fps 1 --out <目录>` 抽帧自行核对，遗漏补入并标注「画面补充」；② 否则询问用户是否配置 vision API；③ 都不行则跳过（不阻塞），按 R13 如实标注。
- **R13 复检透明（必守）**：撰写报告前核对产物目录 `recheck_report.json`；
  - 保留正文中的【画面补充】【画面校正】标记并在报告中解释来源；
  - 对 `uncertain`/未复检区间如实说明，不得把「不确定」静默写成结论；
  - 档位/覆盖/同源校验结论写入报告「复检说明」小节。
- 各形态均保留：关键概念/术语表、金句与重要观点。

### timeline 专属（B站/YouTube/播客）
- **R2 时间戳分点**：按时间轴切段（每段 3~10 分钟），`### 段N｜[起-止] 主题`；
- **R3 知识点分点**：段内每个知识点独立分点（`#### 知识点N：名称（[时间]）`），含要点 → 细节（2~4 条）→ 例子（如有）；
- **原话摘录**：挑 5~10 条有教学价值的原话，逐条附时间点；
- **多模态复检（配置 vision 后固定路径）**：按档位窗口抽帧（3 帧/秒、静帧去重）核对画面遗漏（白板公式/PPT/代码/图表）与口误/同音字，补充/校正以【画面补充】【画面校正】写入转写；
  经验证据：Whisper 转写 + 未复检 = 报告可能缺失关键公式与结论（M13 冒烟：美食视频 5 处画面校正、教育类视频复检覆盖不足已记录）；复检执行者选择见 R12。

### document / collection（规划中）
- `document`：按原文章节切段（`### 段N｜章节名`），引用用「小节标题」定位；报告骨架已实现（一句话总结 → 知识地图 → 知识详解 → 概念表 → 金句 → 原文摘录 → 自测 → 延伸阅读）；正文图片在 `assets/`，agent 按需多模态读图并入知识点；
- `collection`（社媒聚合）：跨条目主题聚类 + 观点对比 + 来源标注——M6 起实现。

## 5. 交付
- 单条：`<out_dir>/<作者>/<YYYY-MM-DD>_<标题>[_P##]/<标题>_学习报告.md`；
- 合集：`<out_dir>/<作者>/<YYYY-MM-DD>_<标题>_合集/`（`P01…` 子目录 + 汇总 `content_info.json` + 总报告骨架）；
- 交付前完成 R11 的 glossary 校验；向用户汇报报告摘要（覆盖知识点数/来源/路径），有【画面补充】【画面校正】或未复检区间时一并说明（R13）；
- timeline 产物含 `recheck_report.json`（档位/评分理由/预算/同源校验/交叉比对/复检明细与不确定项），交付前核对。

## 注意事项
- 本文件不写死任何本机路径；一切以用户配置的 `out_dir` 为准。
- 平台接口偶发错乱（实测 B 站 AI 字幕串台：美食视频拿到「可控核聚变」字幕）：来源分级丢弃 AI 字幕转 Whisper 主选、双 ASR 交叉比对量化分歧、人工/官方字幕过同源校验会自动处理，不要绕过。
- **YouTube 提取失败**（反爬更新/限流）先升级依赖：`pip install -U yt-dlp`（`el.py doctor` 会显示当前版本）；字幕接口 429 已内置退避重试。
- **知乎风控**：短时间大量请求会触发临时限流（40362，通常几十分钟自行恢复）；适配器会给出可读提示。日常使用用浏览器 cookie 路径即可，不要高频循环抓取。
- **抖音**：detail 接口已对非浏览器调用硬拦（直连 403 Argus/Uifid）→ 取数走真实浏览器上下文（本机需 Playwright + 持久化 profile：`el.py cookie douyin` 打开窗口一次）；风控严格勿高频；scan 已内置 SPA 水合等待。
- 搜索接口偶发风控：失败时提示用户改用链接。
- **X（Twitter）**：单推文无需 cookie；限流会跨宿主升级（429 → SSL EOF，含图片/视频 CDN）；缓速默认 10s，被拒时退避一次即给可读提示，不要循环重试；时间线与搜索不做（实测不可靠）。
- **小红书**：依赖短时效 `xsec_token`（旧 token 失效 → sec-404）；连发 note 请求会 sec-404，缓速 12s；无 token 时 explore 兜底命中率低（如实提示重新分享）；账号级能力（主页/收藏）列为后续方向（M11）。
- **公众号**：单篇链接无需 cookie；搜狗搜索为实验性（验证码/antispider 风险，结果链接会话绑定、不可跨进程复用）；页面「参数错误/系统出错」等异常会给可读提示。
