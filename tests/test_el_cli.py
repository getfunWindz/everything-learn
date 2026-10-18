import sys, os, json, argparse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
import el
from core import pipeline


@pytest.fixture(autouse=True)
def _same_source_pass(monkeypatch):
    """M13/T12：本文件聚焦 CLI 管线，字幕默认视为通过同源校验（避免真实下载/Whisper）；
    来源策略本身由 tests/test_core_pipeline_recheck.py 覆盖，专项测试可自行覆盖此 patch。"""
    monkeypatch.setattr(pipeline, "_same_source_verify",
                        lambda *a, **k: {"checked": True, "passed": True, "avg": 1.0})


@pytest.fixture(autouse=True)
def _fast_media_download(monkeypatch):
    """媒体下载在 CLI 测试中快速失败（fake URL 经环境代理会得到 502，重试退避拖慢套件）；
    生产重试/续传逻辑由 tests/test_transcriber.py 覆盖，专项测试可自行覆盖此 patch。"""
    import transcriber

    def _boom(*a, **k):
        raise transcriber.requests.exceptions.ConnectionError("下载失败（测试环境 mock）")

    monkeypatch.setattr(transcriber, "download_audio", _boom)


VIEW = {"code": 0, "data": {
    "bvid": "BV1GJ411x7h7", "aid": 170001, "title": "测试视频",
    "owner": {"name": "测试UP"}, "duration": 300, "pubdate": 1600000000,
    "pages": [{"cid": 1001, "page": 1, "part": "P1 基础", "duration": 100}]}}

DEFAULT_LINES = [
    {"start": 0.0, "end": 2.5, "text": "大家好"}, {"start": 2.5, "end": 5.0, "text": "这是测试"}]

class FakeClient:
    def __init__(self, lines="default"):
        self.lines = DEFAULT_LINES if lines == "default" else lines
        self.search_out = []
    def get_video_info(self, bvid="", aid=0):
        assert bvid or aid
        from providers.bilibili import VideoInfo
        return VideoInfo(VIEW["data"])
    def fetch_subtitle(self, bvid, cid, duration=None, lang=None):
        from providers.bilibili import SubtitleLine
        if self.lines is None:
            return [], "none"
        return ([SubtitleLine(l["start"], l["end"], l["text"]) for l in self.lines], "human")
    def get_audio_url(self, bvid, cid):
        return "http://fake/audio"
    def get_video_url(self, bvid, cid, lowest=False):
        return "http://fake/video"
    def search(self, keyword, limit=5):
        return self.search_out

def report_dir(out):
    """cmd_run 输出在 out/<UP主>/<标题>[ P{n}] 子目录，取唯一路径"""
    ups = [d for d in os.listdir(out) if os.path.isdir(os.path.join(out, d))]
    assert len(ups) == 1, f"期望唯一 UP主 目录，实际 {ups}"
    sub = [d for d in os.listdir(os.path.join(out, ups[0]))
           if os.path.isdir(os.path.join(out, ups[0], d))]
    assert len(sub) == 1, f"期望唯一视频目录，实际 {sub}"
    return os.path.join(out, ups[0], sub[0])

def test_run_with_subtitle(tmp_path):
    out = tmp_path / "out"
    client = FakeClient()
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                    no_whisper=False, pick=1), client=client)
    rd = report_dir(out)
    files = sorted(os.listdir(rd))
    assert "content.txt" in files and "content_info.json" in files and "report_template.md" in files
    info = json.load(open(os.path.join(rd, "content_info.json"), encoding="utf-8"))
    assert info["title"] == "测试视频" and info["page"] == "1" and info["source"] == "字幕"
    sub = open(os.path.join(rd, "content.txt"), encoding="utf-8").read()
    assert "大家好" in sub
    tpl = open(os.path.join(rd, "report_template.md"), encoding="utf-8").read()
    assert "## 知识详解" in tpl and "测试视频" in tpl
    assert "## 原话摘录" in tpl  # 学习报告要求：附上有参考价值的视频原话

def test_run_no_subtitle_no_whisper_flag(tmp_path):
    out = tmp_path / "out"
    client = FakeClient(lines=None)
    with pytest.raises(SystemExit) as e:
        el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                        no_whisper=True, pick=1), client=client)
    assert e.value.code == 3

def test_run_whisper_not_installed_guidance(tmp_path, monkeypatch):
    import transcriber
    def boom(*a, **k):
        raise transcriber.WhisperNotInstalled("未安装 faster-whisper，请执行: pip install faster-whisper")
    monkeypatch.setattr(transcriber, "transcribe_url", boom)
    out = tmp_path / "out"
    client = FakeClient(lines=None)
    with pytest.raises(SystemExit) as e:
        el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                        no_whisper=False, pick=1), client=client)
    assert e.value.code == 5

def test_run_keyword_search_pick(tmp_path):
    out = tmp_path / "out"
    client = FakeClient()
    client.search_out = [{"bvid": "BV1GJ411x7h7", "title": "测试视频", "author": "测试UP", "duration": "5:00"}]
    el.cmd_run(argparse.Namespace(input="随便一个关键词", page=1, out=str(out),
                                    no_whisper=False, pick=1), client=client)
    rd = report_dir(out)
    info = json.load(open(os.path.join(rd, "content_info.json"), encoding="utf-8"))
    assert info["item_id"] == "BV1GJ411x7h7"

def test_resolve_cmd(capsys):
    el.cmd_resolve(argparse.Namespace(input="https://www.biliel.com/video/BV1GJ411x7h7/?p=2"))
    out = json.loads(capsys.readouterr().out)
    assert out["bvid"] == "BV1GJ411x7h7" and out["page"] == 2

VIEW3 = {"code": 0, "data": {
    "bvid": "BV1GJ411x7h7", "aid": 170001, "title": "测试合集",
    "owner": {"name": "测试UP"}, "duration": 300, "pubdate": 1600000000,
    "pages": [
        {"cid": 1001, "page": 1, "part": "P1 基础", "duration": 100},
        {"cid": 1002, "page": 2, "part": "P2 进阶", "duration": 100},
        {"cid": 1003, "page": 3, "part": "P3 实战", "duration": 100},
    ]}}

