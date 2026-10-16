import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest

from core import cache as cache_mod
from core import config as core_config
from core import pipeline as pipeline_mod
from core.models import ContentResult, ItemRef, ItemMeta
from core.pipeline import RunOptions, PipelineError, UnsupportedUrlError, process_item, run_input


class FakeAdapter:
    name = "fake"
    kinds = {"video"}

    def __init__(self, n_items=1, status="ok", kind="timeline", label="", images=()):
        self.status, self.kind = status, kind
        self.n_items = n_items
        self.media_audio = "http://media/audio"
        self.media_video = "http://media/video"
        self.media_calls = []
        if kind == "document":
            self.content = ContentResult(kind="document", status=status,
                                         blocks=[{"heading": "引言", "text": "正文"}],
                                         images=list(images), label=label)
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
    # M13/T12：本文件 FakeAdapter 均以“字幕可用”为前提。新来源策略下 unknown 字幕需过
    # 同源校验才会被采信；默认放行以避免真实下载/转写，来源策略本身由
    # test_core_pipeline_recheck.py 覆盖（需失败/兜底的测试自行覆盖此 patch）。
    monkeypatch.setattr(pipeline_mod, "_same_source_verify",
                        lambda *a, **k: {"checked": True, "passed": True, "avg": 1.0})
    yield


def _ref():
    return ItemRef(platform="fake", item_id="X1", url="http://fake/v/1",
                   sub_id="1", title="part1", duration=100)


def test_media_candidates_uses_adapter_hook():
    class A:
        def fetch_media_url(self, ref, media="audio", lowest=False):
            return "single"

        def fetch_media_urls(self, ref, media="audio", lowest=False):
            return ["one", "", None, "two"]

    assert pipeline_mod._media_candidates(A(), _ref(), "audio") == ["one", "two"]


def test_media_candidates_falls_back_to_single():
    class A:
        def fetch_media_url(self, ref, media="audio", lowest=False):
            return "single"

    assert pipeline_mod._media_candidates(A(), _ref(), "audio") == ["single"]


def test_media_candidates_hook_error_falls_back():
    class A:
        def fetch_media_url(self, ref, media="audio", lowest=False):
            return "single"

        def fetch_media_urls(self, ref, media="audio", lowest=False):
            raise RuntimeError("hook boom")

    assert pipeline_mod._media_candidates(A(), _ref(), "audio") == ["single"]


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


def test_cache_hit_restores_provenance_language_and_report(tmp_path):
    """B3：命中缓存时 content_info 仍含 provenance/language/recheck，并补写 recheck_report.json"""
    out = str(tmp_path / "out")
    cache_mod.set_cached("fake", "X1", "1", {
        "kind": "timeline", "items": [{"start": 0.0, "end": 1.0, "text": "你好"}],
        "provenance": "whisper", "language": "zh",
        "recheck": {"tier": 1, "score": 0.1, "reasons": ["x"], "uncovered": []},
        "recheck_report": {"tier": 1, "score": 0.1, "reasons": ["x"],
                            "same_source": {}, "uncertain": []},
    })
    status, src, n, out_dir, err = process_item(FakeAdapter(), _ref(), out,
                                                RunOptions(out_root=out), single=True)
    assert (status, src) == ("ok", "cache")
    info = json.load(open(os.path.join(out_dir, "content_info.json"), encoding="utf-8"))
    assert info["provenance"] == "whisper" and info["language"] == "zh"
    assert info["recheck"]["tier"] == 1
    assert os.path.exists(os.path.join(out_dir, "recheck_report.json"))


