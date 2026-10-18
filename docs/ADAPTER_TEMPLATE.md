# 适配器开发指南（逐步）

> 前置：先完成 `ADAPTER_SPEC.md` §6 的可行性尽调，并把结论给用户确认。
> 本文是操作步骤；规范条款以 `ADAPTER_SPEC.md` 为准。

## 步骤

1. **复制骨架**
   ```bash
   cp scripts/providers/_template.py scripts/providers/<platform>.py   # Windows PowerShell 用 copy
   ```
2. **实现六个方法**（建议顺序，每步配测试）：
   | 顺序 | 方法 | 先写什么测试 |
   |---|---|---|
   | 1 | `match` | 正/负例 URL 字符串 |
   | 2 | `resolve` | 链接 → ItemRef；名称 → `SearchNeeded` |
   | 3 | `list_items` | 多集/单项展开 |
   | 4 | `fetch_meta` | 标题/作者/时长映射 |
   | 5 | `fetch_content` | fixture 解析 → ContentResult（status 判定） |
   | 6 | `fetch_media_url` | audio/video/lowest 三个分支 |
3. **fixture 离线测试**（不联网）：
   ```python
   # tests/providers/test_<platform>.py
   import sys, os
   sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
   from providers.<platform> import MyAdapter

   def test_parse_fixture():
       html = open("tests/fixtures/<platform>/sample.html", encoding="utf-8").read()
       content = MyAdapter()._parse(html)          # 解析函数尽量纯函数化，便于 fixture 测试
       assert content.status in ("ok", "suspect", "invalid", "empty")
   ```
4. **契约测试**（必须）：
   ```python
   from tests.contract.harness import run_contract
   from providers.<platform> import MyAdapter

   def test_contract():
       assert run_contract(MyAdapter()) == []
   ```
5. **真实链接冒烟**（手动，记录到提交信息/PR）：
   ```bash
   python scripts/el.py run "<真实链接>" --out "<临时目录>" --no-cache
   # 期望：content_info.json / content.txt / report_template.md + 基线 <标题>学习报告.md
   ```
6. **全量回归 + 提交**
   ```bash
   python -m pytest tests/ -q          # 必须全绿
   git add scripts/providers/<platform>.py tests/
   git commit -m "feat(providers): <platform> 适配器"
   ```
7. **（需要 cookie 的平台）声明 `COOKIE_EXPORT`**（`el.py cookie <platform>` 读取；字段表见 `ADAPTER_SPEC.md` §4）
   ```python
   COOKIE_EXPORT = {
       "url": "https://example.com/login",        # 登录入口
       "read_url": "https://example.com",         # cookie 读取域
       # "probe_url": "https://example.com/article/1",  # 可选：触发风控挑战 cookie 的页面
       "required": ["session_id"],                # 登录态判定
       "ready": ["session_id"],                   # 可导出判定
       "wanted": ["session_id", "device_id"],     # 写入文件的键与顺序
       "file": ".example_cookie",                 # scripts/ 下，记得加入 .gitignore
   }
   ```
   冒烟：`python -X utf8 scripts/el.py cookie example --timeout 60`；自查：`python -X utf8 scripts/el.py cookie --help`。

## 常见坑

- **限时直链**：音视频直链常带签名与过期时间，取到后要尽快下载；下载失败优先重试获取直链，而不是重试下载同一个 URL。
- **时长单位**：统一用秒（float）；有的平台返回毫秒/字符串（"12:34"），先归一化。
- **分页**：`list_items` 要拉全量再交给 `--pages/--all` 过滤；不要在适配器里做页码选择（那是流水线的职责）。
- **编码**：页面解析统一 `response.encoding` 显式设置（GBK/UTF-8 混用常见）。
- **风控**：带签名参数的平台把签名函数独立出来并写单测；定时/复杂逻辑不要散落在请求代码里。
- **风控挑战 cookie**：若站点有一个由前端脚本生成的挑战 cookie（如知乎 `__zse_ck`），`probe_url` 指向一个内容页即可让真实浏览器自动生成；`ready` 里必须带上它。
- **可选依赖**：`import` 放在函数内部，缺失时抛带安装命令的可读异常。


## 可选钩子与字段（2026-10-18 补充）

| 名称 | 作用 |
|---|---|
| `reload_auth()` | 登录态自动恢复后重新注入 cookie（pipeline 认证错误自动重试会调用） |
| `fetch_media_urls(ref, media, lowest)` | 多候选媒体直链（跨主机容灾），默认回退 `fetch_media_url` |
| `media_headers()` | 媒体 CDN 需要的 UA/Referer |
| `language_hint` / `has_video` | 转写语言提示 / 是否具备画面层（档位复检） |
| `accept_auto_subtitle` | 平台策略：自动字幕是否直接采信（YouTube 用） |
| `media_budget_sec` | 媒体下载预算（弱网平台收窄，避免空耗） |
| `ContentResult.quality_hint` | 适配器级质量提示（会员墙/截断/图片为主），报告优先展示 |

## 契约测试生成器

```bash
python scripts/gen_contract_test.py <adapter_name>          # 生成 tests/providers/test_<name>.py
python scripts/gen_contract_test.py <adapter_name> --force  # 覆盖
```

生成文件做 `run_contract`（name/kinds/方法/`fetch_media_url(media,lowest)` 签名）；
fixture、纯函数单测与真实冒烟仍需按本指南补全。

## 登录态自动化（cookie）

- 声明 `COOKIE_EXPORT` 后，`el.py cookie <p> --auto` 会先用 headless 持久 profile 读取已登录 cookie（无窗口），
  未登录才提示一次登录；pipeline 遇到认证错误会自动恢复并重试（需实现 `reload_auth`）。
- 无显示环境（CI/服务器）用文本导入：
  ```bash
  el.py cookie <p> --import cookies.txt     # Netscape / k=v;… / JSON
  el.py cookie <p> --cookie-string "k=v; k2=v2"
  ```