class MultiPageClient:
    """按 cid 返回不同 P 的字幕；lines_map: {cid: [SubtitleLine...] | None}"""
    def __init__(self, lines_map, view=VIEW3):
        self.lines_map = lines_map
        self.view = view
    def get_video_info(self, bvid="", aid=0):
        from providers.bilibili import VideoInfo
        return VideoInfo(self.view["data"])
    def fetch_subtitle(self, bvid, cid, duration=None, lang=None):
        from providers.bilibili import SubtitleLine
        raw = self.lines_map.get(cid)
        if raw is None:
            return [], "none"
        return ([SubtitleLine(l["start"], l["end"], l["text"]) for l in raw], "human")
    def get_audio_url(self, bvid, cid):
        return "http://fake/audio"
    def get_video_url(self, bvid, cid, lowest=False):
        return "http://fake/video"
    def search(self, keyword, limit=5):
        return []

def _mk_lines(texts):
    return [{"start": i * 1.0, "end": i * 1.0 + 0.8, "text": t} for i, t in enumerate(texts)]

def _batch_dir(out):
    ups = [d for d in os.listdir(out) if os.path.isdir(os.path.join(out, d))]
    assert len(ups) == 1, f"期望唯一 UP主 目录，实际 {ups}"
    d = [x for x in os.listdir(os.path.join(out, ups[0])) if x.endswith(" 合集")]
    assert len(d) == 1, f"期望唯一 合集 目录，实际 {d}"
    return os.path.join(out, ups[0], d[0])

def test_batch_all_structure(tmp_path):
    out = tmp_path / "out"
    client = MultiPageClient({
        1001: _mk_lines([f"第一课{i}" for i in range(12)]),
        1002: _mk_lines([f"第二课{i}" for i in range(12)]),
        1003: _mk_lines([f"第三课{i}" for i in range(12)]),
    })
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=None, pages=None,
                                    all_=True, out=str(out), no_whisper=False, pick=1),
                 client=client)
    sub = _batch_dir(out)
    for p in ("P01", "P02", "P03"):
        assert os.path.isdir(os.path.join(sub, p)), f"缺少 {p}"
        assert os.path.exists(os.path.join(sub, p, "content.txt"))
    info = json.load(open(os.path.join(sub, "content_info.json"), encoding="utf-8"))
    assert info["page_count"] == 3
    assert [r["status"] for r in info["pages"]] == ["ok", "ok", "ok"]
    assert info["pages"][0]["line_count"] == 12

def test_batch_partial_failure_continues(tmp_path):
    out = tmp_path / "out"
    client = MultiPageClient({
        1001: _mk_lines([f"第一课{i}" for i in range(12)]),
        1002: None,   # 无字幕且 --no-whisper → no_subtitle
        1003: _mk_lines([f"第三课{i}" for i in range(12)]),
    })
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=None, pages=None,
                                    all_=True, out=str(out), no_whisper=True, pick=1),
                 client=client)
    sub = _batch_dir(out)
    info = json.load(open(os.path.join(sub, "content_info.json"), encoding="utf-8"))
    assert [r["status"] for r in info["pages"]] == ["ok", "no_subtitle", "ok"]

def test_batch_pages_range(tmp_path):
    out = tmp_path / "out"
    client = MultiPageClient({
        1001: _mk_lines([f"第一课{i}" for i in range(12)]),
        1002: _mk_lines([f"第二课{i}" for i in range(12)]),
        1003: _mk_lines([f"第三课{i}" for i in range(12)]),
    })
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=None, pages="2-3",
                                    all_=False, out=str(out), no_whisper=False, pick=1),
                 client=client)
    sub = _batch_dir(out)
    assert os.path.isdir(os.path.join(sub, "P02"))
    assert os.path.isdir(os.path.join(sub, "P03"))
    assert not os.path.isdir(os.path.join(sub, "P01"))

def test_batch_failure_records_error(tmp_path):
    out = tmp_path / "out"
    client = MultiPageClient({
        1001: None,
        1002: _mk_lines([f"第二课{i}" for i in range(12)]),
    })
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=None, pages="1-2",
                                    all_=False, out=str(out), no_whisper=True, pick=1),
                 client=client)
    sub = _batch_dir(out)
    info = json.load(open(os.path.join(sub, "content_info.json"), encoding="utf-8"))
    p1 = info["pages"][0]
    assert p1["status"] == "no_subtitle"
    assert "error" in p1 and p1["error"]  # 失败原因非空

def test_resume_skips_done_pages(tmp_path):
    out = tmp_path / "out"
    # 上次已成功 P1，这次只处理 P2、P3
    base = os.path.join(str(out), "测试UP", "测试合集 合集")
    os.makedirs(base, exist_ok=True)
    json.dump({"title": "测试合集", "platform": "bilibili", "item_id": "BV1GJ411x7h7",
               "pages": [{"page": 1, "status": "ok"}, {"page": 2, "status": "failed"}]},
              open(os.path.join(base, "content_info.json"), "w", encoding="utf-8"))
    processed = []
    client = MultiPageClient({
        1001: _mk_lines([f"第一课{i}" for i in range(12)]),
        1002: _mk_lines([f"第二课{i}" for i in range(12)]),
        1003: _mk_lines([f"第三课{i}" for i in range(12)]),
    })
    orig = pipeline.process_item
    def spy(adapter, ref, root_, opts, single=False):
        processed.append(ref.sub_id)
        return orig(adapter, ref, root_, opts, single=single)
    pipeline.process_item = spy
    try:
        el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=None, pages=None,
                                        all_=True, out=str(out), no_whisper=False,
                                        pick=1, resume=True), client=client)
    finally:
        pipeline.process_item = orig
    assert processed == ["2", "3"]  # P1 已 ok 被跳过；P2 failed 重试

