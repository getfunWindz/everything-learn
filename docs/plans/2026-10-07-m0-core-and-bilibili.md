# M0 实施计划：核心迁移与 B 站适配器（everything-learn）

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 bilibili-learn@9558e26 的成熟代码迁移进 everything-learn 的「core + providers」架构，实现 B 站功能对等 + 独立配置 + 统一产物契约 + 适配器规范，为 M1（YouTube）铺路。

**Architecture:** 方案 1（Fork 演进）：拷贝代码基线 → 抽出平台无关的 `core/`（模型契约、注册表、配置、缓存、产物、流水线）→ B 站逻辑归入 `providers/bilibili.py` → 新 CLI `el.py`。产物统一为 `content_info.json / content.txt / report_template.md`。

**Tech Stack:** Python 3.10 · pytest · PyAV · requests · faster-whisper（可选）

---

## 前置状态

- 设计 spec：`docs/specs/2026-10-07-everything-learn-design.md`（已评审通过）
- 源码基线：`bilibili-learn` @ `9558e26`（v3.4，160 tests green）
- 本仓库已有 README + spec 首提交 `35cc5c0`

## 目标目录结构（M0 完成后）

```
scripts/
  el.py                    # CLI（替代 bili.py）
  core/
    __init__.py  models.py  adapter.py  registry.py  config.py
    cache.py  retry.py  artifacts.py  pipeline.py  pagespec.py
  providers/
    __init__.py  _template.py  bilibili.py
  transcriber.py  vision.py  report.py
  glossary.py  export.py  doctor.py  favs.py  mergeutil.py
tests/
  conftest.py  test_core_*.py  test_pagespec.py
  providers/test_bilibili.py
  test_el_cli.py  test_subtitle_quality.py  test_transcriber.py
  test_vision.py  test_doctor.py  test_export.py  test_glossary.py
  contract/test_adapter_contract.py
```

---

## Task 0: 基线拷贝（含基线回归）

**Files:** 全量拷贝 `bilibili-learn@9558e26` → 本仓库

- [ ] **Step 1: 拷贝代码基线**

```bash
cd "C:/Users/getfunWindz/.pi/agent/skills"
SRC="bilibili-learn"
DST="everything-learn"
cp -r "$SRC/scripts" "$DST/" && rm -f "$DST/scripts/config.json"
cp -r "$SRC/tests" "$DST/"
mkdir -p "$DST/references" && cp "$SRC/references/glossary.json" "$DST/references/"
cp "$SRC/requirements.txt" "$DST/"
# 清掉拷贝进来的缓存/配置/临时物
find "$DST/scripts" "$DST/tests" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
rm -rf "$DST/scripts/.pytest_cache" "$DST/scripts/cache" "$DST/scripts/logs" "$DST/scripts/.bili_cookie" "$DST/tests/.pytest_cache"
```

- [ ] **Step 2: 适配 .gitignore**

```gitignore
__pycache__/
*.pyc
.pytest_cache/
.bili_cookie
config.json
logs/
cache/
*.tmp
.obsidian/
```

- [ ] **Step 3: 基线回归（关键 sanity check）**

Run: `cd everything-learn && python -m pytest tests/ -q`
Expected: **160 passed**（拷贝过来的测试原样通过，证明基线完整）

- [ ] **Step 4: 提交**

```bash
git add -A && git commit -m "chore: 从 bilibili-learn@9558e26 拷贝代码基线（M0 起点）"
```

---

## Task 1: core 抽象（models / adapter / registry）+ 契约测试框架

**Files:**
- Create: `scripts/core/__init__.py`、`scripts/core/models.py`、`scripts/core/adapter.py`、`scripts/core/registry.py`
- Create: `tests/test_core_models.py`、`tests/test_core_registry.py`、`tests/contract/test_adapter_contract.py`
- Modify: `docs/specs/2026-10-07-everything-learn-design.md`（接口补 `list_items` / `fetch_media_url(lowest)`）

- [ ] **Step 1: 写失败测试**

