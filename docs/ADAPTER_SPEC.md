# 适配器接入规范（ADAPTER_SPEC）

> everything-learn 的核心机制：任何平台的内容都通过一个**适配器**接入统一流水线。
> 本规范用于两件事：① 人工开发新适配器；② agent 在「自进化引导」中为用户现场生成适配器。
> **接入前必须先完成 §6 可行性尽调**：先确认可达，再动手写代码。

---

## 1. 接口契约

适配器类需实现以下全部方法（`core/adapter.py` 的 `SourceAdapter` 协议）：

| 方法 | 签名 | 要求 |
|---|---|---|
| `match` | `match(url) -> bool` | 纯字符串判断、**不联网**；输入 URL 属本平台返回 True |
| `resolve` | `resolve(q) -> ItemRef` | 链接/名称 → 稳定标识；无法直接定位时抛 `core.adapter.SearchNeeded`；优先本地解析（正则/ID 提取），需要网络时给出可读异常 |
| `list_items` | `list_items(ref) -> list[ItemRef]` | 多P/播放列表展开；单项返回 `[ref]`；每个 ItemRef 带 `sub_id / title / duration` |
| `fetch_meta` | `fetch_meta(ref) -> ItemMeta` | `title` 用**内容级标题**（用于目录命名），分集名放 `page_part`；`stats` 可空 |
| `fetch_content` | `fetch_content(ref) -> ContentResult` | 内容归一化，见 §2 |
| `fetch_media_url` | `fetch_media_url(ref, media="audio", lowest=False) -> str \| None` | `audio` 供 Whisper；`video` 供抽帧复检（`lowest=True` 取最低码率省带宽）；无媒体返回 `None` |

字段类型见 `core/models.py`（ItemRef / ItemMeta / ContentResult）。

## 2. 内容归一化

| 形态 | ContentResult 要求 | 报告 |
|---|---|---|
| `timeline` | `segments=[{"start": float秒, "end": float秒, "text": str}]` | 时间轴分点报告（当前已实现） |
| `document` | `blocks=[{"heading": str, "text": str}]` | 章节式报告（M3 起实现；在此之前流水线会返回明确失败） |

`status` 语义（由适配器按自身质量校验给出，流水线接管后续降级）：

- `ok`：可信；
- `suspect`：软可疑（如覆盖不足/关键词零命中）——配置 vision 时会先做 3 帧归属复检，通过则采信；
- `invalid`：硬不可信（如时长越界、明确错乱）——直接走 Whisper；
- `empty`：无内容。

## 3. 错误处理

- 可重试错误（限流/网络）：用 `core.retry.retry_with_backoff`；B 站参考实现另有更细的 412/风控处理，可直接借鉴 `providers/bilibili.py` 的 `ApiClient._get`。
- 不可达/解析失败：返回 `empty` 语义或抛**可读异常**（含原因），不要抛裸异常穿透流水线。
- **禁止在适配器内写产物**：一切落盘由 `core.pipeline` + `core.artifacts` 统一完成。

## 4. 凭据与配置

- 凭据只能来自 `config.auth.<platform>`（dict）或环境变量；**禁止硬编码**。
- cookie/密钥文件路径写入 `.gitignore`（参考 B 站的 `scripts/.bili_cookie` 约定）。
- 可选第三方依赖（如 yt-dlp）必须**懒加载**，未安装时给出明确安装提示。

## 5. 注册与发现

- 在 `scripts/providers/<platform>.py` 定义适配器类，用 `@register` 装饰（或由 `core.registry.load_providers()` 扫描注册）。
- 文件名**不要以 `_` 开头**（模板 `_template.py` 不会被注册）。
- 不需要改动 `core/` 任何文件。

## 6. 可行性尽调清单（写代码之前必须逐项回答）

| # | 尽调项 | 要回答的问题 |
|---|---|---|
| 1 | 内容形态 | 视频 / 音频 / 图文 / 帖子？入口 URL 模式是什么？ |
| 2 | 获取方式 | 官方 API（需 key？）/ 公开接口 / HTML 解析 / RSS？ |
| 3 | 鉴权需求 | 无 / cookie / OAuth / 付费 API？ |
| 4 | 风控强度 | 无 / 限流 / 签名参数 / 验证码 / 设备指纹？维护成本评估。 |
| 5 | 内容可得性 | 原生字幕？自动字幕？音视频可下载？正文可提取？需浏览器渲染？ |
| 6 | **结论** | **可自动化**（进入搭建）/ **半自动**（仅手动导出后处理）/ **不可行**（原因 + 替代方案） |

尽调结论需向用户展示并获得确认，才可进入搭建。

## 7. 自进化生成流程（agent 执行）

```
① 用户输入未被任何适配器 match
   → 主动询问：「这个平台还没有适配器，是否按规范现场搭建一个？」
② 用户确认 → 完成 §6 尽调表 → 给出结论 → 用户确认继续
③ cp scripts/providers/_template.py scripts/providers/<name>.py，按 §1 实现
④ 测试三件套（必须齐全）：
   a. fixture 离线解析测试：tests/fixtures/<platform>/ + tests/providers/test_<name>.py
   b. 契约测试：from tests.contract.harness import run_contract
                   assert run_contract(MyAdapter()) == []
   c. 真实链接冒烟：手动跑一次真链接，记录链接与结果
⑤ 全量回归（pytest tests/ -q）→ 独立提交（一次提交只做「新增一个适配器」）
⑥ 冒烟不通过不允许合入；尽调判定不可达则停止并说明原因
```

## 8. 适配器验收标准

- [ ] 契约测试 0 问题（`run_contract` 返回空列表）
- [ ] fixture 离线解析测试通过（不联网）
- [ ] 真实链接冒烟：产物三件套落盘（`content_info.json` / `content.txt` / `report_template.md`）
- [ ] 未配置 out_dir 时正常报错；配置后按 `<作者>/<日期>_<标题>` 正确归档
- [ ] 凭据不硬编码；可选依赖缺失时有明确提示
