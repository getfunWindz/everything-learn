# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与语义化版本。

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
