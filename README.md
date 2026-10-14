# everything-learn

[![tests](https://github.com/getfunWindz/everything-learn/actions/workflows/tests.yml/badge.svg)](https://github.com/getfunWindz/everything-learn/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> **bilibili-learn 的全量进化版**：多平台学习内容总结技能 —— 视频 / 音频 / 图文 / 社媒短内容聚合。

## 安装

```bash
pip install -r requirements.txt   # 核心：requests + pytest（faster-whisper 行按需保留）
pip install yt-dlp                # 可选：YouTube / 抖音 取流与字幕
pip install playwright            # 可选：cookie 一键导出 + browserfetch（无需 playwright install）
```

- 🚧 状态：**M0–M12 完成** —— B 站、YouTube、播客、知乎、TED、微博、抖音、公众号、X、小红书 + **账号级扫描**（微博收藏/关注、知乎、YouTube、抖音、小红书、X、Medium 列表、公众号合集、B站 UP主；浏览器辅助/需登录的已标注）可用（含反爬缓速基座与 cookie 导出标配）；更多平台按路线接入
- 🧬 上游项目：[bilibili-learn](https://github.com/getfunWindz/bilibili-learn)（轻量版，只做 B 站，保持原样）

## 当前能力（M0）

- **B 站**：链接 / BV号 / av号 / 名称搜索 / 收藏夹 → 字幕分级校验（ok/suspect/invalid）→ 多模态 3 帧归属复检 → Whisper 兜底（默认 medium、长视频默认禁 VAD）→ 中文学习报告骨架
- **YouTube**：链接 / youtu.be / shorts / 播放列表 / 裸视频 ID → 人工字幕优先（含中文翻译轨）→ 自动字幕滚动去重清洗（标注「字幕(自动)」）→ 音轨 Whisper 兜底 → 同规格报告
- **播客**：RSS / Apple Podcasts 链接 → `podcast:transcript` 直接解析（字幕(转写稿)）→ 无转写稿则音频 Whisper；无画面（复检自动跳过）；任意 XML feed 可被嗅探路由
- **TED**：`ted.com/talks/<slug>` → 官网官方字幕（多语含简中，zh-cn 优先）→ 同规格报告；字幕全缺时 mp4 → Whisper 兜底；无需 cookie
- **微博**：单条微博（正文/长文/图片/转发）→ 章节式报告；用户主页时间线批量 + **我的收藏枚举**（`scan "https://weibo.com/u/favorites"`，需 cookie，`el.py cookie weibo`）；432 风控给出可读提示
- **抖音**：视频链接 / 短链 → yt-dlp（访客 cookie，一键导出）→ 无平台字幕 → Whisper 转写 → 时间轴报告
- **微信公众号**：单篇文章（短链/长链）→ 无需 cookie 直连 → 章节式报告 + 图片入 `assets/`；搜狗搜索实验性（`el.py search --platform wechat`，结果会话绑定）
- **X（Twitter）**：单推文（x.com/twitter.com，含 /i/status 与 t.co 短链）→ 无需 cookie → 正文/图片；视频推文自动取 mp4 → Whisper → 「视频口播内容」小节（多语自动检测）；主页时间线：`scan "https://x.com/home"`（需登录 + `browser_proxy` 配置）
- **小红书**：单笔记（xhslink 短链 / explore / discovery 链接）→ 正文/图片入 `assets/`；视频笔记 → Whisper → 「视频口播内容」；依赖短时效 `xsec_token`（分享链自带或 explore 兜底现取，实验性）
- **账号级扫描（M11/M12）**：微博用户时间线全量（`since_id` 翻页）/**我的收藏**/**关注**（`weibo.com/u/page/follow/<uid>`）、知乎用户回答/收藏夹 items、YouTube 频道列表、抖音主页/收藏/喜欢、小红书主页（含滚动分页）/收藏（浏览器辅助，收藏/喜欢需登录）、X 主页时间线（需登录+代理）、Medium 书签/列表（`@handle/list/<slug>`，需登录）、公众号合集（album）、B站 UP主视频（`space.bilibili.com/<mid>`）→ `el.py scan <链接>` 枚举筛选 + `el.py run <链接> --all` 批量报告；B站账号数据：`el.py toview / history / following`（需 cookie）
- **知乎**：专栏文章 / 单个回答 / 问题（`--all` 展开首屏 5 条回答）→ SSR 正文 → 章节式报告骨架 + 图片入 `assets/`；需要浏览器 cookie（可用内置导出器）
- **架构**：`core/`（内容契约、注册表、配置、缓存、重试、流水线、产物、HTML→块转换、图片下载）+ `providers/`（平台适配器）；B 站为参考实现
- **产物三件套**：`content_info.json` / `content.txt` / `report_template.md`
- **批量**：多 P / 合集逐项处理、失败不中断、`--resume` 断点续跑、请求限流与重试
- **自进化**：遇到没有适配器的平台 → 询问用户 → 可行性尽调 → 按规范现场搭建（见 ADAPTER_SPEC）

## 快速使用

> 以下命令均在本技能目录（本仓库根目录）下执行；若从其他目录调用，请先 `cd` 到本目录再用相对路径。

```bash
# 首次使用：必须为报告选择一个根目录（建议新建「学习笔记」总文件夹）
python scripts/el.py run "<视频链接/BV号>" --out "D:/学习笔记"

# 需要 cookie 的平台（知乎/B站）：一键导出（Playwright + 系统 Edge/Chrome，首次需在窗口内登录一次）
pip install playwright                   # 可选依赖，无需 playwright install
python scripts/el.py cookie zhihu        # 或 bilibili；导出后重跑 el.py run 即可
python scripts/el.py cookie --list       # 查看已声明导出的平台（新平台见 ADAPTER_SPEC §4）

# 批量多 P / 断点续跑
python scripts/el.py run "<BV号>" --all --out "D:/学习笔记"
python scripts/el.py run "<BV号>" --pages "1-10" --out "D:/学习笔记" --resume

# 微信公众号（单篇链接无需 cookie；搜索为实验性）
python scripts/el.py run "https://mp.weixin.qq.com/s/xxxx" --out "D:/学习笔记"
python scripts/el.py search --platform wechat "关键词"
python scripts/el.py run --platform wechat "关键词" --pick 1 --out "D:/学习笔记"

# X（Twitter）单推文（无需 cookie；视频推文含口播转写）
python scripts/el.py run "https://x.com/NASA/status/2108563438901350773" --out "D:/学习笔记"

# 小红书单笔记（xhslink 分享链；链接需含 xsec_token 或让工具现取）
python scripts/el.py run "https://xhslink.cn/o/xxxx" --out "D:/学习笔记"

# X 主页时间线扫描（需登录 + 浏览器代理：config.json 的 browser_proxy 或 EL_BROWSER_PROXY）
python scripts/el.py scan "https://x.com/home" --limit 20

# 账号级扫描（枚举列表→过滤/优先级→批量抓取；支持微博/知乎/YouTube/抖音与小红书；收藏类需登录）
python scripts/el.py scan "https://www.zhihu.com/collection/xxxx" --priority
python scripts/el.py run "https://www.zhihu.com/collection/xxxx" --all --out "D:/学习笔记"
python scripts/el.py scan "https://weibo.com/u/favorites" --limit 20                 # 我的微博收藏（需 cookie）
python scripts/el.py run "https://weibo.com/u/favorites" --all --out "D:/学习笔记"    # 收藏批量出合集
python scripts/el.py scan "https://www.douyin.com/user/xxxx" --priority --limit 20      # 抖音主页（浏览器辅助）
python scripts/el.py scan "https://www.douyin.com/user/self?tab=favorite" --limit 20   # 我的抖音喜欢（需登录）
python scripts/el.py scan "https://www.douyin.com/user/self" --limit 20                 # 我的抖音收藏（需登录）
python scripts/el.py scan "https://www.xiaohongshu.com/user/profile/xxxx" --limit 20    # 小红书主页（需登录）
python scripts/el.py scan "https://www.xiaohongshu.com/user/profile/xxxx?tab=collect" --limit 20  # 我的小红书收藏（需登录）
python scripts/el.py scan "https://medium.com/@<handle>/list/reading-list"                 # Medium 书签/列表（需登录）
python scripts/el.py scan "https://weibo.com/u/page/follow/<uid>" --limit 20             # 我的微博关注（需 cookie）
python scripts/el.py scan "https://mp.weixin.qq.com/mp/appmsgalbum?__biz=..&action=getalbum&album_id=.."  # 公众号合集
python scripts/el.py scan "https://space.bilibili.com/<mid>" --limit 20                  # B站 UP主视频（wbi）
python scripts/el.py toview / history --limit 10 / following            # B站账号数据（需 cookie）

# 环境自检 / 报告导出
python scripts/el.py platforms                  # 列出已注册平台（名单）+ 自进化入口
python scripts/el.py doctor
python scripts/el.py export "<报告目录>" --format html
```

## 测试

```bash
python -m pytest tests/ -q    # 403 passed（全部 mock 网络，离线可跑）
```

## 平台规划

| 平台 | 形态 | 状态 |
|---|---|---|
| bilibili | timeline（视频） | ✅ 可用 |
| YouTube | timeline（视频） | ✅ 可用 |
| 播客（RSS/Apple） | timeline（音频） | ✅ 可用 |
| 知乎 | document（图文） | ✅ 可用（需 cookie；`scripts/cookie_export.py zhihu` 一键导出） |
| TED | timeline（视频） | ✅ 可用（官网官方字幕，多语含简中） |
| Medium | document（图文） | ✅ 可用（浏览器 SSR 抽取正文 + feed 双通道；书签/列表 scan 需登录；CF 平台缓速） |
| 抖音 | timeline（视频） | ✅ 可用（单视频 + 主页/收藏/喜欢扫描；访客 cookie + yt-dlp/browserfetch；收藏/喜欢需登录） |
| 微博 | document（帖子） | ✅ 可用（单帖；时间线/我的收藏/**关注** scan 需 cookie） |
| 微信公众号 | document（图文） | ✅ 可用（单篇链接直连，无需 cookie；**合集（album）scan**；搜狗搜索实验性） |
| X（Twitter） | document（推文） | ✅ 可用（单推文 + 视频口播转写；主页时间线 scan（需登录+代理）；搜索不做） |
| 小红书 | document（笔记） | ✅ 可用（单笔记 + 视频口播转写；token 现取现用；主页（含分页）/收藏枚举（scan，需登录）） |
| 名单外平台 | — | 自进化流程现场接入 |