def test_cache_hit_document_keeps_recheck(tmp_path):
    """B3：document 命中缓存时同样恢复 provenance/recheck"""
    out = str(tmp_path / "out")
    cache_mod.set_cached("fake", "X1", "1", {
        "kind": "document", "items": [{"heading": "", "text": "正文"}], "images": [],
        "provenance": "whisper", "language": None,
        "recheck": {"tier": 1, "score": 0.2, "reasons": ["y"], "lite": True},
        "recheck_report": {"tier": 1, "score": 0.2, "reasons": ["y"], "lite": True},
    })
    status, src, n, out_dir, err = process_item(FakeAdapter(kind="document"), _ref(), out,
                                                RunOptions(out_root=out), single=True)
    assert (status, src) == ("ok", "cache")
    info = json.load(open(os.path.join(out_dir, "content_info.json"), encoding="utf-8"))
    assert info["provenance"] == "whisper" and info["recheck"]["tier"] == 1
    assert os.path.exists(os.path.join(out_dir, "recheck_report.json"))


def test_process_item_uses_adapter_label(tmp_path):
    """适配器 label 透传到 source（如 字幕(自动)）"""
    out = str(tmp_path / "out")
    status, src, n, out_dir, err = process_item(FakeAdapter(label="字幕(自动)"), _ref(), out,
                                                RunOptions(out_root=out), single=True)
    assert (status, src) == ("ok", "字幕(自动)")


def test_process_item_official_same_source_fail_forces_whisper(tmp_path, monkeypatch):
    """M13/T12：旧 status 分级退役；official 字幕未过同源校验 → Whisper 重转"""
    import transcriber as tr
    monkeypatch.setattr(tr, "transcribe_url",
                        lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "whisper"}])
    monkeypatch.setattr(pipeline_mod, "_same_source_verify",
                        lambda *a, **k: {"checked": False, "passed": False, "reason_code": "error"})

    class OfficialAdapter(FakeAdapter):
        def __init__(self):
            super().__init__()
            self.content = ContentResult(kind="timeline", status="ok", provenance="official",
                                         segments=[{"start": 0.0, "end": 1.0, "text": "官方字幕"}])

    out = str(tmp_path / "out")
    status, src, n, out_dir, err = process_item(OfficialAdapter(), _ref(), out,
                                                RunOptions(out_root=out), single=True)
    assert (status, src) == ("ok", "whisper")
    assert "whisper" in open(os.path.join(out_dir, "content.txt"), encoding="utf-8").read()


def test_process_item_none_provenance_goes_whisper(tmp_path, monkeypatch):
    """M13/T12：provenance=none 且无字幕候选 → 直接 Whisper（旧 invalid 分级退役）"""
    import transcriber as tr
    monkeypatch.setattr(tr, "transcribe_url", lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "w"}])

    class NoCandidateAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(status="empty")
            self.content = ContentResult(kind="timeline", status="empty", provenance="none",
                                         segments=[])

    out = str(tmp_path / "out")
    status, src, n, out_dir, err = process_item(NoCandidateAdapter(), _ref(), out,
                                                RunOptions(out_root=out), single=True)
    assert (status, src) == ("ok", "whisper")


def test_process_item_no_audio_attribution_fail_forces_whisper(tmp_path, monkeypatch):
    """M13/T12：无音频流 → 视觉归属兜底；兜底失败 → Whisper（旧 vision 裁决路径退役）"""
    import transcriber as tr
    monkeypatch.setattr(tr, "transcribe_url",
                        lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "w"}])
    monkeypatch.setattr(pipeline_mod, "_same_source_verify",
                        lambda *a, **k: {"checked": False, "passed": False,
                                         "reason_code": "no_audio", "reason": "无可下载音频流"})
    monkeypatch.setattr(pipeline_mod, "_attribution_fallback",
                        lambda *a, **k: {"passed": False, "reason": "vision 未配置"})
    out = str(tmp_path / "out")
    status, src, n, out_dir, err = process_item(FakeAdapter(), _ref(), out,
                                                RunOptions(out_root=out), single=True)
    assert (status, src) == ("ok", "whisper")


def test_process_item_no_whisper(tmp_path):
    """无字幕候选 + --no-whisper → no_subtitle（语义保持）"""
    class EmptyAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(status="empty")
            self.content = ContentResult(kind="timeline", status="empty", provenance="none",
                                         segments=[])

    out = str(tmp_path / "out")
    opts = RunOptions(out_root=out, no_whisper=True)
    status, src, n, out_dir, err = process_item(EmptyAdapter(), _ref(), out, opts, single=True)
    assert status == "no_subtitle"