def test_batch_shows_progress_and_eta(tmp_path, capsys):
    out = tmp_path / "out"
    client = MultiPageClient({
        1001: _mk_lines([f"第一课{i}" for i in range(12)]),
        1002: _mk_lines([f"第二课{i}" for i in range(12)]),
        1003: _mk_lines([f"第三课{i}" for i in range(12)]),
    })
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=None, pages=None,
                                    all_=True, out=str(out), no_whisper=False,
                                    pick=1, resume=False), client=client)
    out_text = capsys.readouterr().out
    assert "预计剩余" in out_text

def test_batch_generates_report_skeleton(tmp_path):
    out = tmp_path / "out"
    client = MultiPageClient({
        1001: _mk_lines([f"第一课{i}" for i in range(12)]),
        1002: _mk_lines([f"第二课{i}" for i in range(12)]),
    })
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=None, pages=None,
                                    all_=True, out=str(out), no_whisper=False,
                                    pick=1, resume=False), client=client)
    sub = _batch_dir(out)
    tpl = open(os.path.join(sub, "report_template.md"), encoding="utf-8").read()
    assert "## P1" in tpl and "## P2" in tpl        # 按 P 分章
    assert "P1 基础" in tpl and "P2 进阶" in tpl      # 引用分P标题

def test_report_command_regenerates_single(tmp_path):
    out = tmp_path / "out"
    client = FakeClient()
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                    no_whisper=False, pick=1), client=client)
    single = report_dir(out)
    os.remove(os.path.join(single, "report_template.md"))  # 模拟模板丢失
    el.cmd_report(argparse.Namespace(target=single))
    assert os.path.exists(os.path.join(single, "report_template.md"))

def test_report_command_handles_batch_dir(tmp_path):
    out = tmp_path / "out"
    client = MultiPageClient({
        1001: _mk_lines([f"第一课{i}" for i in range(12)]),
        1002: _mk_lines([f"第二课{i}" for i in range(12)]),
    })
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=None, pages=None,
                                    all_=True, out=str(out), no_whisper=False,
                                    pick=1, resume=False), client=client)
    sub = _batch_dir(out)
    el.cmd_report(argparse.Namespace(target=sub))
    tpl = open(os.path.join(sub, "report_template.md"), encoding="utf-8").read()
    assert "## P1" in tpl

def test_run_log_file_created(tmp_path):
    import logging
    out = tmp_path / "out"
    client = FakeClient()
    el.setup_logging(str(tmp_path / "logs"))
    try:
        el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                        no_whisper=False, pick=1), client=client)
    finally:
        logging.shutdown()
    logfile = tmp_path / "logs" / "el.log"
    assert logfile.exists()
    content = open(logfile, encoding="utf-8").read()
    assert "BV1GJ411x7h7" in content

def test_batch_writes_progress_incrementally(tmp_path):
    """批量中断时汇总已含已完成 P（resume 依赖）"""
    out = tmp_path / "out"
    client = MultiPageClient({
        1001: _mk_lines([f"第一课{i}" for i in range(12)]),
        1002: _mk_lines([f"第二课{i}" for i in range(12)]),
        1003: _mk_lines([f"第三课{i}" for i in range(12)]),
    })
    orig = pipeline.process_item
    calls = {"n": 0}
    def boom(adapter, ref, root_, opts, single=False):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt()  # 模拟中断
        return orig(adapter, ref, root_, opts, single=single)
    pipeline.process_item = boom
    try:
        with pytest.raises(KeyboardInterrupt):
            el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=None, pages=None,
                                            all_=True, out=str(out), no_whisper=False,
                                            pick=1, resume=False), client=client)
    finally:
        el._process_page = orig
    sub = _batch_dir(out)
    info = json.load(open(os.path.join(sub, "content_info.json"), encoding="utf-8"))
    assert [r["status"] for r in info["pages"]] == ["ok"]  # P1 已记录，P2 中断

def test_config_out_dir_used_when_no_out_arg(tmp_path, monkeypatch):
    from core import config as cfg
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"out_dir": str(tmp_path / "cfg_out")}), encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(p))
    client = FakeClient()
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=None,
                                    no_whisper=False, pick=1), client=client)
    rd = report_dir(tmp_path / "cfg_out")
    assert os.path.exists(os.path.join(rd, "content.txt"))

def test_output_archived_by_owner(tmp_path):
    """B3：输出按 UP 主归档为 <out>/<UP主>/<日期>_<标题>/"""
    out = tmp_path / "out"
    client = FakeClient()
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                    no_whisper=False, pick=1), client=client)
    up_dir = out / "测试UP"
    assert up_dir.is_dir()
    sub = [d for d in os.listdir(up_dir) if os.path.isdir(os.path.join(up_dir, d))][0]
    assert os.path.exists(up_dir / sub / "content.txt")

def test_export_command(tmp_path):
    pytest.importorskip("docx")
    out = tmp_path / "out"
    client = FakeClient()
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                    no_whisper=False, pick=1), client=client)
    rd = report_dir(out)
    el.cmd_export(argparse.Namespace(target=rd, format="html", out=None))
    htmls = [f for f in os.listdir(rd) if f.endswith(".html")]
    assert htmls and any("学习报告" in f for f in htmls)
    el.cmd_export(argparse.Namespace(target=rd, format="docx", out=None))
    docs = [f for f in os.listdir(rd) if f.endswith(".docx")]
    assert docs and any("学习报告" in f for f in docs)

def test_model_param_priority_over_config(tmp_path, monkeypatch):
    """--model CLI 参数优先于 config.whisper_model"""
    from core import config as cfg
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"whisper_model": "medium"}), encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(p))
    import transcriber
    seen = []
    # transcribe_url 是平台无关入口（原 transcribe_video 已薄封装）
    def spy(url, model_size="medium", progress_callback=None, **kw):
        seen.append(model_size)
        return [{"start": 0.0, "end": 1.0, "text": "x"}]
    monkeypatch.setattr(transcriber, "transcribe_url", spy)
    out = tmp_path / "out"
    client = FakeClient(lines=None)  # 无字幕 → 走 whisper
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                    no_whisper=False, pick=1, lang=None,
                                    model="small"), client=client)
    assert seen == ["small"]  # CLI 覆盖 config 的 medium

