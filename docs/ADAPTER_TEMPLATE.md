# 适配器开发指南（逐步）

> 前置：先完成 `ADAPTER_SPEC.md` §6 的可行性尽调，并把结论给用户确认。
> 本文是操作步骤；规范条款以 `ADAPTER_SPEC.md` 为准。

## 步骤

1. **复制骨架**
   ```bash
   cp scripts/providers/_template.py scripts/providers/<platform>.py
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
   # 期望：content_info.json / content.txt / report_template.md 三件套
   ```
6. **全量回归 + 提交**
   ```bash
   python -m pytest tests/ -q          # 必须全绿
   git add scripts/providers/<platform>.py tests/
   git commit -m "feat(providers): <platform> 适配器"
   ```

## 常见坑

- **限时直链**：音视频直链常带签名与过期时间，取到后要尽快下载；下载失败优先重试获取直链，而不是重试下载同一个 URL。
- **时长单位**：统一用秒（float）；有的平台返回毫秒/字符串（"12:34"），先归一化。
- **分页**：`list_items` 要拉全量再交给 `--pages/--all` 过滤；不要在适配器里做页码选择（那是流水线的职责）。
- **编码**：页面解析统一 `response.encoding` 显式设置（GBK/UTF-8 混用常见）。
- **风控**：带签名参数的平台把签名函数独立出来并写单测；定时/复杂逻辑不要散落在请求代码里。
- **可选依赖**：`import` 放在函数内部，缺失时抛带安装命令的可读异常。
