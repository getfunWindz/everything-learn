import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest

from core import cache as cache_mod
from core import config as core_config
from core import pipeline as pipeline_mod
from core.models import ContentResult, ItemRef, ItemMeta
from core.pipeline import RunOptions, PipelineError, process_item, run_input


class FakeAdapter:
    name = "fake"
    kinds = {"video"}

    def __init__(self, n_items=1, status="ok", kind="timeline", label=""):
        self.status, self.kind = status, kind
        self.n_items = n_items
        self.media_audio = "http://media/audio"
        self.media_video = "http://media/video"
        self.media_calls = []
        if kind == "document":
            self.content = ContentResult(kind="document", status=status,
                                         blocks=[{"heading": "引言", "text": "正文"}],
                                         label=label)
        else:
            self.content = ContentResult(kind="timeline", status=status,
                                         segments=[{"start": 0.0, "end": 1.0, "text": "你好"}],
                                         reason="零命中", label=label)

    def match(self, url):
        return True

    def resolve(self, q):
        return ItemRef(platform="fake", item_id="X1", url="http://fake/v/1",
                       sub_id="1", title="part1", duration=100)

    def list_items(self, ref):
        return [ItemRef(platform="fake", item_id="X1", url=f"http://fake/v/1?p={i}",
                        sub_id=str(i), title=f"part{i}", duration=100)
                for i in range(1, self.n_items + 1)]

    def fetch_meta(self, ref):
        return ItemMeta(title="测试标题", author="作者A", duration=100,
                        page=ref.sub_id or "1", page_part=ref.title)

    def fetch_content(self, ref):
        return self.content

    def fetch_media_url(self, ref, media="audio", lowest=False):
        self.media_calls.append((media, lowest))
        return self.media_video if media == "video" else self.media_audio


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(core_config, "CONFIG_PATH", str(tmp_path / "config.json"))
    yield


def _ref():
    return ItemRef(platform="fake", item_id="X1", url="http://fake/v/1",
                   sub_id="1", title="part1", duration=100)


def test_process_item_ok_writes_artifacts_and_cache(tmp_path):
    out = str(tmp_path / "out")
    status, src, n, out_dir, err = process_item(FakeAdapter(), _ref(), out,
                                                RunOptions(out_root=out), single=True)
    assert (status, src, n, err) == ("ok", "字幕", 1, "")
    assert os.path.exists(os.path.join(out_dir, "content_info.json"))
    assert os.path.exists(os.path.join(out_dir, "content.txt"))
    assert os.path.exists(os.path.join(out_dir, "report_template.md"))
    cached = cache_mod.get_cached("fake", "X1", "1")
    assert cached["kind"] == "timeline" and cached["items"][0]["text"] == "你好"
    # 单条输出命名：<out>/<作者>/<日期>_<标题>
    assert "作者A" in out_dir and "测试标题" in out_dir


def test_process_item_cache_hit(tmp_path):
    out = str(tmp_path / "out")
    cache_mod.set_cached("fake", "X1", "1",
                         {"kind": "timeline", "items": [{"start": 0, "end": 1, "text": "缓存"}]})
    status, src, n, out_dir, err = process_item(FakeAdapter(), _ref(), out,
                                                RunOptions(out_root=out), single=True)
    assert (status, src, n) == ("ok", "cache", 1)
    assert "缓存" in open(os.path.join(out_dir, "content.txt"), encoding="utf-8").read()


def test_process_item_uses_adapter_label(tmp_path):
    """适配器 label 透传到 source（如 字幕(自动)）"""
    out = str(tmp_path / "out")
    status, src, n, out_dir, err = process_item(FakeAdapter(label="字幕(自动)"), _ref(), out,
                                                RunOptions(out_root=out), single=True)
    assert (status, src) == ("ok", "字幕(自动)")


def test_process_item_suspect_without_vision_forces_whisper(tmp_path, monkeypatch):
    import transcriber as tr
    monkeypatch.setattr(tr, "transcribe_url",
                        lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "whisper"}])
    out = str(tmp_path / "out")
    status, src, n, out_dir, err = process_item(FakeAdapter(status="suspect"), _ref(), out,
                                                RunOptions(out_root=out), single=True)
    assert (status, src) == ("ok", "whisper")
    assert "whisper" in open(os.path.join(out_dir, "content.txt"), encoding="utf-8").read()


def test_process_item_invalid_forces_whisper(tmp_path, monkeypatch):
    import transcriber as tr
    monkeypatch.setattr(tr, "transcribe_url", lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "w"}])
    out = str(tmp_path / "out")
    status, src, n, out_dir, err = process_item(FakeAdapter(status="invalid"), _ref(), out,
                                                RunOptions(out_root=out), single=True)
    assert (status, src) == ("ok", "whisper")


def test_process_item_suspect_rescued_by_vision(tmp_path, monkeypatch):
    monkeypatch.setattr(core_config, "load_config",
                        lambda: {"vision": {"enabled": True, "api_key": "k",
                                            "subtitle_check": "auto"}})
    monkeypatch.setattr(pipeline_mod, "_vision_verify_subtitle", lambda *a, **k: True)
    monkeypatch.setattr(pipeline_mod, "_vision_check", lambda *a, **k: None)
    out = str(tmp_path / "out")
    status, src, n, out_dir, err = process_item(FakeAdapter(status="suspect"), _ref(), out,
                                                RunOptions(out_root=out), single=True)
    assert (status, src) == ("ok", "字幕+vision复核")


def test_process_item_no_whisper(tmp_path):
    out = str(tmp_path / "out")
    opts = RunOptions(out_root=out, no_whisper=True)
    status, src, n, out_dir, err = process_item(FakeAdapter(status="empty"), _ref(), out,
                                                opts, single=True)
    assert status == "no_subtitle"


def test_process_item_document_flow_not_ready(tmp_path):
    """document 形态在 M0 未实现 → 明确 failed（不崩溃、不产坏骨架）"""
    out = str(tmp_path / "out")
    status, src, n, out_dir, err = process_item(FakeAdapter(kind="document"), _ref(), out,
                                                RunOptions(out_root=out), single=True)
    assert status == "failed" and "未实现" in err


def test_run_input_single(tmp_path):
    out = str(tmp_path / "out")
    res = run_input("http://fake/v/1", RunOptions(out_root=out), adapter=FakeAdapter())
    assert isinstance(res, tuple) and res[0] == "ok"
    assert os.path.isdir(res[3])


def test_run_input_batch(tmp_path):
    out = str(tmp_path / "out")
    opts = RunOptions(out_root=out, all_=True, interval=0)
    summary = run_input("http://fake/v/1", opts, adapter=FakeAdapter(n_items=3))
    assert summary["kind"] == "batch" and summary["page_count"] == 3
    base = os.path.join(out, "作者A")
    subs = os.listdir(base)
    coll = [d for d in subs if d.endswith("_合集")]
    assert len(coll) == 1
    assert os.path.exists(os.path.join(base, coll[0], "content_info.json"))
    assert os.path.exists(os.path.join(base, coll[0], "report_template.md"))
    assert os.path.exists(os.path.join(base, coll[0], "P01", "content.txt"))


def test_run_input_pages_selection(tmp_path):
    out = str(tmp_path / "out")
    opts = RunOptions(out_root=out, pages="2-3", interval=0)
    summary = run_input("http://fake/v/1", opts, adapter=FakeAdapter(n_items=3))
    assert summary["page_count"] == 2
    assert [r["page"] for r in summary["pages"]] == ["2", "3"]
