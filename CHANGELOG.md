# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与语义化版本。

## [0.4.23] - 2026-10-18

### Added

- **首次引导六项确认**：out_dir / Whisper / 依赖 / 使用说明 / **内建知识库** / 术语库；`onboard --kb on|off` 写 `config.kb.enabled` + `config.kb.asked`，`asked=true` 后不再重复询问；`onboard --complete` 要求知识库已选择。
- **意图优先工作流**：搜索 / 抓取中转 / 报告 / KB 检索 / KB 入库 / 完整 wiki / 混合模式；每步可停止/跳过；报告格式只在报告模式询问。
- **KB 与术语反哺接入工作流**：`kb search` 检索、`kb index/build` 入库、`glossary clean/alias/add/absorb` 术语链；完整 llm-wiki digest/图谱路由 `bilibili-to-wiki`。

### Changed

- README/SKILL 同步六项确认、KB 特征位（enabled/asked）、意图路由与停止点；报告 R1–R13 明确仅报告模式生效。
- `setup` 设置 `kb.asked=true`；`--yes` 未显式选 KB 时保留既有 enabled；`kb.asked` 默认 false。

### Tests

- **950 项离线测试**（948 passed / 2 skipped；新增 onboard KB 选择/完成门禁、setup = asked、CLI 引导等）。

## [0.4.22] - 2026-10-18

### Added

- **KB v2 检索**：FTS5（优先 trigram，中文子串）+ LIKE 回退；snippet 命中上下文；bm25/命中次数排序；`--platform/--author/--kind/--since` 过滤。
- **KB 运维命令**：`el.py kb index <目录>`（深度报告改写后重索引）、`prune`、`delete`、`rebuild`；`build` 默认清理失效记录。
- **KB 去重**：`doc_key = platform:item_id:sub_id`（缺失回退 path）；同一条目多路径保留内容更大的副本；合集 `kind=batch` 默认跳过（`--include-collections` 可包含）。
- **KB schema v2**：新增 `item_id/sub_id/published/mtime/doc_key` 与 FTS 表；旧库自动重建（源报告不受影响）。

### Fixed

- 删除/移走笔记后知识库仍返回旧记录（`kb build` 现在会 prune）。
- 深度报告覆盖基线后 KB 仍是旧内容（交付流程增加 `kb index`）。
- LIKE 查询的 `%`/`_` 未转义导致匹配全部；短中文查询（<3 字）FTS 不命中。
- 合集汇总与 P 条目重复索引；同一条目多路径重复。

### Changed

- `kb build` 输出改为「已扫描 N 个目录；知识库 M 条；清理 P 条」；`kb stats` 显示 FTS 状态。
- README/SKILL 增加知识库运维与过滤示例；final-status 更新 KB 能力/限制说明。

### Tests

- **945 项离线测试**（KB 新增 12 项：去重、合集跳过、prune、深度重索引、FTS/LIKE、过滤、转义、delete/rebuild、schema v1 重建、CLI、FTS 回退、直接 prune）；Windows 下 2 项 POSIX chmod 用例跳过。

## [0.4.21] - 2026-10-18

### Added

- **公开仓库随包分发适配器规范**：`docs/ADAPTER_SPEC.md` / `docs/ADAPTER_TEMPLATE.md` 不再被 `.gitignore` 排除，自进化流程在公开克隆中可直接执行。
- **`report_check` 节名别名**：兼容 R5/R6 旧称（知识点全览 / 学习路径与自测），避免按 SKILL 字面写标题被误杀。

### Fixed