def test_invalid_pages_graceful(tmp_path, capsys):
    """非法页码范围应友好报错退出，而非 Traceback 崩溃"""
    client = MultiPageClient({})
    with pytest.raises(SystemExit) as e:
        el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=None, pages="5-2",
                                        all_=False, out=str(tmp_path / "o"), no_whisper=True,
                                        pick=1, resume=False, lang=None, model=None),
                     client=client)
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "无效" in err and "Traceback" not in err

def test_no_vision_check_flag_reaches_opts(tmp_path, monkeypatch):
    """兼容开关：--no-vision-check 仍被接受并透传（旧 _vision_check 原语已退役）"""
    captured = _capture_run_opts(tmp_path, monkeypatch)
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(tmp_path / "out"),
                                  no_whisper=False, pick=1, no_cache=False, interval=1.5,
                                  no_vision_check=True), client=object())
    assert captured["opts"].no_vision_check is True

def test_whisper_empty_transcript_fails(tmp_path, monkeypatch):
    """M13/T12：无字幕候选且 Whisper 返回空 → failed（旧 vision 纯视觉兜底已退役）"""
    import transcriber as tr_mod
    monkeypatch.setattr(tr_mod, "transcribe_url", lambda *a, **k: [])
    out = tmp_path / "out"
    client = FakeClient(lines=None)
    with pytest.raises(SystemExit) as e:
        el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                        no_whisper=False, pick=1, lang=None, model=None),
                     client=client)
    assert e.value.code == 4

def test_frames_command_saves_jpegs(tmp_path, monkeypatch):
    """frames 命令：抽帧保存 JPEG 文件（供多模态 agent 用 read 工具读图复检）"""
    import vision as vision_mod
    import transcriber as tr_mod
    monkeypatch.setattr(vision_mod, "extract_frames_range",
                        lambda p, s, e, fps=1, max_frames=30:
                            [(0, b"\xff\xd8a"), (1, b"\xff\xd8b")])
    monkeypatch.setattr(tr_mod, "download_audio", lambda url, dest: dest)
    class FC(FakeClient):
        def get_video_url(self, b, c): return "http://v"
    out = tmp_path / "frames"
    el.cmd_frames(argparse.Namespace(input="BV1GJ411x7h7", range="0-10",
                                       out=str(out), fps=1, max=30),
                    client=FC())
    files = sorted(os.listdir(out))
    assert len(files) == 2
    assert any(f.startswith("frame_0s") for f in files)

def test_page_and_pages_mutually_exclusive():
    with pytest.raises(SystemExit):
        el.main(["run", "BV1GJ411x7h7", "--page", "2", "--all"])


def test_vad_flags_mutually_exclusive():
    """--vad 与 --no-vad 互斥（argparse 拒绝同时传入）"""
    with pytest.raises(SystemExit):
        el.main(["run", "BV1GJ411x7h7", "--vad", "--no-vad"])


def test_unknown_url_skips_bilibili_cookie_check(tmp_path, monkeypatch, capsys):
    """未知平台 URL 不应先弹 B 站 cookie 警告；应直接给自进化提示"""
    from core import config as cfg
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(el, "setup_logging", lambda *a, **k: "")
    calls = []

    class Fake:
        def __init__(self, *a, **k):
            pass
        def check_login(self):
            calls.append(1)
            return False

    def boom(*a, **k):
        raise el.UnsupportedUrlError("未知平台")

    monkeypatch.setattr(el, "ApiClient", Fake)
    monkeypatch.setattr(el.pipeline, "run_input", boom)
    with pytest.raises(SystemExit) as e:
        el.main(["run", "https://example.com/x", "--out", str(tmp_path / "out")])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert calls == []
    assert "cookie" not in err.lower()


def test_run_recheck_args_parsed(monkeypatch):
    """T17：run 解析 --recheck/--budget/--yes-recheck → args 值正确"""
    captured = {}
    monkeypatch.setattr(el, "cmd_run", lambda args: captured.update(vars(args)))
    el.main(["run", "BV1GJ411x7h7", "--recheck", "2", "--budget", "1.5", "--yes-recheck"])
    assert captured["recheck"] == "2"
    assert captured["budget"] == 1.5
    assert captured["yes_recheck"] is True
    assert captured["no_recheck"] is False


def test_run_recheck_3_parsed_and_maps_none(monkeypatch):
    """Q6：CLI --recheck 3 解析为 '3'，且 decide_tier 映射为 tier 3（不复检）"""
    captured = {}
    monkeypatch.setattr(el, "cmd_run", lambda args: captured.update(vars(args)))
    el.main(["run", "BV1GJ411x7h7", "--recheck", "3"])
    assert captured["recheck"] == "3"
    from core import recheck as rc_mod
    sig = {"dur_min": 1.0, "kp_density": 5.0, "kp_spread": 0.2, "src_risk": 1.0,
           "vis_ref": 2.0, "gap_ratio": 0.1, "max_gap": 5.0}
    d = rc_mod.decide_tier(sig, {"recheck": {"tier_policy": "auto"}},
                           provenance="auto", has_video=True,
                           policy=captured["recheck"])
    assert d["tier"] == 3


def _capture_run_opts(tmp_path, monkeypatch):
    """运行 cmd_run（mock pipeline.run_input）并返回捕获容器（含 RunOptions）"""
    captured = {}

    def fake_run(inp, opts, adapter=None, client=None):
        captured["opts"] = opts
        return "ok", "正文", 1, str(tmp_path / "out"), ""

    monkeypatch.setattr(el.pipeline, "run_input", fake_run)
    return captured


