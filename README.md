# everything-learn

> **bilibili-learn 的全量进化版**：多平台学习内容总结技能 —— 视频 / 音频 / 图文 / 社媒短内容聚合。

- 📄 设计文档（spec）：[docs/specs/2026-10-07-everything-learn-design.md](docs/specs/2026-10-07-everything-learn-design.md)
- 🧩 适配器规范（自进化）：[docs/ADAPTER_SPEC.md](docs/ADAPTER_SPEC.md) · [开发指南](docs/ADAPTER_TEMPLATE.md)
- 🚧 状态：**M0 + M1 + M2 + M3 + M4 完成** —— B 站、YouTube、播客、知乎、**TED**可用（含反爬缓速基座）；更多平台按路线接入
- 🧬 上游项目：[bilibili-learn](https://github.com/getfunWindz/bilibili-learn)（轻量版，只做 B 站，保持原样）

## 当前能力（M0）

- **B 站**：链接 / BV号 / av号 / 名称搜索 / 收藏夹 → 字幕分级校验（ok/suspect/invalid）→ 多模态 3 帧归属复检 → Whisper 兜底（默认 medium、长视频默认禁 VAD）→ 中文学习报告骨架
- **YouTube**：链接 / youtu.be / shorts / 播放列表 / 裸视频 ID → 人工字幕优先（含中文翻译轨）→ 自动字幕滚动去重清洗（标注「字幕(自动)」）→ 音轨 Whisper 兜底 → 同规格报告
- **播客**：RSS / Apple Podcasts 链接 → `podcast:transcript` 直接解析（字幕(转写稿)）→ 无转写稿则音频 Whisper；无画面（复检自动跳过）；任意 XML feed 可被嗅探路由
- **TED**：`ted.com/talks/<slug>` → 官网官方字幕（多语含简中，zh-cn 优先）→ 同规格报告；字幕全缺时 mp4 → Whisper 兜底；无需 cookie
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

# 环境自检 / 报告导出
python scripts/el.py doctor
python scripts/el.py export "<报告目录>" --format html
```

## 测试

```bash
python -m pytest tests/ -q    # 289 passed（全部 mock 网络，离线可跑）
```

## 平台规划

| 平台 | 形态 | 状态 |
|---|---|---|
| bilibili | timeline（视频） | ✅ 可用 |
| YouTube | timeline（视频） | ✅ 可用 |
| 播客（RSS/Apple） | timeline（音频） | ✅ 可用 |
| 知乎 | document（图文） | ✅ 可用（需 cookie；`scripts/cookie_export.py zhihu` 一键导出） |
| TED | timeline（视频） | ✅ 可用（官网官方字幕，多语含简中） |
| Medium | document（图文） | 🚧 M5 |
| 抖音 | timeline（视频） | 🚧 M7（尽调 ✅：访客 cookie + yt-dlp） |
| 微博 / 公众号 / X / 小红书 | — | 🚧 M6+（逐个尽调） |
| 名单外平台 | — | 自进化流程现场接入 |