- **报告模板/基线契约统一**：模板与自动基线使用 `## 学习地图（知识点全览）`、`## 自测（每 3~5 个知识点一题）`，与 `report_check` 一致；文档平台无复检时写 `n/a`，不再引用不存在的 `recheck_report.json`。
- **基线报告**：文档平台知识点编号连续（不再按正文行号跳号）；一句话总结/金句排除标题行。
- **`setup`**：`--yes` 必须显式 `--out`，不再默认写 `~/学习笔记`；`write_user_config` 深合并，保留既有 `kb.path`。
- **`doctor`**：faster-whisper/yt-dlp 的 `find_spec` 成功但 import 失败时给可读提示，不再 traceback。
- **导出**：缺 `python-docx` 时抛 `RuntimeError`（CLI 可读退出），`export.py` 补 `import sys`（GBK 控制台保护真正生效）。
- **`glossary.py`**：`--help` 可用；补模块级 `import sys`。
- **`split`**：无时间轴内容不再打印 `None–Nones`。
- **未知平台 URL**：不再先弹 B 站 cookie 警告，直接给自进化提示。
- 清理未使用导入/变量（`ruff check scripts --select F` 全绿）。

### Changed

- **`requirements.txt`**：`faster-whisper` 归入可选依赖；README 明确视频/播客再安装，图文平台只装核心依赖。
- **文档对齐**：README/SKILL/HANDOFF 更新为 12 平台、931 passed / 2 skipped（933 collected）、跨平台 CI、无日期目录名、基线报告需深化的说明。

### Tests

- **933 项离线测试**（新增 14 项：配置深合并、setup 硬性 out_dir（含交互）、导出缺依赖、doctor 导入失败、模板/基线契约、report_check 别名、基线编号、split 文档输出、onboard 计数、glossary --help、未知 URL 警告）。Windows 下 2 项 POSIX chmod 用例跳过。

## [0.4.20] - 2026-10-18

### Added

- **报告证据回溯**：`report_check` 新增金句引号逐字回溯、知识点时间戳不超出 content 时长；`--strict` 统一 fail（默认 warn）。
- **平台 canary**：`scripts/canary.py`（12 平台固定样本；默认 meta、`--full` 含内容；子进程隔离/超时/JSON；必需失败退出 1）。实测 12/12 通过。
- **长内容分块**：`el.py split <目录> --chunk-chars` → `content.partNN.md` + `split_manifest.json`。
- **安装分发**：`pyproject.toml` + `scripts/__init__` 路径引导 + `everything-learn` console script（editable 安装实测可用）。
- **秘密存储**：cookie/config/kb 文件 POSIX `chmod 600`。

### Changed

- **版本单一来源**：`scripts/version.py`（0.4.20）；`el.py --version`；report 复用。

### Tests

- **919 项离线测试**（新增 11 项：quotes/timestamps（含省略号分段）、split、canary、版本一致性、POSIX 权限；Windows 下 2 项 chmod 用例跳过）。

## [0.4.19] - 2026-10-18

### Added

- **V2EX 适配器（自进化实战）**：`document` 形态，主题正文 + 前排 30 条回复；公开 API（`/api/topics/show.json`）+ 页面解析，无需 cookie；缓速 6s。用 `gen_contract_test.py` 生成契约测试后再补 fixture 单测与真实冒烟（7s 完成）。

### Fixed

- **重复 setup 子命令**：Python 3.11 argparse 会抛 `conflicting subparser`（3.10 静默覆盖）；删除重复注册。

### Tests

- **908 项离线测试**（新增 5 项：V2EX 契约/解析/内容）。
- **跨平台验证**：WSL Ubuntu + Python 3.11 全量 **908 passed**；GitHub Actions 6 个矩阵任务（Windows/macOS/Ubuntu × 3.10/3.11）全部通过。

## [0.4.18] - 2026-10-18

### Added

- **微博/抖音登录态自动化**：headless 复用 profile（实测删除文件后各 1s 写回；抖音 59 项含 `odin_tt`）；微博补 `reload_auth`；无显示环境支持 `el.py cookie <p> --import <file>` / `--cookie-string`（Netscape / `k=v;…` / JSON）。
- **`el.py setup` 一键初始化**：依赖检查（`--install` 显式同意安装）、报告目录、知识库开关、可选登录；写入配置并完成引导置位。
- **内建知识库（SQLite）**：`el.py kb build/search/stats`；`setup` 询问是否启用，开启后 `run` 自动索引（失败不影响主流程）。
- **报告引用回溯**：`report_check.py --strict` 将「带单位数字无法在 content.txt 找到」判 fail（默认 warn；换算/派生数字不误杀）。
- **自进化**：模板补齐 `reload_auth`/`fetch_media_urls`/平台字段/cookie 导入说明；新增 `scripts/gen_contract_test.py` 生成契约测试。
- **跨平台 CI**：GitHub Actions 矩阵扩为 Windows/macOS/Linux × Python 3.10/3.11（shell: bash，PYTHONUTF8=1）。