`tests/test_core_models.py`：

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from core.models import ItemRef, ItemMeta, Segment, Block, TimelineContent, DocumentContent, ContentResult

def test_item_ref_defaults():
    r = ItemRef(platform="bilibili", item_id="BV1xx", sub_id="1", duration=100)
    assert r.platform == "bilibili" and r.sub_id == "1"

def test_timeline_content_serializes_lines():
    c = TimelineContent(segments=[Segment(0.0, 1.5, "你好")])
    assert c.kind == "timeline" and c.segments[0].text == "你好"

def test_document_content_blocks():
    c = DocumentContent(blocks=[Block(heading="引言", text="正文")])
    assert c.kind == "document" and c.blocks[0].heading == "引言"

def test_content_result_status():
    r = ContentResult(kind="timeline", status="suspect", segments=[], reason="零命中")
    assert r.status == "suspect" and r.reason == "零命中"
```

`tests/test_core_registry.py`：

```python
import sys, os, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from core import registry

class FakeAdapter:
    name = "fake"
    kinds = {"video"}
    def match(self, url): return "fake.example" in url
    def resolve(self, q): return None
    def list_items(self, ref): return [ref]
    def fetch_meta(self, ref): return None
    def fetch_content(self, ref): return None
    def fetch_media_url(self, ref, lowest=False): return None

def setup_function(_):
    registry.reset()

def test_register_and_get():
    registry.register(FakeAdapter)
    assert registry.get("fake") is not None
    assert registry.get("nope") is None

def test_match_url():
    registry.register(FakeAdapter)
    assert registry.match_url("https://fake.example/v/1").name == "fake"
    assert registry.match_url("https://other.com/v/1") is None

def test_registry_lists():
    registry.register(FakeAdapter)
    assert [a.name for a in registry.all()] == ["fake"]
```

`tests/contract/test_adapter_contract.py`（共享契约 harness，M1+ 的每个适配器都跑它）：

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from core.adapter import SourceAdapter

def run_contract(adapter):
    """对任意适配器执行契约检查；返回问题列表（空=通过）"""
    problems = []
    if not getattr(adapter, "name", ""):
        problems.append("缺少 name")
    if not isinstance(getattr(adapter, "kinds", None), set) or not adapter.kinds:
        problems.append("kinds 必须为非空 set")
    for m in ("match", "resolve", "list_items", "fetch_meta", "fetch_content", "fetch_media_url"):
        if not callable(getattr(adapter, m, None)):
            problems.append(f"缺少方法 {m}")
    return problems

def test_contract_rejects_incomplete():
    class Bad: pass
    assert run_contract(Bad())  # 有问题

def test_contract_accepts_protocol_shape():
    class Ok(FakeAdapter := type("A", (), {})): pass
    # 见下：用与 test_core_registry 相同的 FakeAdapter 形状
```

> 实现时把 FakeAdapter 提取到 `tests/contract/_fakes.py` 供两个测试文件共用。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_core_models.py tests/test_core_registry.py tests/contract/ -q`
Expected: FAIL（`core` 不存在）

- [ ] **Step 3: 实现 core 模块**

`scripts/core/models.py`：

```python
"""核心数据模型：平台无关的内容契约"""
from dataclasses import dataclass, field


@dataclass
class ItemRef:
    """定位到可处理的最小单元（B站分P / YouTube 列表项 / 播客单集）"""
    platform: str
    item_id: str
    url: str = ""
    sub_id: str = ""
    title: str = ""
    duration: int = 0
    extra: dict = field(default_factory=dict)   # 平台私有（如 bvid/cid）


@dataclass
class ItemMeta:
    title: str
    author: str = ""
    url: str = ""
    published: str = ""
    duration: int = 0
    desc: str = ""
    stats: dict = field(default_factory=dict)
    page: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Block:
    text: str
    heading: str = ""


@dataclass
class TimelineContent:
    segments: list = field(default_factory=list)   # [Segment]
    kind: str = "timeline"


@dataclass
class DocumentContent:
    blocks: list = field(default_factory=list)     # [Block]
    images: list = field(default_factory=list)
    kind: str = "document"