def test_run_no_recheck_maps_off(tmp_path, monkeypatch):
    """T17：--no-recheck 映射为 opts.recheck=='off'（显式豁免）"""
    captured = _capture_run_opts(tmp_path, monkeypatch)
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(tmp_path / "out"),
                                  no_whisper=False, pick=1, no_cache=False, interval=1.5,
                                  no_recheck=True), client=object())
    assert captured["opts"].recheck == "off"


def test_run_recheck_budget_yes_passed(tmp_path, monkeypatch):
    """T17：--recheck/--budget/--yes-recheck 透传进 RunOptions"""
    captured = _capture_run_opts(tmp_path, monkeypatch)
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(tmp_path / "out"),
                                  no_whisper=False, pick=1, no_cache=False, interval=1.5,
                                  recheck="2", budget=1.5, yes_recheck=True), client=object())
    assert captured["opts"].recheck == "2"
    assert captured["opts"].budget == 1.5
    assert captured["opts"].yes_recheck is True


def test_run_recheck_defaults_none(tmp_path, monkeypatch):
    """T17：默认（不传）→ opts.recheck is None / budget None / yes_recheck False"""
    captured = _capture_run_opts(tmp_path, monkeypatch)
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(tmp_path / "out"),
                                  no_whisper=False, pick=1, no_cache=False, interval=1.5),
               client=object())
    assert captured["opts"].recheck is None
    assert captured["opts"].budget is None
    assert captured["opts"].yes_recheck is False


# ---------- 字幕同源校验接线（候选过校验 → 采信；未过 → 强制 Whisper） ----------


def test_subtitle_candidate_kept_when_same_source_passes(tmp_path, monkeypatch):
    """M13/T12：旧 status=suspect 分级退役；字幕候选过同源校验即采信，不调用 Whisper"""
    import transcriber as tr_mod
    monkeypatch.setattr(tr_mod, "transcribe_url",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应调用 Whisper")))
    out = tmp_path / "out"
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                    no_whisper=False, pick=1, lang=None, model=None),
                 client=FakeClient())
    rd = report_dir(out)
    info = json.load(open(os.path.join(rd, "content_info.json"), encoding="utf-8"))
    assert info["source"] == "字幕"


def test_failed_same_source_forces_whisper(tmp_path, monkeypatch):
    """M13/T12：同源校验未过（原 vision 判不一致场景）→ 强制 Whisper"""
    import transcriber as tr_mod
    monkeypatch.setattr(tr_mod, "transcribe_url",
                        lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "whisper 转写"}])
    monkeypatch.setattr(pipeline, "_same_source_verify",
                        lambda *a, **k: {"checked": False, "passed": False,
                                         "reason_code": "error", "reason": "串台"})
    out = tmp_path / "out"
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                    no_whisper=False, pick=1, lang=None, model=None),
                 client=FakeClient())
    rd = report_dir(out)
    info = json.load(open(os.path.join(rd, "content_info.json"), encoding="utf-8"))
    assert info["source"] == "whisper"


def test_no_candidate_skips_same_source(tmp_path, monkeypatch):
    """M13/T12：无字幕候选 → 不做同源校验，直接 Whisper"""
    called = {"n": 0}
    def spy(*a, **k):
        called["n"] += 1
        return {"checked": True, "passed": True}
    import transcriber
    monkeypatch.setattr(transcriber, "transcribe_url",
                        lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "w"}])
    monkeypatch.setattr(pipeline, "_same_source_verify", spy)
    out = tmp_path / "out"
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                    no_whisper=False, pick=1, lang=None, model=None),
                 client=FakeClient(lines=None))
    assert called["n"] == 0
    rd = report_dir(out)
    info = json.load(open(os.path.join(rd, "content_info.json"), encoding="utf-8"))
    assert info["source"] == "whisper"


class AutoSubtitleClient(FakeClient):
    """AI 字幕候选（provenance=auto）"""
    def fetch_subtitle(self, bvid, cid, duration=None, lang=None):
        from providers.bilibili import SubtitleLine
        return ([SubtitleLine(l["start"], l["end"], l["text"]) for l in self.lines], "auto")


def test_ai_subtitle_provenance_recorded(tmp_path):
    """M13 §10.1：auto 来源 + language=zh 落入 content_info（--no-whisper 采信 AI 字幕）"""
    out = tmp_path / "out"
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                  no_whisper=True, pick=1, lang=None, model=None),
               client=AutoSubtitleClient())
    rd = report_dir(out)
    info = json.load(open(os.path.join(rd, "content_info.json"), encoding="utf-8"))
    assert info["provenance"] == "auto" and info["language"] == "zh"
    assert info["source"].startswith("字幕(AI)")


def test_human_subtitle_provenance_recorded(tmp_path):
    """人工字幕过同源校验 → provenance=human 落入 content_info"""
    out = tmp_path / "out"
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                  no_whisper=False, pick=1, lang=None, model=None),
               client=FakeClient())
    rd = report_dir(out)
    info = json.load(open(os.path.join(rd, "content_info.json"), encoding="utf-8"))
    assert info["provenance"] == "human" and info["language"] == "zh"
    assert info["source"] == "字幕"


# ---------- VAD / medium 默认（Task 5） ----------

def test_vad_flag_reaches_transcriber(tmp_path, monkeypatch):
    """--vad 手动开启：透传到 transcriber（长视频默认关闭）"""
    from core import config as cfg
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "none.json"))
    import transcriber as tr_mod
    seen = {}
    def spy(url, **kw):
        seen.update(kw)
        return [{"start": 0.0, "end": 1.0, "text": "w"}]
    monkeypatch.setattr(tr_mod, "transcribe_url", spy)
    out = tmp_path / "out"
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                    no_whisper=False, pick=1, lang=None, model=None, vad=True),
                 client=FakeClient(lines=None))
    assert seen["vad"] is True
    assert seen["model_size"] == "small"   # 出厂默认 fast 档（2026-10-17）
    assert seen["duration_sec"] == 100

