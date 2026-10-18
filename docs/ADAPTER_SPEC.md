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
| `sniff`（可选） | `sniff(url) -> bool` | 网络探测（如取 content-type/首段字节判断 XML）；**仅当所有适配器 match 均未命中且输入为 http(s) 时调用**；异常会被静默跳过 |
| `resolve` | `resolve(q) -> ItemRef` | 链接/名称 → 稳定标识；无法直接定位时抛 `core.adapter.SearchNeeded`；优先本地解析（正则/ID 提取），需要网络时给出可读异常 |
| `list_items` | `list_items(ref) -> list[ItemRef]` | 多P/播放列表展开；单项返回 `[ref]`；每个 ItemRef 带 `sub_id / title / duration` |
| `fetch_meta` | `fetch_meta(ref) -> ItemMeta` | `title` 用**内容级标题**（用于目录命名），分集名放 `page_part`；`stats` 可空 |
| `fetch_content` | `fetch_content(ref) -> ContentResult` | 内容归一化，见 §2 |
| `fetch_media_url` | `fetch_media_url(ref, media="audio", lowest=False) -> str \| None` | `audio` 供 Whisper；`video` 供抽帧复检（`lowest=True` 取最低码率省带宽）；无媒体返回 `None` |

字段类型见 `core/models.py`（ItemRef / ItemMeta / ContentResult）。

**可选钩子（按需实现）**：

| 钩子 | 签名 | 用途 |
|---|---|---|
| `sniff` | `sniff(url) -> bool` | 见上：match 全未命中时的兜底路由（网络探测，仅 http(s)） |
| `media_headers` | `media_headers() -> dict \| None` | 媒体下载（Whisper/抽帧）请求头；平台 CDN 校验 UA/Referer 时必须（如抖音） |
| `fetch_root_meta` | `fetch_root_meta(ref, refs) -> ItemMeta` | 合集根 meta（收藏夹/列表/频道等账号级列表用列表自身 meta；默认取 `refs[0]`） |
| `COOKIE_EXPORT` | dict | 需 cookie 的平台声明导出规格（见 §4）；`el.py cookie <platform>` 自动发现 |

**多模态复检通道（统一，必接）**：

- 复检逻辑**全部在 core，不在适配器内实现**：`core.pipeline` 在 `suspect` 时做 3 帧归属复检、配置 vision 时做内容完整性复检；`core.vision` 负责抽帧与多模态调用。
- 适配器只需实现统一的 `fetch_media_url(ref, media="video", lowest=True)`：
  - 有画面的平台（B 站 / YouTube / TED / 抖音 / X 视频帖 / 小红书视频笔记 / 微博视频帖…）返回**可抽帧的视频流直链**（`lowest=True` 取最低码率省带宽）；CDN 校验 UA/Referer 时同时实现 `media_headers()`；
  - 无画面平台（播客 / 纯图文的知乎、公众号、Medium 等）返回 `None`——core 会自动跳过复检，**不要**为此写任何特判分支。
- 契约测试（`tests/contract/harness.py`）会校验该方法具备 `media/lowest` 关键字；所有适配器（含自进化生成的）必须通过。

## 2. 内容归一化

| 形态 | ContentResult 要求 | 报告 |
|---|---|---|
| `timeline` | `segments=[{"start": float秒, "end": float秒, "text": str}]` | 时间轴分点报告（当前已实现） |
| `document` | `blocks=[{"heading": str, "text": str}]` | 章节式报告（已实现） |

`status` 语义（由适配器按自身质量校验给出，流水线接管后续降级）：

- `ok`：可信；
- `suspect`：软可疑（如覆盖不足/关键词零命中）——配置 vision 时会先做 3 帧归属复检，通过则采信；
- `invalid`：硬不可信（如时长越界、明确错乱）——直接走 Whisper；
- `empty`：无内容。

## 3. 错误处理