@dataclass
class ContentResult:
    """适配器返回的内容抓取结果（供流水线分级/降级）"""
    kind: str                       # timeline / document
    status: str                     # ok / suspect / invalid / empty
    segments: list = field(default_factory=list)   # timeline：dict {start,end,text}
    blocks: list = field(default_factory=list)     # document：dict {heading,text}
    reason: str = ""
```

`scripts/core/adapter.py`：

```python
"""内容源适配器协议（all providers must implement）"""
from typing import Protocol, runtime_checkable


class SearchNeeded(Exception):
    """输入是关键词而非链接：由 CLI 调 adapter.resolve 搜索后再试"""


@runtime_checkable
class SourceAdapter(Protocol):
    name: str                 # "bilibili" / "youtube" / "podcast" ...
    kinds: set                # {"video"} / {"audio"} / {"article"} / {"thread"}

    def match(self, url: str) -> bool: ...
    def resolve(self, q: str): ...            # 链接/名称 → ItemRef；无法直接定位时抛 SearchNeeded
    def list_items(self, ref): ...            # ItemRef → [ItemRef]（多P/列表展开；单项返回 [ref]）
    def fetch_meta(self, ref): ...            # ItemRef → ItemMeta
    def fetch_content(self, ref): ...         # ItemRef → ContentResult
    def fetch_media_url(self, ref, lowest: bool = False): ...  # 音/视频直链；无媒体返回 None
```

`scripts/core/registry.py`：

```python
"""适配器注册表：注册 / 查询 / URL 路由 / 自动发现"""
import importlib
import pkgutil

_ADAPTERS = {}


def register(adapter_cls):
    """类装饰器：实例化并注册（同名覆盖）"""
    _ADAPTERS[adapter_cls.name] = adapter_cls()
    return adapter_cls


def get(name: str):
    return _ADAPTERS.get(name)


def all():
    return list(_ADAPTERS.values())


def match_url(url: str):
    for a in _ADAPTERS.values():
        try:
            if a.match(url):
                return a
        except Exception:
            continue
    return None


def default():
    """无 URL 匹配时的兜底适配器（M0 = bilibili，供名称搜索用）"""
    return _ADAPTERS.get("bilibili")


def reset():
    _ADAPTERS.clear()


def load_providers(package: str = "providers"):
    """自动发现 providers 包下所有适配器模块（下划线开头/模板跳过）"""
    pkg = importlib.import_module(package)
    for m in pkgutil.iter_modules(pkg.__path__):
        if not m.name.startswith("_"):
            importlib.import_module(f"{package}.{m.name}")
```

- [ ] **Step 4: 同步 spec 接口**

把 `docs/specs/2026-10-07-everything-learn-design.md` §3.2 的接口更新为与 `adapter.py` 一致（新增 `list_items`、`fetch_media_url(ref, lowest=False)`）。

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_core_models.py tests/test_core_registry.py tests/contract/ -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add scripts/core tests/ docs/specs
git commit -m "feat(core): 内容契约与适配器注册表（models/adapter/registry + 契约测试框架）"
```

---

## Task 2: core/config.py（新配置 + out_dir 硬性规则）

**Files:**
- Create: `scripts/core/config.py`（基于 `scripts/config.py` 改写）
- Delete: `scripts/config.py`（先保留到 Task 7 全量改 import 后再删；M0 内统一处理）
- Modify: `tests/test_config.py` → `tests/test_core_config.py`

- [ ] **Step 1: 写失败测试**（迁移 + 新增）

`tests/test_core_config.py`（由 `test_config.py` 改写，import 改 `from core import config as cfg`）：