### Tests

- **903 项离线测试**（新增 15 项：cookie 解析/导入、setup、kb、引用回溯、契约生成器）。

## [0.4.17] - 2026-10-18

### Added

- **登录态全自动恢复**：`cookie_export.auto_export` 用 headless 持久 profile 读取已登录 cookie（实测删文件后 1s 自动写回，无窗口）；未登录且非交互时快速失败并给一次性登录指引。`core.auth.ensure` 进程内去重；pipeline 对 `fetch_meta/fetch_content/list_items` 的认证错误自动恢复并重试（provider `reload_auth`）。B 站原生搜索 -101 同样自动重试。新增 `el.py cookie <p> --auto`。
- **浏览器自动选择**：cookie 导出与 browserfetch 均按 `msedge → chrome → chromium` 回退，profile 按实际 channel 隔离。

### Fixed

- **控制台编码**：`el.py`/`cookie_export`/`glossary`/`export`/`report_check` 统一 `errors="replace"`，GBK 终端不再因 ✅/⚠ 崩溃。
- **root meta 认证重试**：`run_input` 取根元信息时也走自动恢复。

### Tests

- **888 项离线测试**（新增 10 项：auto_export 命中/非交互/交互回退、浏览器回退、auth 判定/去重/缓存失败、pipeline 重试与 reload、cookie --auto CLI）。

## [0.4.16] - 2026-10-18

### Fixed

- **`report_check.py` 兼容 GBK 控制台**：Windows 默认编码下 `✓/❌` 会触发 `UnicodeEncodeError`；输出改为 ASCII `[ok]/[pass]/PASS/FAIL`，任意终端可用。

### Tests

- 878 项离线测试（CLI 用例兼容，无新增）。

## [0.4.15] - 2026-10-18

### Added

- **`scripts/report_check.py`：R1–R13 交付前结构自检**——sections / garble / placeholder / numbering / point_structure / map / selftest / quotes / glossary 九项检查，fail 非零退出；支持 `--json`。实测 43:50 课件（50 知识点）与 29min 播客（23 知识点）均通过。

### Tests

- **878 项离线测试**（新增 8 项：结构/编号/自测密度/乱码/占位符/术语/CLI）。

## [0.4.14] - 2026-10-18

### Fixed

- **画面校正解析**：支持「原转写「X」（两处）→「Y」」与「原「X」→「Y」」；Prompt 要求 X 必须逐字复制本窗转写、找不到时改输出「不确定」（实测 43:50 课件 7 条 `correction_unmatched` 中 1 条由此可正确应用）。
- **`recheck_report.json` 新增 `summary`**：supplements / corrections / low_confidence / unmatched / uncertain 计数，R13 交付可直接核对。

### Tests

- **870 项离线测试**（新增 2 项：带括注/省略「转写」的校正解析）。

## [0.4.13] - 2026-10-18

### Fixed

- **术语校验器英文词边界**：`pi` 不再命中 `pipeline`、`KDA` 不再命中 `backdata`（英文术语须独立成词，允许复数 s/es）；中文仍按子串。
- **名词注释列表写法识别**：R11 校验支持 `- **名词注释**：` 形式，避免按知识点分条时注释被漏检。

### Tests

- **868 项离线测试**（新增 3 项：词边界匹配、覆盖校验不误报、列表注释识别）。

## [0.4.12] - 2026-10-18

### Added

- **小红书 token 缓存（6h）**：成功取数后写入 `scripts/cache/xhs_tokens.json`；无 token / token 失效链接先复用缓存（实测第二次运行 1s 成功）。
- **explore 兜底升级**：requests 之外增加登录态浏览器滚动收集（虚拟列表，20s 约 95 个 `note_id→token`）；按 id 命中率仍低（实测跨会话 0/1，41s 失败），首见无 token 链接仍建议 App 分享。

