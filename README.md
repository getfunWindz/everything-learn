# everything-learn

> **bilibili-learn 的全量进化版**：多平台学习内容总结技能 —— 视频 / 音频 / 图文 / 社媒短内容聚合。

- 📄 设计文档（spec）：[docs/specs/2026-10-07-everything-learn-design.md](docs/specs/2026-10-07-everything-learn-design.md)
- 🚧 状态：**设计评审中**（M0 尚未开始）
- 🧬 上游项目：[bilibili-learn](https://github.com/getfunWindz/bilibili-learn)（轻量版，保持原样）

## 规划中的能力

- **平台适配器**：B站（参考实现）→ YouTube（M1）→ 播客（M2）→ 知乎（M3）→ TED / Medium / 公众号 / X / 微博 / 小红书…（M4+）
- **自进化**：遇到没有适配器的平台 → 询问用户 → 可行性尽调 → 按规范现场搭建适配器
- **内容获取**：字幕优先 + Whisper 兜底 + 多模态归属复检（复用 bilibili-learn v3.4 成果）
- **中文详解学习报告**：时间轴版（音视频）/ 章节版（图文）/ 聚合版（社媒）

## 首次使用（硬性）

首次配置或使用前，必须为报告选择一个存放根路径（如新建「学习笔记」总文件夹）；未配置 `out_dir` 时 CLI 会拒绝运行。