```python
def test_out_dir_hard_rule(tmp_path, monkeypatch):
    """out_dir 未配置 → 拒绝运行；CLI 覆盖优先"""
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "none.json"))
    import pytest
    with pytest.raises(cfg.ConfigError):
        cfg.resolve_out_dir(None)
    assert cfg.resolve_out_dir("D:/custom") == "D:/custom"

def test_out_dir_from_config(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text('{"out_dir": "D:/笔记"}', encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(p))
    assert cfg.resolve_out_dir(None) == "D:/笔记"

def test_new_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "none.json"))
    c = cfg.load_config()
    assert c["whisper_model"] == "medium" and c["whisper_vad"] == "auto"
    assert c["api_retries"] == 4 and c["vision"]["subtitle_check"] == "auto"
    assert c["out_dir"] == "" and c["auth"] == {}
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_core_config.py -q` → FAIL

- [ ] **Step 3: 实现**

`scripts/core/config.py` = 现有 `config.py` +：

```python
class ConfigError(Exception):
    """配置缺失（如 out_dir 未设置）"""


def resolve_out_dir(cli_out: str = None) -> str:
    """CLI 参数 > config.out_dir > 报错（硬性要求用户自选根路径）"""
    if cli_out:
        return cli_out
    out = load_config().get("out_dir") or ""
    if not out:
        raise ConfigError(
            "未配置报告输出目录。请先选择存放学习报告的根文件夹（建议新建「学习笔记」总文件夹），"
            "写入 scripts/config.json 的 out_dir，或运行时用 --out 指定。")
    return out
```

`DEFAULT_CONFIG` 按 spec §9 全量替换（out_dir 空、whisper_* 沿用、auth、vision 含 subtitle_check）。删除旧哈希字段语义不变。

- [ ] **Step 4: 全量回归**

Run: `python -m pytest tests/ -q`
Expected: PASS（老 `test_config.py` 仍走旧 `scripts/config.py`，两套暂并存；Task 7 统一删旧）

- [ ] **Step 5: 提交**

```bash
git add scripts/core/config.py tests/test_core_config.py
git commit -m "feat(core): 新配置体系与输出目录硬性规则（resolve_out_dir）"
```

---

## Task 3: core/cache.py + core/retry.py（含缓存键平台化）

**Files:**
- Create: `scripts/core/cache.py`、`scripts/core/retry.py`（自 `scripts/subcache.py` 拆分改写）
- Tests: `tests/test_core_cache.py`（部分迁移 `test_cache_merge.py`）

- [ ] **Step 1: 写失败测试**

```python
def test_cache_key_platform_aware(tmp_path, monkeypatch):
    import core.cache as cache
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    cache.set_cached("bilibili", "BV1xx", "1", [{"start": 0, "end": 1, "text": "hi"}])
    assert cache.get_cached("youtube", "BV1xx", "1") is None   # 同 id 不同平台不串
    assert cache.get_cached("bilibili", "BV1xx", "1")[0]["text"] == "hi"

def test_retry_backoff_succeeds():
    from core.retry import retry_with_backoff
    n = {"i": 0}
    def flaky():
        n["i"] += 1
        if n["i"] < 3:
            raise RuntimeError("boom")
        return "ok"
    assert retry_with_backoff(flaky, attempts=4, base_delay=0) == "ok"
```

- [ ] **Step 2: 运行确认失败** → FAIL

- [ ] **Step 3: 实现**

`core/cache.py`：`_cache_path(platform, item_id, sub_id="")` → `{platform}_{item_id}[_{sub_id}].json`；`get_cached/set_cached/clear_cache` 全部加 `platform` 第一参数（原子写保留）。
`core/retry.py`：`retry_with_backoff(fn, attempts=3, base_delay=2.0)` 从 subcache 原样迁入。

- [ ] **Step 4: 提交**

```bash
git add scripts/core/cache.py scripts/core/retry.py tests/test_core_cache.py
git commit -m "feat(core): 平台化缓存键与通用退避重试"
```

---

## Task 4: transcriber / vision 迁移（平台解耦）

**Files:**
- Copy: `scripts/transcriber.py`、`scripts/vision.py`（基本原样）
- Modify: `transcriber.transcribe_video(client, bvid, cid, …)` → 新增 `transcribe_url(url, duration_sec=..., …)`；`transcribe_video` 保留为薄封装（兼容旧测试）
- Tests: `tests/test_transcriber.py`、`tests/test_vision.py` 原样拷贝即绿（import 不变）

