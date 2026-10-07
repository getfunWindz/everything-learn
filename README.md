# everything-learn

> **bilibili-learn 的全量进化版**：多平台学习内容总结技能 —— 视频 / 音频 / 图文 / 社媒短内容聚合。

- 📄 设计文档（spec）：[docs/specs/2026-10-07-everything-learn-design.md](docs/specs/2026-10-07-everything-learn-design.md)
- 🧩 适配器规范（自进化）：[docs/ADAPTER_SPEC.md](docs/ADAPTER_SPEC.md) · [开发指南](docs/ADAPTER_TEMPLATE.md)
- 🚧 状态：**M0 开发完成** —— 核心架构迁移 + B 站适配器可用；YouTube（M1）→ 播客（M2）→ 知乎（M3）→ 更多按里程碑接入
- 🧬 上游项目：[bilibili-learn](https://github.com/getfunWindz/bilibili-learn)（轻量版，只做 B 站，保持原样）

## 当前能力（M0）

- **B 站**：链接 / BV号 / av号 / 名称搜索 / 收藏夹 → 字幕分级校验（ok/suspect/invalid）→ 多模态 3 帧归属复检 → Whisper 兜底（默认 medium、长视频默认禁 VAD）→ 中文学习报告骨架
- **架构**：`core/`（内容契约、注册表、配置、缓存、重试、流水线、产物）+ `providers/`（平台适配器）；B 站为参考实现
- **产物三件套**：`content_info.json` / `content.txt` / `report_template.md`
- **批量**：多 P / 合集逐项处理、失败不中断、`--resume` 断点续跑、请求限流与重试
- **自进化**：遇到没有适配器的平台 → 询问用户 → 可行性尽调 → 按规范现场搭建（见 ADAPTER_SPEC）

## 快速使用

```bash
# 首次使用：必须为报告选择一个根目录（建议新建「学习笔记」总文件夹）
python scripts/el.py run "<视频链接/BV号>" --out "D:/学习笔记"

# 批量多 P / 断点续跑
python scripts/el.py run "<BV号>" --all --out "D:/学习笔记"
python scripts/el.py run "<BV号>" --pages "1-10" --out "D:/学习笔记" --resume

# 环境自检 / 报告导出
python scripts/el.py doctor
python scripts/el.py export "<报告目录>" --format html
```

## 测试

```bash
python -m pytest tests/ -q    # 199 passed（全部 mock 网络，离线可跑）
```

## 平台规划

| 平台 | 形态 | 状态 |
|---|---|---|
| bilibili | timeline（视频） | ✅ 可用 |
| YouTube | timeline（视频） | 🚧 M1 |
| 播客 | timeline（音频） | 🚧 M2 |
| 知乎 | document（图文） | 🚧 M3 |
| TED / Medium / 公众号 / X / 微博 / 小红书… | — | 自进化流程现场接入 |