def test_process_item_document_flow_ok(tmp_path):
    """document 形态（M3）：正文 → 章节式骨架落盘 + 缓存 kind=document"""
    out = str(tmp_path / "out")
    status, src, n, out_dir, err = process_item(FakeAdapter(kind="document"), _ref(), out,
                                                RunOptions(out_root=out), single=True)
    assert (status, src, n, err) == ("ok", "document", 1, "")
    tpl = open(os.path.join(out_dir, "report_template.md"), encoding="utf-8").read()
    assert "知识地图" in tpl
    cached = cache_mod.get_cached("fake", "X1", "1")
    assert cached["kind"] == "document"


def test_process_item_document_downloads_images(tmp_path, monkeypatch):
    """document 正文图片 → assets/ 且记录到 content_info.assets"""
    import core.images as images_mod
    monkeypatch.setattr(images_mod, "download_images",
                        lambda urls, out_dir, **k: ["assets/img_01.jpg"])
    out = str(tmp_path / "out")
    a = FakeAdapter(kind="document", images=["https://pic1.zhimg.com/a.jpg"])
    status, src, n, out_dir, err = process_item(a, _ref(), out, RunOptions(out_root=out), single=True)
    info = json.load(open(os.path.join(out_dir, "content_info.json"), encoding="utf-8"))
    assert status == "ok" and info["assets"] == ["assets/img_01.jpg"]


def test_process_item_document_video_lite_recheck(tmp_path):
    """B2：视频帖（document）产 lite 版档位复检 meta + recheck_report.json"""
    a = FakeAdapter(kind="document")
    a.content.video_lines = [{"start": 0.0, "end": 10.0,
                              "text": "这段讲了性质变化和方向比较，单位是米"}]
    a.content.video_duration = 10.0
    out = str(tmp_path / "out")
    status, src, n, out_dir, err = process_item(a, _ref(), out, RunOptions(out_root=out), single=True)
    assert (status, src, n, err) == ("ok", "document", 1, "")
    info = json.load(open(os.path.join(out_dir, "content_info.json"), encoding="utf-8"))
    assert info["provenance"] == "whisper"
    assert info["recheck"]["lite"] is True
    assert info["recheck"]["tier"] in (1, 2)
    assert os.path.exists(os.path.join(out_dir, "recheck_report.json"))


def test_process_item_document_without_video_no_recheck(tmp_path):
    """B2：纯图文 document（无视频转写）不产复检记录"""
    out = str(tmp_path / "out")
    status, src, n, out_dir, err = process_item(FakeAdapter(kind="document"), _ref(), out,
                                                RunOptions(out_root=out), single=True)
    info = json.load(open(os.path.join(out_dir, "content_info.json"), encoding="utf-8"))
    assert status == "ok" and not info.get("recheck")


def test_run_input_meta_failure_becomes_pipeline_error(tmp_path):
    """适配器 fetch_meta 失败（如 cookie 失效）→ PipelineError（可读，非裸 ValueError）"""
    class BadMeta(FakeAdapter):
        def fetch_meta(self, ref):
            raise RuntimeError("cookie 失效")
    out = str(tmp_path / "out")
    try:
        run_input("http://fake/v/1", RunOptions(out_root=out), adapter=BadMeta())
        assert False
    except PipelineError as e:
        assert "cookie 失效" in str(e)


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