def test_whisper_default_model_small(tmp_path, monkeypatch):
    """无 --model 时用出厂默认 fast 档 small（首次引导建议升 medium）"""
    from core import config as cfg
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "none.json"))
    import transcriber as tr_mod
    seen = {}
    def spy(url, **kw):
        seen.update(kw)
        return [{"start": 0.0, "end": 1.0, "text": "w"}]
    monkeypatch.setattr(tr_mod, "transcribe_url", spy)
    out = tmp_path / "out"
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                    no_whisper=False, pick=1, lang=None, model=None),
                 client=FakeClient(lines=None))
    assert seen["model_size"] == "small"
    assert seen["vad"] is None  # config 默认 auto → 由 transcriber 按视频时长决策


def test_cookie_command_auto_delegates(tmp_path, monkeypatch):
    """el.py cookie <p> --auto → 先 headless 复用 profile"""
    import cookie_export
    from core import config as cfg
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "config.json"))
    calls = {}
    monkeypatch.setattr(cookie_export, "auto_export",
                        lambda platform, browser, interactive, timeout: calls.update(
                            platform=platform, browser=browser, interactive=interactive,
                            timeout=timeout) or True)
    try:
        el.main(["cookie", "zhihu", "--auto", "--timeout", "12"])
        assert False
    except SystemExit as e:
        assert e.code == 0
    assert calls == {"platform": "zhihu", "browser": "auto", "interactive": True, "timeout": 12}


def test_cookie_command_delegates(tmp_path, monkeypatch):
    """el.py cookie <platform> → 委托 cookie_export.export（--browser/--timeout 透传）"""
    import cookie_export
    from core import config as cfg
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "config.json"))
    calls = {}
    monkeypatch.setattr(cookie_export, "export",
                        lambda platform, browser, timeout: calls.update(
                            platform=platform, browser=browser, timeout=timeout) or 0)
    try:
        el.main(["cookie", "zhihu", "--timeout", "12"])
        assert False
    except SystemExit as e:
        assert e.code == 0
    assert calls == {"platform": "zhihu", "browser": "auto", "timeout": 12}


def test_cookie_command_list(capsys):
    """el.py cookie --list → 列出 provider 声明的平台（标配可查）"""
    try:
        el.main(["cookie", "--list"])
        assert False
    except SystemExit as e:
        assert e.code == 0
    out = capsys.readouterr().out
    assert "zhihu" in out and "bilibili" in out


class _FakeWx:
    def search_articles(self, q, limit=5):
        return [{"title": "标题A", "account": "账号A", "date": "2026-08-16",
                 "link": "https://weixin.sogou.com/link?url=x", "snippet": ""},
                {"title": "标题B", "account": "账号B", "date": "2021-09-06",
                 "link": "https://weixin.sogou.com/link?url=y", "snippet": ""}]

    def resolve_sogou_link(self, link):
        return "https://mp.weixin.qq.com/s/AbCdEf1234567890_-abc"


def test_search_wechat_platform(monkeypatch, capsys):
    monkeypatch.setattr(el, "_wechat_adapter", lambda: _FakeWx())
    el.cmd_search(argparse.Namespace(keyword="深度学习", limit=5, platform="wechat"))
    out = capsys.readouterr()
    assert "标题A" in out.out and "账号A" in out.out


def test_run_wechat_platform_pick(tmp_path, monkeypatch):
    monkeypatch.setattr(el, "_wechat_adapter", lambda: _FakeWx())
    calls = {}

    def fake_run(inp, opts, adapter=None, client=None):
        calls["input"] = inp
        calls["adapter"] = adapter
        return "ok", "正文", 3, str(tmp_path / "out"), ""

    monkeypatch.setattr(el.pipeline, "run_input", fake_run)
    el.cmd_run(argparse.Namespace(input="深度学习", platform="wechat", pick=2,
                                  out=str(tmp_path / "out"), no_whisper=False,
                                  no_cache=False, interval=1.5), client=object())
    assert calls["input"] == "https://mp.weixin.qq.com/s/AbCdEf1234567890_-abc"
    assert isinstance(calls["adapter"], _FakeWx)


def test_run_wechat_no_bilibili_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(el, "_wechat_adapter", lambda: _FakeWx())

    def raise_search(inp, opts, adapter=None, client=None):
        from core.adapter import SearchNeeded
        raise SearchNeeded(inp)

    monkeypatch.setattr(el.pipeline, "run_input", raise_search)
    with pytest.raises(SystemExit) as e:
        el.cmd_run(argparse.Namespace(input="深度学习", platform="wechat", pick=1,
                                      out=str(tmp_path / "out"), no_whisper=False,
                                      no_cache=False, interval=1.5), client=object())
    assert e.value.code == 4


def test_run_url_skips_bili_check_for_non_bili(tmp_path, monkeypatch):
    """非 B 站适配器 URL 运行时不应触发 B 站登录检查（避免误报 cookie 警告）"""
    calls = {"login": 0}

    class C:
        def check_login(self):
            calls["login"] += 1
            return True

    monkeypatch.setattr(el.pipeline, "run_input",
                        lambda inp, opts, adapter=None, client=None:
                        ("ok", "正文", 1, str(tmp_path / "out"), ""))
    el.cmd_run(argparse.Namespace(input="https://x.com/NASA/status/2108563438901350773",
                                  pick=1, out=str(tmp_path / "out"), no_whisper=False,
                                  no_cache=False, interval=1.5), client=C())
    assert calls["login"] == 0
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", pick=1, out=str(tmp_path / "out"),
                                  no_whisper=False, no_cache=False, interval=1.5), client=C())
    assert calls["login"] == 1