### Tests

- **865 项离线测试**（新增 6 项：浏览器滚动命中/上限、requests 命中不启浏览器、缓存写入/复用/过期）。

## [0.4.11] - 2026-10-18

### Changed

- **复检计划行标注「上界」**：`复检计划：N 窗 / 预估 X USD（上界，按帧去重后通常更低）`，避免把 3fps 理论上界误读为实际花费。
- **档 2 实测标定补全**：新增 7:51 / 10:08 / 43:50 三档（100% 覆盖、0 错误）。实测实际为上界的 8%–44%（去重 2.5×–66×）；43:50 课件 102 校正 + 140 补充、$0.1217、31m25s。详见 `docs/plans/2026-10-17-tier2-calibration.md`。

## [0.4.10] - 2026-10-18

### Fixed

- **YouTube 慢链路空耗 3 分钟（归因纠正）**：实测 yt-dlp info 1.4–3.7s、字幕 0.2–10s；231s 的 run 里 225s 是「同源校验整段音频 180s + Whisper 45s」预算被慢链路耗尽。新增媒体测速闸门（首轮 512KB / 15s 窗口平均 <64KB/s → 立即判不可用）+ 同域冷却（同一 run 内后续同域候选/Whisper 重下立即跳过）；实测 TED 人工字幕视频 **231s → 17s**，Karpathy 自动字幕 12s。
- **storyboard 污染音频候选（真 bug）**：YouTube `formats` 里的 `sb0`–`sb3`（ytimg mhtml，`vcodec=none`）被当成音频候选下载 → Whisper 报 `tuple index out of range`；现按 `acodec/ext/protocol` 过滤（`pick_audio_url` 与 `fetch_media_urls` 同步修复）。

### Tests

- **859 项离线测试**（新增 6 项：慢流快失败、15s 探测窗口、健康速率不误杀、同域冷却、storyboard 过滤 ×2）。

## [0.4.9] - 2026-10-18

### Fixed

- **公众号正文质量闸门 + 浏览器兜底重试**：请求侧正文 <20 字才触发浏览器；浏览器结果同样过可用性检查（≥20 字或 ≥1 图）且不得比请求侧更短，否则不采信并给出可读原因；浏览器首屏无 `#js_content` 时重载一次（第二遍多等 1.5s），并把「环境异常 / 需微信客户端打开」翻译成可读提示。
- **测试离线隔离**：公众号错误页用例不再启动真实浏览器。

### Tests

- **853 项离线测试**（新增 4 项：可用性闸门、浏览器重试、放弃时错误可读、过短兜底拒绝）。

## [0.4.8] - 2026-10-17

### Added

- **交付格式确认（每次调用必问）**：SKILL 工作流新增第 1 步，先询问用户 `md / doc / docx / txt`，再开始抓取/转写/复检；`run --report-format` 自动导出所选格式。
- **四种交付格式**：新增 `doc`（Word 兼容 HTML）与 `txt`（去 Markdown 标记）；`export --format md|html|docx|doc|txt`。

### 测试

- **849 项离线测试**（新增 3 项：txt/doc 导出、run 格式落地）。

## [0.4.7] - 2026-10-17

### Changed

- **文件夹命名去掉日期前缀/下划线**：单条目录改为 `<作者>/<标题>`（标题只保留文字/数字/空格；重名加空格序号 ` 2`）；合集改为 `<标题> 合集`；报告文件改为 `<标题>学习报告.md`。不再出现 `2026-10-17_...` 这类结构前缀。
- **YouTube 放宽策略**：自动字幕直接采信并标注 `字幕(自动)（未交叉校验）`；媒体预算从 180s 收到 45s；需要强制交叉校验时用 `--whisper-auto`。
- **微信公众号浏览器兜底**：搜狗 requests 验证码 → 浏览器搜索；签名链接 requests「环境异常」→ 浏览器渲染文章（`#js_content` / `#js_image_desc`）。实测恢复 3/3 抓取。

### 测试