def test_run_input_falls_back_to_sniff(tmp_path):
    """无 match 的 URL → sniff 适配器接管（不再误送 bilibili 搜索）"""
    from core import registry as reg

    class SniffAdapter:
        name = "sniffable"
        kinds = {"audio"}
        def match(self, url): return False
        def sniff(self, url): return "sniff.example" in url
        def resolve(self, q):
            return ItemRef(platform="sniffable", item_id="F1", url=q, sub_id="")
        def list_items(self, ref): return [ref]
        def fetch_meta(self, ref):
            return ItemMeta(title="播客标题", author="主播A", duration=10)
        def fetch_content(self, ref):
            return ContentResult(kind="timeline", status="ok",
                                 segments=[{"start": 0.0, "end": 1.0, "text": "hi"}])
        def fetch_media_url(self, ref, media="audio", lowest=False): return None

    reg.reset()
    reg.register(SniffAdapter)
    try:
        out = str(tmp_path / "out")
        res = run_input("https://sniff.example/audio/123", RunOptions(out_root=out))
        assert isinstance(res, tuple) and res[0] == "ok"
    finally:
        reg.reset()


def test_run_input_list_items_failure_becomes_pipeline_error(tmp_path):
    """适配器 list_items 失败（如 feed 不可达/CF 拦截）→ PipelineError（可读）"""
    class BadList(FakeAdapter):
        def list_items(self, ref):
            raise RuntimeError("feed 不可达（HTTP 403）")
    out = str(tmp_path / "out")
    try:
        run_input("http://fake/v/1", RunOptions(out_root=out), adapter=BadList())
        assert False
    except PipelineError as e:
        assert "feed 不可达" in str(e)


def test_media_headers_hook():
    from core.pipeline import _media_headers

    class A:
        def media_headers(self): return {"X": "1"}

    class B:
        def media_headers(self): raise RuntimeError("boom")

    assert _media_headers(A()) == {"X": "1"}
    assert _media_headers(object()) is None
    assert _media_headers(B()) is None


def test_run_input_uses_adapter_root_meta_hook(tmp_path):
    """合集根 meta：适配器可选钩子 fetch_root_meta 优先于 refs[0]（账号级列表语义）"""
    class HookAdapter(FakeAdapter):
        def fetch_root_meta(self, ref, refs):
            return ItemMeta(title="合集根标题", author="合集作者")
    out = str(tmp_path / "out")
    opts = RunOptions(out_root=out, all_=True, interval=0)
    run_input("http://fake/v/1", opts, adapter=HookAdapter(n_items=2))
    base = os.path.join(out, "合集作者")
    coll = [d for d in os.listdir(base) if d.endswith("_合集")]
    assert coll and "合集根标题" in coll[0]


def test_run_input_unknown_url_raises_unsupported(tmp_path, monkeypatch):
    """名单外平台 URL：不得兜底成 B 站搜索，必须给自进化提示"""
    from core import registry
    monkeypatch.setattr(registry, "match_url", lambda u: None)
    monkeypatch.setattr(registry, "sniff_url", lambda u: None)
    with pytest.raises(UnsupportedUrlError) as ei:
        run_input("https://vimeo.com/12345", RunOptions(out_root=str(tmp_path / "out")))
    assert "自进化" in str(ei.value)


def test_run_input_name_still_falls_back_to_default(tmp_path, monkeypatch):
    """名称类输入仍走默认适配器（B 站搜索）"""
    from core import registry
    from core.adapter import SearchNeeded

    class _Namer:
        def resolve(self, q):
            raise SearchNeeded(q)

    monkeypatch.setattr(registry, "match_url", lambda u: None)
    monkeypatch.setattr(registry, "sniff_url", lambda u: None)
    monkeypatch.setattr(registry, "default", lambda: _Namer())
    with pytest.raises(SearchNeeded):
        run_input("某个视频名", RunOptions(out_root=str(tmp_path / "out")))