- [ ] **Step 1: 写失败测试**

```python
def test_transcribe_url_flow(tmp_path, monkeypatch):
    """平台无关入口：给直链→下载→转写"""
    monkeypatch.setattr(transcriber, "download_audio", lambda url, dest: dest)
    monkeypatch.setattr(transcriber, "transcribe",
                        lambda path, model_size="medium", **kw: [{"start": 0.0, "end": 1.0, "text": "hi"}])
    out = transcriber.transcribe_url("http://media/audio.m4s", model_size="small", duration_sec=10)
    assert out == [{"start": 0.0, "end": 1.0, "text": "hi"}]
```

- [ ] **Step 2: 运行确认失败** → FAIL

- [ ] **Step 3: 实现**

`transcribe_url(url, model_size="medium", progress_callback=None, vad=None, duration_sec=None, vad_long_sec=1800)` = 原 `transcribe_video` 去掉 client/bvid/cid，直接下载 url 后调用 `transcribe`；`transcribe_video(client, bvid, cid, **kw)` 改为 `return transcribe_url(client.get_audio_url(bvid, cid), **kw)`。

- [ ] **Step 4: 回归 + 提交**

Run: `python -m pytest tests/test_transcriber.py tests/test_vision.py -q` → PASS

```bash
git add scripts/transcriber.py scripts/vision.py tests/
git commit -m "refactor(transcriber): 平台无关的 transcribe_url 入口"
```

---

## Task 5: providers/bilibili.py（ApiClient + 适配器）

**Files:**
- Create: `scripts/providers/__init__.py`、`scripts/providers/bilibili.py`（= 旧 `api_client.py` + `resolver.py` + 新 `BilibiliAdapter`）
- Delete（Task 7 统一）: `scripts/api_client.py`、`scripts/resolver.py`
- Tests: `tests/providers/test_bilibili.py`（迁移 `test_api_client.py`，import 改 `from providers.bilibili import ApiClient, BiliError, VideoInfo, SubtitleLine, SubtitleResult`）

- [ ] **Step 1: 写失败测试**

```python
def test_bilibili_adapter_interface():
    from providers.bilibili import BilibiliAdapter
    a = BilibiliAdapter(client=FakeClient())   # 见 tests/providers 内的 fake
    assert a.name == "bilibili" and "video" in a.kinds
    assert a.match("https://www.bilibili.com/video/BV1GJ411x7h7") is True
    assert a.match("https://youtube.com/watch?v=x") is False

def test_bilibili_adapter_list_items():
    from providers.bilibili import BilibiliAdapter
    a = BilibiliAdapter(client=FakeClient())
    ref = a.resolve("BV1GJ411x7h7")
    items = a.list_items(ref)
    assert len(items) >= 1 and items[0].platform == "bilibili"

def test_bilibili_fetch_content_wraps_subtitle_result(monkeypatch):
    """fetch_content → ContentResult（state 透传分级）"""
    ...
```

- [ ] **Step 2: 运行确认失败** → FAIL

- [ ] **Step 3: 实现**

```python
# providers/bilibili.py 骨架
class BilibiliAdapter:
    name = "bilibili"
    kinds = {"video"}

    def __init__(self, client=None):
        self.client = client or ApiClient()

    def match(self, url): ...        # bilibili.com / b23.tv / BV/av 模式
    def resolve(self, q):
        from providers.bilibili_resolver import resolve_input, SearchNeeded  # 或本文件内合并
        spec = resolve_input(q)      # 旧 resolver 逻辑
        return ItemRef(platform="bilibili", item_id=spec.bvid,
                       url=f"https://www.bilibili.com/video/{spec.bvid}",
                       sub_id=str(spec.page or ""), extra={"aid": spec.aid})
    def list_items(self, ref):
        info = self.client.get_video_info(bvid=ref.item_id)
        return [ItemRef(platform="bilibili", item_id=ref.item_id, sub_id=str(p.page),
                        title=p.part, duration=p.duration,
                        extra={"aid": info.aid, "cid": p.cid, "owner": info.owner,
                               "info": info}) for p in info.pages]
    def fetch_meta(self, ref): ...
    def fetch_content(self, ref):
        res = self.client.fetch_subtitle(ref.item_id, ref.extra["cid"], duration=ref.duration,
                                         lang=self._lang, title=ref.title, desc=ref.extra.get("desc",""))
        return ContentResult(kind="timeline", status=res.status,
                             segments=[{"start": l.start, "end": l.end, "text": l.content} for l in res.lines],
                             reason=res.reason)
    def fetch_media_url(self, ref, lowest=False):
        if lowest: return self.client.get_video_url(ref.item_id, ref.extra["cid"], lowest=True)
        return self.client.get_audio_url(ref.item_id, ref.extra["cid"])
```