- **846 项离线测试**（新增 6 项：命名/抖音音轨/微信兜底/YouTube 策略/浏览器锁清理）。

## [0.4.6] - 2026-10-17

### Fixed

- **抖音无音轨误选（真 bug）**：`fetch_media_url(media="audio")` 选了最低码率的 DASH 纯视频流（`media-video-*`），faster-whisper 抛 `tuple index out of range`。现优先选 muxed（含音轨）流；实测 2:31 教程视频正常转写。
- **21min 档 2 全程完成**：43/43 窗口、100% 覆盖、0 错误；31m12s、上界 0.732 USD / actual 0.325 USD；15 处画面校正、118 处补充。

### 测试

- **840 项离线测试**（新增 1 项：抖音 muxed 选择）。

## [0.4.5] - 2026-10-17

**报告本体与文件名规范化**：修复「输出目录只有骨架、没有报告」；严格文件名净化。

### Added

- **自动基线报告**：每次 `run`（含缓存命中/单条/合集）自动生成 `<标题>_学习报告.md`（由 content.txt 机械整理：总结/知识全览/分段详解/金句/自测/复检说明），杜绝“只有 report_template.md”；agent 可在此基础上按 R1-R13 深化，重跑默认不覆盖已有报告；`el.py report [--force]` 可重建。
- **合集基线**：批量完成后生成 `<合集>_合集学习报告.md`（拼接各 P 基线报告）。
- **严格文件名净化**：目录/文件名只保留文字、数字、空格（连字符/下划线/斜杠转空格；emoji/中英文标点全部去除），空标题回退「未命名」。覆盖作者目录、标题目录、报告文件名、合集目录、缓存键。

### 测试

- **839 项离线测试**（新增 4 项）。

## [0.4.4] - 2026-10-17

**立项批 4**：Medium CF 重试、跨平台质量矩阵、X 视频口播样本。

### Added

- **文档质量矩阵（B10）**：`core.quality.document_quality_hint()` 统一口径（会员墙 > 有口播 > 过短 > 图片为主 > 截断），pipeline 文档分支接入并写 `content_info.quality_hint`；12 项参数化矩阵覆盖 5 平台 × 3 类。
- **Medium CF 重试（B6）**：`BrowserFetchError` 命中 403/429/Just a moment 等 → 冷却 12s 重试 1 次；非 CF 错误转可读 `MediumError`，不盲目重试。

### Verified

- **X 视频口播（B5）**：OpenAI 官方 GPT-6 Astra 推文（163s 视频）→ 真实英文口播 2.7min，无幻觉；`recheck.lite=true`。样本：`https://x.com/OpenAI/status/2095595741528125780`。

### 测试

- **834 项离线测试**（新增 14 项）。

## [0.4.3] - 2026-10-17

**立项批 3**：小红书 token 化搜索、播客单集定位、同源长段修正、公众号指纹防漂移。

### Fixed

- **同源校验误杀长字幕段（播客）**：长 cue（30s+）整段与 12s ASR 对比 → 相似度必然偏低；改为按时间比例切片。实测 Practical AI 平均相似度 0.371→0.735，正确采信转写稿。
- **Apple 单集链接定位错误**：`?i=` 旧单集 lookup 常为空，之前回落到 feed（会转写最新一集）；现用「iTunes trackName → URL slug」匹配 feed 标题，正确命中目标单集（实测 P485）。

### Added

- **小红书搜索 token 化（B2）**：搜索结果直接从页面 state 提取 `xsecToken`，生成可抓取的完整链接；实测 search → run 成功（无需 App 分享）。
- **公众号搜索结果指纹（B9）**：`search` 落指纹（标题+账号，顺序敏感，1h TTL）；`run --pick` 检测到列表漂移时要求 `--yes` 复核，避免取错条目。
- **复检窗口级断点（B8）**：每窗口完成即写 `.recheck_ckpt/<platform>_<item>.json`（区间 + 转写哈希 + vision 结果）；`--resume` 复用已完成窗口且不再调用 vision；完整跑完后自动清理。
- **预算透明**：`recheck_report.budget` 新增 `actual`（按去重后帧重算）、`frames_planned`/`frames_used`；实测 7min 视频计划 1314 帧、实发 153 帧，上界 0.254 USD / 实际约 0.045 USD。