def test_whisper_language_resolution(monkeypatch):
    """适配器 language_hint 应透传到 transcriber；--lang 优先"""
    from core import pipeline
    captured = {}

    class Adapter:
        name = "demo"
        language_hint = "en"
        def fetch_content(self, ref):
            from core.models import ContentResult
            return ContentResult(kind="timeline", status="empty", segments=[])
        def fetch_meta(self, ref):
            from core.models import ItemMeta
            return ItemMeta(title="t", author="a", duration=60)
        def fetch_media_url(self, ref, media="audio", lowest=False):
            return "http://media.demo/audio.m4s"

    def fake_transcribe_url(url, model_size="medium", language="zh", **kw):
        captured["language"] = language
        return [{"start": 0, "end": 1, "text": "hello"}]

    import core.config as core_config
    monkeypatch.setattr(pipeline.config, "load_config", lambda: dict(core_config.DEFAULT_CONFIG))
    monkeypatch.setattr(pipeline.config, "apply_hf_env", lambda *a, **k: None)
    import transcriber
    monkeypatch.setattr(transcriber, "transcribe_url", fake_transcribe_url)
    from core.models import ItemRef
    opts = pipeline.RunOptions(out_root=".", no_cache=True)
    monkeypatch.setattr(pipeline, "_item_out_dir", lambda *a, **k: "out")
    monkeypatch.setattr(pipeline.artifacts, "write_item_artifacts", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.artifacts, "write_recheck_report", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.artifacts, "build_info", lambda meta, ref, **k: {"kind": "timeline", **k})
    pipeline.process_item(Adapter(), ItemRef(platform="demo", item_id="1", duration=60), ".", opts, single=True)
    assert captured["language"] == "en"


def test_whisper_language_cli_override(monkeypatch):
    """--lang 优先于适配器 language_hint"""
    from core import pipeline
    captured = {}

    class Adapter:
        name = "demo"
        language_hint = "en"
        def fetch_content(self, ref):
            from core.models import ContentResult
            return ContentResult(kind="timeline", status="empty", segments=[])
        def fetch_meta(self, ref):
            from core.models import ItemMeta
            return ItemMeta(title="t", author="a", duration=60)
        def fetch_media_url(self, ref, media="audio", lowest=False):
            return "http://media.demo/audio.m4s"

    import core.config as core_config
    monkeypatch.setattr(pipeline.config, "load_config", lambda: dict(core_config.DEFAULT_CONFIG))
    monkeypatch.setattr(pipeline.config, "apply_hf_env", lambda *a, **k: None)
    import transcriber
    monkeypatch.setattr(transcriber, "transcribe_url",
                        lambda url, model_size="medium", language="zh", **kw: (
                            captured.update(language=language) or
                            [{"start": 0, "end": 1, "text": "x"}]))
    from core.models import ItemRef
    opts = pipeline.RunOptions(out_root=".", no_cache=True, lang="ja")
    monkeypatch.setattr(pipeline, "_item_out_dir", lambda *a, **k: "out")
    monkeypatch.setattr(pipeline.artifacts, "write_item_artifacts", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.artifacts, "write_recheck_report", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.artifacts, "build_info", lambda meta, ref, **k: {"kind": "timeline", **k})
    pipeline.process_item(Adapter(), ItemRef(platform="demo", item_id="1", duration=60), ".", opts, single=True)
    assert captured["language"] == "ja"


def test_whisper_low_tier_weak_quality_hint(tmp_path, monkeypatch, capsys):
    """低档 whisper + 质量弱 → 强制推荐：content_info.quality_hint + stderr 提示"""
    import transcriber
    from core.models import ContentResult

    def fake_transcribe(url, model_size="medium", **kw):
        if kw.get("stats") is not None:
            kw["stats"].update({"avg_logprob": -1.5, "language_probability": 0.2,
                                "segments": 3, "no_speech_ratio": 0.1})
        return [{"start": 0.0, "end": 1.0, "text": "你好"}]

    monkeypatch.setattr(transcriber, "transcribe_url", fake_transcribe)
    a = FakeAdapter()
    a.content = ContentResult(kind="timeline", status="empty", provenance="none", segments=[])
    out = str(tmp_path / "out")
    opts = RunOptions(out_root=out, model="small")
    status, src, n, out_dir, err = process_item(a, _ref(), out, opts, single=True)
    assert src == "whisper"
    info = json.load(open(os.path.join(out_dir, "content_info.json"), encoding="utf-8"))
    assert info.get("quality_hint") and info["quality_hint"]["level"] == "weak"
    assert info.get("quality", {}).get("avg_logprob") == -1.5
    err_txt = capsys.readouterr().err
    assert "medium" in err_txt