（`resolver.py` 逻辑合入 `providers/bilibili.py` 或保留为 `providers/bilibili_resolver.py`——实现时二选一并统一 import。）

- [ ] **Step 4: 回归 + 提交**

Run: `python -m pytest tests/providers/ -q` → PASS

```bash
git add scripts/providers tests/providers
git commit -m "feat(providers): B站适配器（ApiClient + SourceAdapter 接口实现）"
```

---

## Task 6: core/artifacts.py + report.py + core/pipeline.py

**Files:**
- Create: `scripts/core/artifacts.py`、`scripts/core/pipeline.py`、`scripts/report.py`（自 `bili.py` 抽出渲染逻辑）
- Tests: `tests/test_core_artifacts.py`、`tests/test_core_pipeline.py`

- [ ] **Step 1: 写失败测试**

```python
def test_write_artifacts_timeline(tmp_path):
    from core.artifacts import write_item_artifacts
    from core.models import ItemMeta, TimelineContent
    info = write_item_artifacts(
        str(tmp_path), meta=ItemMeta(title="标题", author="UP", url="u", duration=100),
        content=TimelineContent(segments=[{"start": 0.0, "end": 1.5, "text": "你好"}]),
        source="字幕", platform="bilibili", item_id="BV1xx", sub_id="1")
    assert (tmp_path / "content_info.json").exists()
    assert (tmp_path / "content.txt").read_text(encoding="utf-8").startswith("[0.0-1.5] 你好")
    assert "## 知识详解" in (tmp_path / "report_template.md").read_text(encoding="utf-8")
    assert info["platform"] == "bilibili" and info["kind"] == "timeline"

def test_pipeline_process_item_uses_adapter(tmp_path, monkeypatch):
    """流程：adapter.fetch_content(ok) → 产物落盘 + 写缓存"""
    ...
```

- [ ] **Step 2: 运行确认失败** → FAIL

- [ ] **Step 3: 实现**

- `core/artifacts.py`：`write_item_artifacts(out_dir, meta, content, source, platform, item_id, sub_id="", stats=None) -> dict`；`render_content_txt(content) -> str`（timeline：`[start-end] text`；document：Markdown）；`report_template.md` 由 `report.render_template(kind, info)` 生成。
- `report.py`：把 `bili.render_template` / `render_batch_template` 移入（新文件名 `content_info.json`），按 `kind` 分发（M0 仅 timeline；document 抛 `NotImplementedError("M3 实现")`——不做假实现）。
- `core/pipeline.py`：把 `bili._process_page/_run_batch/_vision_check/_vision_verify_subtitle/_want_subtitle_check` 泛化为 adapter 版本：

```python
def process_item(adapter, ref, out_root, opts, single=False):
    # 1) 缓存 → 2) adapter.fetch_content → 3) suspect：vision 3帧复检（adapter.fetch_media_url(lowest=True)）
    # 4) empty/invalid/复检失败：adapter.fetch_media_url → transcriber.transcribe_url（尊重 vad/no_whisper）
    # 5) 内容完整性复检（vision.check_transcript，adapter.fetch_media_url(lowest=False)）
    # 6) core.artifacts 落盘 + core.cache 写缓存
    ...  # 返回 (status, source, line_count, out_dir, error)

def run_input(input_str, opts, adapter=None, client=None): ...
def run_batch(adapter, refs, opts, out_root): ...   # 沿用 bili._run_batch 结构（增量汇总/断点续跑/间隔）
```