- 可重试错误（限流/网络）：用 `core.retry.retry_with_backoff`；B 站参考实现另有更细的 412/风控处理，可直接借鉴 `providers/bilibili.py` 的 `ApiClient._get`。
- 不可达/解析失败：返回 `empty` 语义或抛**可读异常**（含原因），不要抛裸异常穿透流水线。
- **禁止在适配器内写产物**：一切落盘由 `core.pipeline` + `core.artifacts` 统一完成。

### 3.1 反爬与缓速（强制）

- **所有 HTTP 请求必须经过缓速**：默认用 `core.throttle.ThrottledSession("<platform>")` 替代 `requests.Session()`；直连 `requests.get` 的调用点显式 `throttle.wait("<platform>")`。
- **被风控拒绝时不得无脑重试**（403/412/429/平台风控码）：给出可读提示，退避重试最多 1-2 次；必要时提示用户稍后再试（参考知乎 40362 处理）。
- **新平台尽调必须评估风控强度**，并在 `config.rate_limits.<platform>` 给出保守默认（越易识别越慢）；高风险的平台优先「浏览器辅助获取 + 少发请求」。
- 浏览器辅助路径（cookie/挑战）优先复用 `COOKIE_EXPORT` 标配（§4），不要自行实现第二套会话。
- 缓存、批量间隔（`--interval`）、单会话复用是反爬第一层，不要绕过。

## 4. 凭据与配置

- 凭据只能来自 `config.auth.<platform>`（dict）或环境变量；**禁止硬编码**。
- cookie/密钥文件路径写入 `.gitignore`（如 `scripts/.bili_cookie`、`scripts/.zhihu_cookie`）。
- 可选第三方依赖（如 yt-dlp）必须**懒加载**，未安装时给出明确安装提示。
- **需要 cookie 的平台必须声明 `COOKIE_EXPORT`**（模块级常量，供 `el.py cookie <platform>` / `scripts/cookie_export.py` 读取）：规格就近声明在 provider 模块，导出工具零平台硬编码，新平台只需加一个文件的声明。

  | 字段 | 必填 | 含义 |
  |---|---|---|
  | `url` | ✅ | 登录入口 URL |
  | `read_url` | ✅ | cookie 读取域（通常为站点主域） |
  | `probe_url` | 可选 | 触发风控脚本生成挑战 cookie 的内容页（如知乎 `__zse_ck`） |
  | `required` | ✅ | 登录态判定 cookie 名（如 `z_c0` / `SESSDATA`） |
  | `ready` | ✅ | 可导出判定 cookie 名（含挑战 cookie） |
  | `wanted` | ✅ | 写入文件的 cookie 名与顺序 |
  | `file` | ✅ | 输出文件（相对 `scripts/`，必须 gitignored） |
  | `verify` | 可选 | 登录校验：`{url, expect}`——页面内 fetch `url`（带凭据），响应包含 `expect` 才判定登录。用于「游客也有关键 cookie」的平台（如微博：游客也有 `SUB`，必须验 `/api/config` 的 `"login":true`） |

  默认导出流程：打开 `url` → 等待 `required`（登录）→ 打开 `probe_url`（或 `read_url`）→ 等待 `ready`（挑战 cookie）→ **`verify` 登录校验（如有声明，未通过不落盘）** → 写入 `file`；窗口被关闭等异常均给出可读提示。各站差异优先用 `probe_url`/`ready`/`verify` 精调。
  参考实现：`scripts/providers/zhihu.py`（带 `probe_url`）与 `scripts/providers/bilibili.py`（无挑战 cookie）。

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
- [ ] 真实链接冒烟：产物落盘（`content_info.json` / `content.txt` / `report_template.md`，以及基线 `<标题>学习报告.md`）
- [ ] 未配置 out_dir 时正常报错；配置后按 `<作者>/<标题>` 正确归档（无日期前缀；重名加空格序号）
- [ ] 凭据不硬编码；可选依赖缺失时有明确提示