def test_scan_command_lists_filtered_ranked(monkeypatch, capsys):
    class A:
        name = "fake"
        def resolve(self, q):
            from core.models import ItemRef
            return ItemRef(platform="fake", item_id="root", url=q, sub_id="1")
        def list_items(self, ref):
            from core.models import ItemRef
            return [ItemRef(platform="fake", item_id=f"i{i}", url=f"u{i}", sub_id=str(i),
                            title=t, extra={"view": v})
                    for i, (t, v) in enumerate([("GPT 教程", 100), ("音乐现场", 900),
                                                ("深度学习实战", 500)], 1)]
    monkeypatch.setattr(el, "_scan_adapter", lambda s: A())
    el.cmd_scan(argparse.Namespace(input="https://fake/list", filter=None, priority=True,
                                   limit=0, snapshot=False, out=None))
    out = capsys.readouterr().out
    assert "共 3 条，命中 2 条" in out
    assert out.index("深度学习实战") < out.index("GPT 教程")     # 优先级排序（500>100）


def test_scan_command_limit_and_non_url(monkeypatch, capsys):
    class A:
        name = "fake"
        def resolve(self, q):
            from core.models import ItemRef
            return ItemRef(platform="fake", item_id="r", url=q, sub_id="1")
        def list_items(self, ref):
            from core.models import ItemRef
            return [ItemRef(platform="fake", item_id=f"i{i}", url=f"u{i}", sub_id=str(i),
                            title="AI 内容", extra={}) for i in range(5)]
    monkeypatch.setattr(el, "_scan_adapter", lambda s: A())
    el.cmd_scan(argparse.Namespace(input="https://fake/list", filter=None, priority=False,
                                   limit=3, snapshot=False, out=None))
    assert "共 3 条" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        el.cmd_scan(argparse.Namespace(input="一个名称", filter=None, priority=False,
                                       limit=0, snapshot=False, out=None))
    assert e.value.code == 2


def test_toview_history_following_commands(monkeypatch, capsys):
    class C:
        def get_toview(self):
            return [{"bvid": "BV1", "title": "稍后看", "duration": 60, "owner": "UP"}]
        def get_history(self, limit=30):
            return [{"bvid": "BV2", "title": "历史", "duration": 60}]
        def get_followings(self, limit=50):
            return [{"mid": 1, "uname": "小明", "sign": "签名"}]
    el.cmd_toview(argparse.Namespace(), client=C())
    assert "稍后看" in capsys.readouterr().out
    el.cmd_history(argparse.Namespace(limit=10), client=C())
    assert "历史" in capsys.readouterr().out
    el.cmd_following(argparse.Namespace(limit=10), client=C())
    assert "小明" in capsys.readouterr().out


def test_platforms_command(capsys):
    el.cmd_platforms(argparse.Namespace())
    out = capsys.readouterr().out
    assert "bilibili" in out and "weibo" in out
    assert "自进化" in out


def test_scan_unknown_url_hint(monkeypatch, capsys):
    monkeypatch.setattr(el, "_scan_adapter", lambda s: None)
    args = argparse.Namespace(input="https://vimeo.com/1", limit=None, filter=None,
                              priority=False, snapshot=False, out=None)
    with pytest.raises(SystemExit) as ei:
        el.cmd_scan(args)
    assert ei.value.code == 2
    assert "自进化" in capsys.readouterr().err


# ---------- 首次引导门（onboard） ----------

def _onboard_env(tmp_path, monkeypatch, onboarded=False, out_dir=None):
    import onboard as ob
    from core import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", str(tmp_path / "config.json"))
    skill = tmp_path / "SKILL.md"
    skill.write_text("<!-- ⛳ ONBOARDING: 1 -->\n<!-- ⛔ READ-GATE: ONBOARDING: 1 -->\n",
                     encoding="utf-8")
    monkeypatch.setattr(ob, "SKILL_PATH", str(skill))
    cfg_mod.write_user_config({"out_dir": out_dir if out_dir is not None else str(tmp_path / "out"),
                               "onboarded": onboarded})
    return ob, cfg_mod


def test_onboard_command_shows_checklist(tmp_path, monkeypatch, capsys):
    """el.py onboard：状态 + 四项确认点 + 依赖提示 + 完成指引"""
    _onboard_env(tmp_path, monkeypatch)
    el.cmd_onboard(argparse.Namespace(complete=False, reset=False, json=False))
    out = capsys.readouterr().out
    assert "引导" in out
    assert "产出目录" in out and "Whisper" in out and "依赖" in out and "使用" in out
    assert "术语" in out and "bilibili-to-wiki" in out          # 2026-10-17 第⑤点
    assert "onboard --complete" in out


def test_onboard_complete_and_reset(tmp_path, monkeypatch, capsys):
    ob, _ = _onboard_env(tmp_path, monkeypatch)
    el.cmd_onboard(argparse.Namespace(complete=True, reset=False, json=False))
    st = ob.read_status()
    assert st["onboarded"] is True and st["flag"] == 0
    el.cmd_onboard(argparse.Namespace(complete=False, reset=True, json=False))
    st = ob.read_status()
    assert st["onboarded"] is False and st["flag"] == 1


def test_onboard_complete_requires_out_dir(tmp_path, monkeypatch, capsys):
    _onboard_env(tmp_path, monkeypatch, out_dir="")
    with pytest.raises(SystemExit) as e:
        el.cmd_onboard(argparse.Namespace(complete=True, reset=False, json=False))
    assert e.value.code == 2


def test_onboard_json(tmp_path, monkeypatch, capsys):
    _onboard_env(tmp_path, monkeypatch)
    el.cmd_onboard(argparse.Namespace(complete=False, reset=False, json=True))
    data = json.loads(capsys.readouterr().out)
    assert data["flag"] == 1 and data["onboarded"] is False and "out_dir" in data


def test_run_soft_onboarding_notice(tmp_path, monkeypatch, capsys):
    """onboarded 未完成 → run 软提示（stderr），不阻塞主流程"""
    _onboard_env(tmp_path, monkeypatch, onboarded=False)
    out = tmp_path / "out"
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                  no_whisper=False, pick=1), client=FakeClient())
    err = capsys.readouterr().err
    assert "引导" in err and "onboard" in err


def test_run_no_notice_when_onboarded(tmp_path, monkeypatch, capsys):
    _onboard_env(tmp_path, monkeypatch, onboarded=True)
    out = tmp_path / "out"
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                  no_whisper=False, pick=1), client=FakeClient())
    assert "引导未完成" not in capsys.readouterr().err