- [ ] **Step 4: 回归 + 提交**

Run: `python -m pytest tests/test_core_artifacts.py tests/test_core_pipeline.py -q` → PASS

```bash
git add scripts/core/artifacts.py scripts/core/pipeline.py scripts/report.py tests/test_core_artifacts.py tests/test_core_pipeline.py
git commit -m "feat(core): 通用流水线、产物三件套与按形态渲染"
```

---

## Task 7: el.py CLI + 其余模块迁移 + 测试全量迁移

**Files:**
- Create: `scripts/el.py`（自 `bili.py` 改写：`import config` → `from core import config`；调 `core.pipeline`；命令保留 run/resolve/search/frames/favs/favs-scan/merge/report/export/doctor）
- Move: `glossary.py` / `export.py` / `doctor.py` / `favs.py` / `mergeutil.py` / `pagespec.py` →（pagespec 进 `core/`；其余留在 scripts/ 根）；`subcache.py` 删除（已拆入 core）
- Delete: `scripts/bili.py`、`scripts/api_client.py`、`scripts/config.py`、`scripts/resolver.py`、`scripts/subcache.py`
- Tests: `test_cli.py` → `tests/test_el_cli.py`（`import el`），`test_favs/test_doctor/test_export/test_glossary/test_pagespec/test_cache_merge` 按新 import 迁移

- [ ] **Step 1: 迁移 CLI 并改造测试（红）**

Run: `python -m pytest tests/test_el_cli.py -q` → FAIL（el 不存在/接口未对齐）

- [ ] **Step 2: 实现**

- `el.py`：`cmd_run(args, client=None)` → 构造/注入适配器（`client` 为测试缝：bilibili 输入时 `BilibiliAdapter(client=client)`）→ `pipeline.run_input`；`out_dir` 用 `config.resolve_out_dir(args.out)`，捕获 `ConfigError` 输出友好提示 + 退出码 2。
- 统一 import：`from core import config`；`import subcache` → `from core import cache`
- `pagespec.py` → `core/pagespec.py`，更新 import。

- [ ] **Step 3: 全量回归**

Run: `python -m pytest tests/ -q`
Expected: 全部 PASS（迁移后的 160 项 + 新增 core/契约测试；`video_info.json`/`subtitle.txt` 断言全部改为 `content_info.json`/`content.txt`）

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "refactor: 主 CLI 迁移为 el.py，模块 import 统一到 core，产物命名切换"
```

---

## Task 8: 适配器规范、模板与 SKILL.md

**Files:**
- Create: `docs/ADAPTER_SPEC.md`、`docs/ADAPTER_TEMPLATE.md`、`scripts/providers/_template.py`、`SKILL.md`
- Modify: `README.md`（状态、用法）

- [ ] **Step 1: ADAPTER_SPEC.md**（按设计 §6.3 落成可执行规范）

内容：接口定义、归一化要求、错误处理约定、凭据规则、注册方式、测试三件套（fixture/契约/冒烟）、**可行性尽调清单**（§6.2 表格原样落文档）、模板与示例引用。

- [ ] **Step 2: providers/_template.py**（骨架，含 TODO 注释但可被 `load_providers` 跳过）

```python
"""适配器模板：复制本文件为 <platform>.py 后按注释实现。
注意：文件名勿以 _ 开头才会被自动发现（本文件故意以 _ 开头，不会被注册）。"""
# from core.registry import register
# from core.models import ItemRef, ItemMeta, ContentResult

