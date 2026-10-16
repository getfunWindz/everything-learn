# everything-learn

<p align="center">
  <img src="./assets/readme/hero.svg" width="100%"
       alt="everything-learn：11 个平台的学习内容，一个命令变成中文学习报告（终端运行输出与平台列表）">
</p>

<p align="center">
  <a href="https://github.com/getfunWindz/everything-learn/actions/workflows/tests.yml"><img src="https://github.com/getfunWindz/everything-learn/actions/workflows/tests.yml/badge.svg" alt="tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="license: MIT"></a>
  <img src="https://img.shields.io/badge/tests-755%20offline-brightgreen.svg" alt="755 offline tests">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+">
</p>

**11 个平台，一个命令**：把视频、播客、图文与社媒内容整理成结构化的中文学习报告。
来源分级（AI 字幕 → Whisper 主选 / 人工·官方 → 同源校验）→ 档位评分 → 预算闸门 → 3 帧/秒窗口复检；账号级内容（收藏 / 关注 / 合集 / 主页）可枚举、筛选、批量出合集。

## 平台能力

| 平台 | 形态 | 能力 |
|---|---|---|
| B 站 | timeline | ✅ 链接/BV/av/名称搜索/收藏夹；多 P、UP 主视频列表（wbi）、稍后再看 / 历史 / 关注 |
| YouTube | timeline | ✅ 人工字幕优先（含中文翻译轨）、自动字幕滚动去重、429 退避与多语言轨回退 |
| 播客（RSS / Apple） | timeline | ✅ `podcast:transcript` 直接解析，无转写稿走音频 Whisper |
| TED | timeline | ✅ 官网官方字幕（多语含简中），无字幕时 mp4 → Whisper |
| 知乎 | document | ✅ 文章 / 回答 / 问题（`--all` 展开）；用户回答与收藏夹 items 扫描 |
| 微博 | document | ✅ 单帖 / 时间线 / **我的收藏** / **关注**（需 cookie） |
| 抖音 | timeline | ✅ 单视频（浏览器上下文 + Whisper）；主页 / 收藏 / 喜欢扫描（需登录） |
| 微信公众号 | document | ✅ 单篇直连（无需 cookie）；**合集（album）扫描**；搜狗搜索实验性 |
| X（Twitter） | document | ✅ 单推文（含视频口播转写）；主页时间线扫描（需登录 + 代理） |
| 小红书 | document | ✅ 单笔记（视频附口播转写）；主页（含分页）/ 收藏枚举（需登录） |
| Medium | document | ✅ 文章（SSR 抽取）/ feed / **书签列表**扫描（需登录） |
| 名单外平台 | — | 自进化流程现场接入：尽调 → 搭建 → 三件套（`el.py platforms` 查名单） |

**一次完整运行的产物**（每个条目三件套 + 图片资产）：

```text
学习笔记/<作者>/2026-10-14_标题/
├── content_info.json      # 元信息、来源、assets 清单
├── content.txt            # 逐行带时间轴的转写 / 正文
├── report_template.md     # 中文学习报告骨架
└── assets/                # 正文图片
```

## 怎么工作

<p align="center">
  <img src="./assets/readme/pipeline.svg" width="100%"
       alt="统一流水线：输入 → 路由（core 注册表）→ 取内容 → 来源分级/同源校验 → 档位评分 → 预算闸门 → 窗口复检 → 产物">
</p>

- **适配器只做路由与取数**：`match → resolve → list_items → fetch_meta → fetch_content → fetch_media_url`，并声明内容来源（`provenance`/`language_hint`）。
- **保真复检全部在 `core/`**：`core/recheck.py` 统一来源分级 → 同源校验 → 档位评分 → 区域选择 → 预算闸门；多模态复检通道是适配器标准必接项（`fetch_media_url(media="video", lowest=True)`），无画面平台返回 `None` 自动跳过；契约测试会校验签名。
- **产物透明**：档位/评分理由/预算估算/同源校验/交叉比对/不确定项落入 `recheck_report.json`；正文标注【画面补充】【画面校正】，未覆盖区间如实留痕。

## 快速开始

> 以下命令在本仓库根目录执行（即技能目录）。

```bash
# 安装（核心仅 requests + pytest；其余按需）
pip install -r requirements.txt
pip install yt-dlp        # 可选：YouTube / 抖音
pip install playwright    # 可选：cookie 导出 / browserfetch（无需 playwright install）

# Whisper：出厂默认 fast=small；实机验证 medium 效果最佳（首次引导会建议升级）

# 首次使用：完成引导（环境自检 + 四项确认：产出目录 / Whisper 档位 / 依赖清单 / 使用说明）
python scripts/el.py onboard             # 查看清单与环境摘要
python scripts/el.py onboard --complete  # 四项确认完成后置位（标签位 1→0，此后不再触发）

# 用一个根目录存放报告（未配置 out_dir 时 run 会拒绝运行，不设默认路径）
python scripts/el.py run "<视频链接/BV号>" --out "D:/学习笔记"

# 批量与断点续跑
python scripts/el.py run "<BV号>" --all --out "D:/学习笔记"
python scripts/el.py run "<BV号>" --pages "1-10" --out "D:/学习笔记" --resume

# 复检档位与预算（超阈值会展示金额预算卡）
python scripts/el.py run "<链接>" --recheck 2 --out "D:/学习笔记"    # 强制全量（1 定向 / 3 不检）
python scripts/el.py run "<链接>" --budget 1.5 --out "D:/学习笔记"  # 本次运行复检金额总上限
python scripts/el.py run "<链接>" --no-recheck --out "D:/学习笔记"  # 显式豁免复检（产物标注）
```