def test_update_command_delegates(monkeypatch):
    """el.py update → 委托 update.main（--dry-run 透传）"""
    import update as upd_mod
    seen = {}
    monkeypatch.setattr(upd_mod, "main", lambda argv=None: seen.setdefault("argv", argv) or 0)
    el.cmd_update(argparse.Namespace(dry_run=True))
    assert seen["argv"] == ["--dry-run"]
    monkeypatch.setattr(upd_mod, "main", lambda argv=None: seen.setdefault("argv2", argv) or 0)
    el.cmd_update(argparse.Namespace(dry_run=False))
    assert seen["argv2"] == []


def test_resolve_unknown_url_reports_unsupported(capsys):
    """实测：未知 URL 被静默当成关键词；应走自进化提示"""
    el.cmd_resolve(argparse.Namespace(input="https://example.com/abc"))
    out = json.loads(capsys.readouterr().out)
    assert out["kind"] == "unsupported" and out["url"] == "https://example.com/abc"


def test_resolve_plain_keyword_still_search(capsys):
    el.cmd_resolve(argparse.Namespace(input="DeepSeek V4.1"))
    out = json.loads(capsys.readouterr().out)
    assert out["kind"] == "search"


def test_run_unknown_url_does_not_search(tmp_path, capsys):
    """未知 URL 绝不能回落成关键词搜索"""
    client = FakeClient()
    with pytest.raises(SystemExit) as e:
        el.cmd_run(argparse.Namespace(input="https://example.com/abc", out=str(tmp_path),
                                      pick=None), client=client)
    assert e.value.code == 2
    assert "自进化" in capsys.readouterr().err


def test_run_keyword_requires_explicit_pick(tmp_path, capsys):
    """实测：名称输入静默取第一条；必须列候选并要求 --pick"""
    client = FakeClient()
    client.search_out = [{"bvid": "BV1GJ411x7h7", "title": "候选A"}]
    with pytest.raises(SystemExit) as e:
        el.cmd_run(argparse.Namespace(input="某个关键词", out=str(tmp_path), pick=None),
                   client=client)
    assert e.value.code == 2
    cap = capsys.readouterr()
    assert "1. 候选A（BV1GJ411x7h7）" in (cap.out + cap.err)
    assert "--pick" in cap.err


def test_run_keyword_yes_picks_first(tmp_path):
    client = FakeClient()
    client.search_out = [{"bvid": "BV1GJ411x7h7", "title": "候选A"}]
    el.cmd_run(argparse.Namespace(input="某个关键词", out=str(tmp_path), pick=None, yes=True),
               client=client)
    assert list(tmp_path.rglob("content_info.json"))


def test_ensure_utf8_stdout_safe():
    el._ensure_utf8_stdout()  # 不应抛异常（不可重配置的流跳过）


def test_cmd_search_multi_platform_prints_candidates(monkeypatch, capsys):
    from core import search as search_mod
    monkeypatch.setattr(search_mod, "search",
                        lambda p, k, limit=5: [{"platform": p, "title": "候选T",
                                                "url": "https://x/1", "author": "A",
                                                "duration_text": "1:00"}])
    el.cmd_search(argparse.Namespace(platform="zhihu", keyword="k", limit=2, json=False))
    out = capsys.readouterr().out
    assert "1. 候选T" in out and "https://x/1" in out


def test_cmd_search_json(monkeypatch, capsys):
    from core import search as search_mod
    monkeypatch.setattr(search_mod, "search",
                        lambda p, k, limit=5: [{"platform": p, "title": "T", "url": "u"}])
    el.cmd_search(argparse.Namespace(platform="youtube", keyword="k", limit=1, json=True))
    data = json.loads(capsys.readouterr().out)
    assert data[0]["title"] == "T"


def test_cmd_search_unavailable_is_readable(monkeypatch, capsys):
    from core import search as search_mod
    def boom(*a, **k):
        raise search_mod.SearchUnavailable("暂未接入搜索")
    monkeypatch.setattr(search_mod, "search", boom)
    with pytest.raises(SystemExit) as e:
        el.cmd_search(argparse.Namespace(platform="x_twitter", keyword="k", limit=1, json=False))
    assert e.value.code == 2
    assert "搜索不可用" in capsys.readouterr().err


def test_wechat_pick_requires_confirm_when_list_drifted(monkeypatch, tmp_path, capsys):
    """列表漂移时 --pick 不可直接复用（防取错条目）"""
    class WeChat:
        name = "wechat_mp"
        kinds = {"article"}
        def match(self, u): return "mp.weixin.qq.com" in u
        def search_articles(self, kw, limit=5):
            return [{"title": "新条目", "account": "号", "date": "2026-10-17", "link": "L"}]
        def search_fingerprint(self, hits): return "NEW"
        def load_search_fingerprint(self, q): return {"fp": "OLD", "ts": 0}
        def save_search_fingerprint(self, q, hits): pass
        def resolve_sogou_link(self, link): return "https://mp.weixin.qq.com/s/x"
    monkeypatch.setattr(el, "_wechat_adapter", lambda: WeChat())
    with pytest.raises(SystemExit) as e:
        el.cmd_run(argparse.Namespace(input="DeepSeek", out=str(tmp_path), platform="wechat",
                                      pick=1), client=FakeClient())
    assert e.value.code == 2
    assert "漂移" in capsys.readouterr().err


def test_run_report_format_txt(tmp_path):
    """交付格式在开工前确认；CLI 用 --report-format 落地（txt 示例）"""
    out = tmp_path / "out"
    client = FakeClient()
    el.cmd_run(argparse.Namespace(input="BV1GJ411x7h7", page=1, out=str(out),
                                  no_whisper=False, pick=1, report_format="txt"),
               client=client)
    rd = report_dir(str(out))
    txts = [f for f in os.listdir(rd) if f.endswith(".txt") and "学习报告" in f]
    assert txts, os.listdir(rd)