# @register
# class MyAdapter:
#     name = "example"        # 平台标识（config.auth 键同名）
#     kinds = {"video"}       # {"video"} / {"audio"} / {"article"} / {"thread"}
#     def match(self, url): ...
#     def resolve(self, q): ...
#     def list_items(self, ref): ...
#     def fetch_meta(self, ref): ...
#     def fetch_content(self, ref): ...
#     def fetch_media_url(self, ref, lowest=False): ...
```

- [ ] **Step 3: SKILL.md**（新技能入口文档）

结构按设计 §5.4：支持平台表（B站可用；YouTube M1 规划中）、首次配置硬性路径规则、工作流、自进化引导（§6 流程）、共用/形态规则、交付。**用户侧不写死任何本机路径**。

- [ ] **Step 4: README 更新**（状态 → M0 完成；用法 `python scripts/el.py run <URL>`；测试数）

- [ ] **Step 5: 提交**

```bash
git add SKILL.md README.md docs/ADAPTER_SPEC.md docs/ADAPTER_TEMPLATE.md scripts/providers/_template.py
git commit -m "docs: 适配器规范/模板与 everything-learn SKILL.md"
```

---

## Task 9: 全量回归 + B 站真实冒烟 + M0 验收

- [ ] **Step 1: 全量测试**

Run: `python -m pytest tests/ -q`
Expected: 全绿（数量 ≥ 旧 160 + 新增 core/契约用例）

- [ ] **Step 2: B 站端到端冒烟（两条路径）**

```bash
# 字幕路径（挑一个字幕健康视频）
python scripts/el.py run <带字幕BV> --out <临时目录> --no-cache
# Whisper 兜底路径（挑一个已知错乱/无字幕视频）
python scripts/el.py run BV1Mo8R6UEEB --page 1 --out <临时目录> --no-cache
```
Expected: 产物三件套落盘；`content_info.json.source` 分别符合预期（`字幕` / `whisper`）

- [ ] **Step 3: 无 out_dir 硬性规则冒烟**

Run: `python scripts/el.py run BV1xx`（不配 out_dir、不带 --out）
Expected: 友好报错（提示用户选择根路径），退出码 2，无 Traceback

- [ ] **Step 4: 验收清单核对 + 提交**

- [ ] 原 160 测试全部迁移且通过（无删除/削弱）
- [ ] `core/` 与 `providers/` 分层落实；B站逻辑不再散在 CLI
- [ ] 产物统一为 `content_info.json / content.txt / report_template.md`
- [ ] `out_dir` 硬性规则生效（CLI 拒绝运行 + 提示）
- [ ] 适配器契约 harness 就绪（M1 直接跑）
- [ ] ADAPTER_SPEC / 模板 / SKILL.md 就位
- [ ] 真实冒烟通过（字幕 + Whisper 两条路径）

```bash
git add -A && git commit -m "test: M0 验收（全量回归 + B站冒烟两条路径）" # 如有残留改动
git push origin main
```

---

## 提交节奏（预期）

1. `chore: 从 bilibili-learn@9558e26 拷贝代码基线（M0 起点）`
2. `feat(core): 内容契约与适配器注册表（models/adapter/registry + 契约测试框架）`
3. `feat(core): 新配置体系与输出目录硬性规则（resolve_out_dir）`
4. `feat(core): 平台化缓存键与通用退避重试`
5. `refactor(transcriber): 平台无关的 transcribe_url 入口`
6. `feat(providers): B站适配器（ApiClient + SourceAdapter 接口实现）`
7. `feat(core): 通用流水线、产物三件套与按形态渲染`
8. `refactor: 主 CLI 迁移为 el.py，模块 import 统一到 core，产物命名切换`
9. `docs: 适配器规范/模板与 everything-learn SKILL.md`
10. `test: M0 验收（全量回归 + B站冒烟两条路径）`（如有残留改动）

## 风险与对策

- **产物重命名波及广**：全仓 grep `video_info.json|subtitle.txt` 一次性清理；旧名不允许残留。
- **测试迁移量大**：先跑通基线（Task 0 的 160 green），再按 Task 顺序小步迁移，每任务单独回归。
- **B站接口偶发错乱**：沿用 v3.4 防线（分级 + 归属复检 + Whisper 兜底），冒烟时如遇接口抖动，按 v3.4 会话中的方式判定处理。