### 测试

- **820 项离线测试**（新增 7 项）。

## [0.4.2] - 2026-10-17

**立项批 2**：11 平台搜索 + 校正置信度 + 浏览器串行 + 长任务可预期 + Medium 截断提示。

### Added

- **11 平台搜索**：`el.py search --platform <p>` 覆盖 bilibili/wechat/youtube/podcast/知乎/微博/X/小红书/抖音/Medium/TED；`--json` 机读；`platforms` 输出 `search=native|api|browser|需链接`。
  - API：YouTube（yt-dlp ytsearch）、播客（iTunes Search）。
  - Browser：知乎/微博/X/小红书/抖音/Medium/TED（复用登录 profile，文件锁串行）。
- **校正置信度分级（B11）**：`校正(置信度:高|中|低)`；低置信只留痕（`windows.kind=low_confidence`）不改正文；`recheck_report.uncertain` 同时收录低置信项。
- **浏览器 profile 文件锁（B7）**：跨进程互斥 + 占用等待/可读提示 + 失效锁回收。
- **复检计划/ETA（B3）**：开始前打印窗口数/预估金额/帧帽；逐窗打印已用与预计剩余；`--max-seconds` 到点剩余窗口标未覆盖。
- **Medium 会员文提示（B6）**：`Member-only story`/截断结尾 → `status=suspect` + `quality_hint`；`ContentResult.quality_hint` 通用字段。

### 测试

- **813 项离线测试**（新增 18 项）。

## [0.4.1] - 2026-10-17

**11 平台实测缺陷修复批 1**：阻断级转写/复检/路径问题 + 一批可信度问题。详见
`docs/plans/2026-10-17-fix-batch-1.md` / `docs/plans/2026-10-17-backlog.md`。

### Fixed

- **B 站/抖音英文内容被强制中文转写（P0）**：移除硬编码 `language_hint="zh"`，改为自动检测；英文视频不再输出繁体伪中文。
- **画面复检全部丢失（P0）**：① 解析器不接受 `[41.0s]`/`[41.0秒]` 时间戳；② 提示词未要求专名/数字硬性校正；③ 推理型 vision 模型把 `max_tokens` 吃满返回空正文——三处联合导致 `windows=[]`。现支持秒后缀、专名/数字规则、空正文自动加倍重试（默认 8192，最多 3 次）。
- **播客 Art19 GUID 崩溃（P0）**：`gid://...` 作缓存文件名在 Windows 非法 → 缓存键文件名净化 + 短哈希防撞。
- **长任务无进度（P0）**：复检逐窗打印 `复检窗口 i/N`，产物新增 `checked_windows`；yt-dlp 增加 `socket_timeout` 与提取重试上限。
- **doctor cookie 假阳性**：逐平台展示（B 站真实探针；其余按文件龄期标「未验证/过期风险」）。
- **名称搜索静默取第一条**：现在必须 `--pick N`；`--yes` 可显式取第一条。
- **未知 URL 被当关键词**：`resolve`/`run` 改为明确 `unsupported` + 自进化三步提示。
- **公众号模板噪声**：按行过滤「预览时标签不可点 / 微信扫一扫 / 阅读原文 / ×」等 UI 文案。
- **文档正文过短无提示**：<80 字时写 `quality_hint`（图片帖/会员墙/截断）。
- **X 视频幻觉口播**：`no_speech_ratio`/重复句/模板句检测，命中丢弃并标注。
- **小红书链接**：支持直接粘贴 App 分享文本/短链，错误提示补三步获取方法。
- **浏览器 profile 占用**：可读提示「请串行执行」，不再直出 Playwright 栈。
- **Windows 控制台乱码**：入口强制 UTF-8 输出（无需 `-X utf8`）。
- Whisper 自动检测时 `content_info.language` 回填实际语言。

### 测试

- **795 项离线测试**（新增 26 项）。

## [0.4.0] - 2026-10-17

**术语库反哺进化**：每次转写筛验 + 自动沉淀 + 初启填塞 + bili-wiki 适配。