需要 cookie 的平台一键导出（Playwright + 系统浏览器，首次在窗口内登录一次）：

```bash
python scripts/el.py cookie zhihu        # 或 bilibili / weibo / douyin
python scripts/el.py cookie --list       # 查看已声明导出规格的平台
```

## 账号级扫描（scan → 合集）

`scan` 枚举列表（过滤 / 优先级 / 快照），`run --all` 批量出合集报告：

```bash
# 他人列表
python scripts/el.py scan "https://space.bilibili.com/<mid>" --limit 20     # B站 UP主视频
python scripts/el.py scan "https://www.zhihu.com/collection/xxxx" --priority
python scripts/el.py scan "https://www.youtube.com/@NASA" --limit 5

# 自己的数据（需登录 / cookie）
python scripts/el.py scan "https://weibo.com/u/favorites" --limit 20        # 微博收藏
python scripts/el.py scan "https://weibo.com/u/page/follow/<uid>"           # 微博关注
python scripts/el.py scan "https://medium.com/@<handle>/list/reading-list"  # Medium 书签
python scripts/el.py scan "https://www.douyin.com/user/self" --limit 20     # 抖音收藏
python scripts/el.py scan "https://www.xiaohongshu.com/user/profile/xxxx?tab=collect"
python scripts/el.py scan "https://mp.weixin.qq.com/mp/appmsgalbum?__biz=..&action=getalbum&album_id=.."
python scripts/el.py toview / history --limit 10 / following                # B站账号数据

python scripts/el.py run "<上面的链接>" --all --out "D:/学习笔记"           # 批量出合集
```

其他常用命令：

```bash
python scripts/el.py search --platform wechat "关键词"     # 公众号搜索（实验性，结果会话绑定）
python scripts/el.py platforms                            # 已注册平台名单 + 自进化入口
python scripts/el.py doctor                               # 环境自检（cookie / GPU / whisper / vision）
python scripts/el.py export "<报告目录>" --format html    # 或 docx
```

## 平台细节

- **复检与成本**：AI 字幕不再直接入库（转 Whisper，字幕留作交叉比对）；人工/官方字幕先过同源校验；复检档位 1/2/3（档3 需白名单、默认关闭），档1 定向、档2 全量（3 帧/秒 + 静帧去重）；金额超阈值先出预算卡。
- **B 站**：AI 字幕（`lan=ai-*`）→ Whisper 主选（AI 字幕留作双 ASR 交叉比对）；人工字幕先过同源校验；评分定档后执行窗口复检（默认 fast=small，建议升 medium——实机最佳；长视频默认禁 VAD）。
- **YouTube**：自动字幕为滚动式，已做重复清洗并标注「字幕(自动)」；自动翻译轨被限流时自动回退其它语言轨。
- **播客 / TED**：播客纯音频——档位复检记 n/a，仅同理源校验；TED 官方字幕优先（同源校验 + 档位复检照常）。
- **知乎 / 公众号 / Medium**：SSR 正文 → 章节式报告；公众号图片走 `data-src` 懒加载链，Medium 走 SSR 块抽取。
- **微博 / X / 小红书 / 抖音**：社交内容映射为 document；X / 小红书视频帖附「视频口播内容」+ lite 档位复检（`recheck_report.json`，窗口级复检为后续项）；均按平台缓速（6–12s）并给出可读的风控提示。
- **反爬纪律**：平台级缓速 + 退避重试 + 持久化浏览器 profile（Cloudflare / 签名平台统一走 `browserfetch`，不逆向签名）。
- **低配可用（0.3.0）**：仅 `requests` 环境——图文平台可用；字幕平台自动降级（标注「低配：未经同源校验」+ `quality_hint` 推荐补依赖/升 medium）；whisper 平台给安装引导；画面复检依赖缺失时自动跳过。转写档位低于 medium 时产物带 `quality.tier_note` 常驻建议。

## 更新

- 本仓库**不会自动推送到已部署副本**：`git clone` 部署需手动 `git pull`；直接复制文件部署需重新复制（无自动更新机制）。
- `git pull` 若因 SKILL.md 引导标签位的本地修改被阻断：`git stash` → `git pull` → `python scripts/el.py onboard --complete`（恢复本地已引导状态；配置与产物不受影响）。
- 重新引导：`python scripts/el.py onboard --reset`（标签位回 1，下次使用重新展示四项确认）。

## 测试与工程

```bash
python -m pytest tests/ -q     # 755 passed（全部 mock 网络，离线可跑，约 2 分钟）
```

- **CI**：GitHub Actions（Windows × Python 3.10 / 3.11）。
- **架构**：`core/`（契约、注册表、配置、缓存、重试、缓速、流水线、保真复检 `recheck.py`、产物、HTML→块、图片下载）+ `providers/`（每平台一个适配器；B 站为参考实现）。
- **自进化**：名单外平台按 `docs/ADAPTER_SPEC.md` §6 尽调 → `docs/ADAPTER_TEMPLATE.md` + `scripts/providers/_template.py` 搭建 → fixture / 契约 / 冒烟三件套；未通过不合入。
- **上游**：[bilibili-learn](https://github.com/getfunWindz/bilibili-learn)（只做 B 站的轻量版，保持原样）。

## License

[MIT](LICENSE) · 变更记录见 [CHANGELOG.md](CHANGELOG.md)