def test_whisper_medium_weak_no_forced_hint(tmp_path, monkeypatch, capsys):
    """已达 medium 档：即使质量弱也不产生升级推荐（无可升档）"""
    import transcriber
    from core.models import ContentResult

    def fake_transcribe(url, model_size="medium", **kw):
        if kw.get("stats") is not None:
            kw["stats"].update({"avg_logprob": -1.5, "language_probability": 0.2,
                                "segments": 3, "no_speech_ratio": 0.1})
        return [{"start": 0.0, "end": 1.0, "text": "你好"}]

    monkeypatch.setattr(transcriber, "transcribe_url", fake_transcribe)
    a = FakeAdapter()
    a.content = ContentResult(kind="timeline", status="empty", provenance="none", segments=[])
    out = str(tmp_path / "out")
    opts = RunOptions(out_root=out, model="medium")
    status, src, n, out_dir, err = process_item(a, _ref(), out, opts, single=True)
    assert src == "whisper"
    info = json.load(open(os.path.join(out_dir, "content_info.json"), encoding="utf-8"))
    assert not info.get("quality_hint")


def test_lowdep_no_whisper_degrades_to_subtitle(tmp_path, monkeypatch, capsys):
    """低配（无 faster-whisper）+ 字幕候选 → 降级采信 + 标注 + quality_hint（不失败）"""
    import transcriber
    from core.models import ContentResult
    monkeypatch.setattr(transcriber, "whisper_available", lambda: False)
    a = FakeAdapter()
    a.content = ContentResult(kind="timeline", status="ok", provenance="none",
                              segments=[{"start": 0.0, "end": 1.0, "text": "低配字幕"}])
    out = str(tmp_path / "out")
    status, src, n, out_dir, err = process_item(a, _ref(), out, RunOptions(out_root=out), single=True)
    assert status == "ok"
    assert "低配" in src and "未经同源校验" in src
    info = json.load(open(os.path.join(out_dir, "content_info.json"), encoding="utf-8"))
    assert info["quality_hint"]["level"] == "lowdep"
    assert "faster-whisper" in json.dumps(info["quality_hint"], ensure_ascii=False)


def test_lowdep_no_whisper_no_subtitle_fails_with_guidance(tmp_path, monkeypatch):
    """低配（无 faster-whisper）+ 无字幕 → whisper_missing（CLI 层给出安装引导）"""
    import transcriber
    from core.models import ContentResult
    monkeypatch.setattr(transcriber, "whisper_available", lambda: False)
    a = FakeAdapter()
    a.content = ContentResult(kind="timeline", status="empty", provenance="none", segments=[])
    out = str(tmp_path / "out")
    status, src, n, out_dir, err = process_item(a, _ref(), out, RunOptions(out_root=out), single=True)
    assert status == "whisper_missing"


def test_whisper_small_ok_tier_note(tmp_path, monkeypatch, capsys):
    """低档但质量正常 → 产物带 tier_note（常驻建议），无强制 quality_hint"""
    import transcriber
    from core.models import ContentResult

    def fake_transcribe(url, model_size="medium", **kw):
        if kw.get("stats") is not None:
            kw["stats"].update({"avg_logprob": -0.2, "language_probability": 1.0,
                                "segments": 100, "no_speech_ratio": 0.01})
        return [{"start": 0.0, "end": 60.0, "text": "字" * 600}]

    monkeypatch.setattr(transcriber, "transcribe_url", fake_transcribe)
    a = FakeAdapter()
    a.content = ContentResult(kind="timeline", status="empty", provenance="none", segments=[])
    out = str(tmp_path / "out")
    opts = RunOptions(out_root=out, model="small")
    status, src, n, out_dir, err = process_item(a, _ref(), out, opts, single=True)
    assert src == "whisper"
    info = json.load(open(os.path.join(out_dir, "content_info.json"), encoding="utf-8"))
    assert info["quality"]["tier_note"] and "medium" in info["quality"]["tier_note"]
    assert not info.get("quality_hint")