### Added

- **每次转写术语筛验**：timeline 定稿后按 `_aliases` 勘误替换（`content_info.glossary.corrected` 留痕）+ 存疑清单（`uncertain` 不替换）；缓存 payload 同步（命中恢复）。
- **自动反哺**：画面校正对（`corrected_from`/`corrected_to`）命中术语库 → 自动登记别名；保留 R11 报告沉淀与 `glossary.py absorb` 知识库吸收。
- **初启第⑤点**：引导询问是否填塞自有术语 + 告知自动反哺机制 + 推荐配套 `bilibili-to-wiki`（已适配本技能：任意支持平台内容入库）。
- **glossary.py**：新增 `clean_lines` / `absorb_corrections`。

### Changed

- `_run_window_recheck` 校正行补记 `corrected_to`（供术语库自动反哺）。

### Changed

- **README 中英双语** + 产品级标语「一切知识皆力量 / Knowledge is Power」。

### 测试

- **769 项离线测试**（新增 5 项）。

## [0.3.1] - 2026-10-17

### Added

- **`scripts/update.py` 更新助手**（等价入口 `el.py update [--dry-run]`）：一键 `git pull --ff-only`，自动处理引导标签位（丢弃本地改动 → pull → 按原状态恢复），其他本地改动 stash/pop；pull 失败自动恢复暂存，不丢失用户改动。

### Fixed

- `update._git` 的全局 strip 破坏 porcelain 首行前导空格（首个文件解析少一字符）→ 改 rstrip + 回归测试。

## [0.3.0] - 2026-10-17

**调用逻辑链重构 + 低配可用性**：SKILL.md 双标签位引导门；默认档 fast(small)；低配降级与强制推荐。

### Breaking（默认行为变化）

- **Whisper 出厂默认 `medium → small`（fast 档）**：首次引导强烈建议升级 medium（实机验证最佳）；已有用户 config 不受影响。

### Added

- **SKILL.md 调用逻辑链重构**：介绍 → 标签位①（引导状态）→ 正常调用工作流（八步 + 报告规则 R1-R13）→ 标签位②（阅读门）→ 初次调用逻辑（环境自检 + 四项确认 + 置位）。
- **首次引导门**：`el.py onboard [--complete|--reset|--json]`；`config.onboarded` + SKILL.md 双标签位同步；`run`/`scan` 软提示（不硬卡）；引导完成后不再触发。
- **低配质量检测**：whisper 置信度采集（avg_logprob / language_probability / 转写密度）→ 弱质量 + 低档 → 强制推荐（stderr + `content_info.quality_hint` + 报告「复检说明」）；低档常驻 `quality.tier_note`。
- **低配降级**：无 faster-whisper 时字幕平台降级采信 + 标注 + 强制推荐（不再整体失败）；画面复检依赖（Pillow/PyAV）缺失时提前跳过。

### 测试

- **754 项离线测试**（引导/低配/质量检测新增 34 项）；低配 venv（仅 requests）真实实测通过。

## [0.2.2] - 2026-10-17

**媒体下载健壮性（YouTube 媒体链路修复）**：多候选 + 瞬态重试 + 断点续传 + 时长预算。

### Fixed

- **YouTube 媒体不可达恢复**：googlevideo 瞬态断连原会把「一次抖动」放大为复检跳过 → 下载链路改为多候选（跨主机）+ 断点续传（Range，64KB 分块）+ 进展感知重试 + DNS 快速失败 + 180s 时长预算（含流内检查）；流水线 4 处媒体下载点（同源校验 / Whisper / 窗口复检 / 归属兜底）统一接入。
- 测试卫生：CLI 测试套件因 fake URL 经代理 502 触发重试退避而变慢（418s → 22s）；recheck 契约更新为候选列表。

### Added

- 适配器可选钩子 `fetch_media_urls`（YouTube 已实现，跨主机多样化）；`download_audio` 支持候选列表 / `attempts` / `backoff` / `max_seconds`。

### 测试

- **720 项离线测试**（媒体下载健壮性新增 13 项）。

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
