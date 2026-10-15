import sys, os, json, io
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
import vision

def _make_test_video(path, seconds=2, fps=10, size=(64, 64), static=False):
    """用 PyAV 生成纯色测试视频（static=True 时逐帧同色；未装 av/numpy 时自动 skip）"""
    pytest.importorskip("av")
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    import av
    container = av.open(path, mode="w")
    stream = container.add_stream("mpeg4", rate=fps)
    stream.width, stream.height = size
    stream.pix_fmt = "yuv420p"
    for i in range(seconds * fps):
        frame = av.VideoFrame(size[0], size[1], "rgb24")
        import numpy as np
        arr = np.full((size[1], size[0], 3), 0 if static else i * 10 % 255, dtype=np.uint8)
        frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    return path

def test_extract_frames(tmp_path):
    v = _make_test_video(str(tmp_path / "t.mp4"), seconds=2, fps=10)
    frames = vision.extract_frames(v, interval_sec=1, max_frames=30)
    assert len(frames) >= 2  # 0s / 1s（2 秒视频抽到 2-3 帧）
    ts_list = [ts for ts, _ in frames]
    assert 0 in ts_list
    for ts, jpeg in frames:
        assert jpeg[:2] == b"\xff\xd8"  # JPEG 头

def test_extract_frames_respects_max(tmp_path):
    v = _make_test_video(str(tmp_path / "t.mp4"), seconds=2, fps=10)
    frames = vision.extract_frames(v, interval_sec=0.5, max_frames=2)
    assert len(frames) <= 2

def test_vision_not_configured():
    with pytest.raises(vision.VisionError):
        vision.describe_video({"enabled": False}, [])
    with pytest.raises(vision.VisionError):
        vision.describe_video({"enabled": True, "api_key": ""}, [])

def test_describe_video_payload(monkeypatch):
    captured = {}

    class FakeSession:
        def post(self, url, json=None, headers=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers

            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"choices": [{"message": {"content": "画面描述结果"}}]}

            return R()

    monkeypatch.setattr(vision.requests, "Session", FakeSession)
    cfg = {"enabled": True, "base_url": "https://api.example.com/v1",
           "api_key": "sk-test", "model": "qwen-vl-max"}
    frames = [(0, b"\xff\xd8fakejpeg"), (10, b"\xff\xd8fakejpeg2")]
    out = vision.describe_video(cfg, frames, prompt="测试提示")
    assert out == "画面描述结果"
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    msg = captured["json"]["messages"][0]["content"]
    kinds = [c["type"] for c in msg]
    assert kinds.count("image_url") == 2
    assert any("base64" in c["image_url"]["url"] for c in msg if c["type"] == "image_url")

def test_describe_video_uses_session_trust_env_false(monkeypatch):
    """回归：requests.post 不接受 trust_env 参数（requests 2.34 实测 TypeError）。
    必须走 Session.trust_env=False；旧实现 requests.post(trust_env=False) 会被 mock 掩盖，
    真实调用必崩——本测试用严格签名直接堵住该回归。"""
    captured = {}

    class FakeSession:
        def __init__(self):
            self.trust_env = True  # 默认会继承环境代理，待实现显式关闭

        def post(self, url, json=None, headers=None, timeout=None):
            captured["trust_env"] = self.trust_env
            captured["kwargs"] = {"json": json, "headers": headers, "timeout": timeout}

            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"choices": [{"message": {"content": "ok"}}]}

            return R()

    monkeypatch.setattr(vision.requests, "Session", FakeSession)

    def must_not_call_post(*a, **k):
        raise AssertionError("不应直接调用 requests.post（它不接受 trust_env 参数）")

    monkeypatch.setattr(vision.requests, "post", must_not_call_post)
    cfg = {"enabled": True, "base_url": "https://api.example.com/v1",
           "api_key": "sk-test", "model": "m"}
    out = vision.describe_video(cfg, [(0, b"jpeg")], prompt="p")
    assert out == "ok"
    assert captured["trust_env"] is False
    assert "trust_env" not in captured["kwargs"]


def test_vision_transcribe(monkeypatch, tmp_path):
    monkeypatch.setattr(vision, "extract_frames",
                        lambda p, interval_sec=10, max_frames=24: [(0, b"jpeg")])
    monkeypatch.setattr(vision, "describe_video",
                        lambda cfg, frames, prompt=None: "[0] 画面文字内容")
    out = vision.vision_transcribe({"enabled": True}, "x.mp4")
    assert out == "[0] 画面文字内容"


def test_extract_frames_range(tmp_path):
    """区间内每秒抽帧"""
    v = _make_test_video(str(tmp_path / "t.mp4"), seconds=4, fps=10)
    frames = vision.extract_frames_range(v, start_sec=1, end_sec=4, fps=1, max_frames=30)
    assert 2 <= len(frames) <= 4  # 1s/2s/3s（每秒 1 帧）
    assert frames[0][0] >= 1


def test_extract_frames_at(tmp_path):
    """定点抽帧（seek 优化）：3 个时间点至少取到 2 帧"""
    v = _make_test_video(str(tmp_path / "t.mp4"), seconds=4, fps=10)
    frames = vision.extract_frames_at(v, [0.5, 2.0, 3.5])
    assert len(frames) >= 2
    for ts, jpeg in frames:
        assert jpeg[:2] == b"\xff\xd8"  # JPEG 头


def test_sample_window_frames_dedup(tmp_path):
    # 静帧去重需真静态素材：默认 i*10 渐变视频相邻帧差 ~0.15（远大于阈值 0.02），不是「纯色」
    v = _make_test_video(str(tmp_path / "t.mp4"), seconds=3, fps=10, static=True)
    frames = vision.sample_window_frames(v, 0, 3, fps=3, dedup=True)
    assert frames, "应抽到帧"
    # 纯色视频 → 去重后帧数远小于 3fps×3s=9
    assert len(frames) <= 4
    ts = [t for t, _ in frames]
    assert ts == sorted(ts)


def test_sample_window_frames_respects_cap(tmp_path):
    v = _make_test_video(str(tmp_path / "t.mp4"), seconds=30, fps=10)
    frames = vision.sample_window_frames(v, 0, 30, fps=3, max_frames=20)
    assert len(frames) <= 20


def test_parse_window_result_kinds():
    text = ("[10] 补充：白板公式 E=mc^2\n"
            "[12] 校正：原转写「梯度下降」→「梯度上升」\n"
            "[15] 不确定：画面是纯口播，无法判断\n")
    items = vision.parse_window_result(text)
    kinds = [i["kind"] for i in items]
    assert "supplement" in kinds and "correction" in kinds and "uncertain" in kinds
    corr = [i for i in items if i["kind"] == "correction"][0]
    assert corr["original"] == "梯度下降" and corr["text"] == "梯度上升"


def test_parse_window_result_no_issue():
    assert vision.parse_window_result("无遗漏") == []


def test_check_window_builds_prompt(monkeypatch):
    captured = {}
    def fake_describe(cfg, frames, prompt=None):
        captured["prompt"] = prompt
        return "无遗漏"
    monkeypatch.setattr(vision, "describe_video", fake_describe)
    out = vision.check_window({"enabled": True}, [(0, b"jpeg")],
                              transcript_text="这一秒在讲公式",
                              secondary_text="可能另一份字幕")
    assert out == []
    assert "这一秒在讲公式" in captured["prompt"]
    assert "仅参考" in captured["prompt"]


# ---------- 时间对齐多点视觉归属复检（无音频流兜底，spec §8.2） ----------

def _stub_attribution_frames(monkeypatch):
    """mock 抽帧：时间戳与真实 extract_frames_at 一致（int(round)）"""
    monkeypatch.setattr(vision, "extract_frames_at",
                        lambda p, ts: [(int(round(t)), b"jpeg") for t in ts])


def _attribution_lines(n=20):
    return [{"start": i, "end": i + 1, "text": "知识内容"} for i in range(n)]


def test_check_attribution_aligned_pass(monkeypatch, tmp_path):
    v = _make_test_video(str(tmp_path / "t.mp4"), seconds=20, fps=10)
    _stub_attribution_frames(monkeypatch)
    monkeypatch.setattr(vision, "describe_video", lambda cfg, frames, prompt=None:
                        "\n".join(f"[{ts}] 一致" for ts, _ in frames))
    r = vision.check_attribution_aligned({"enabled": True}, v, _attribution_lines(), 20)
    assert r["passed"] is True
    assert len(r["verdicts"]) == 5 and all(v["match"] for v in r["verdicts"])
    assert r["checked"] is True


def test_check_attribution_aligned_fail_when_mismatch(monkeypatch, tmp_path):
    v = _make_test_video(str(tmp_path / "t.mp4"), seconds=20, fps=10)
    _stub_attribution_frames(monkeypatch)
    monkeypatch.setattr(vision, "describe_video", lambda cfg, frames, prompt=None:
                        "[1] 不一致：影视剧\n[5] 不一致：广告\n[10] 不一致：歌词")
    r = vision.check_attribution_aligned({"enabled": True}, v, _attribution_lines(), 20)
    assert r["passed"] is False and r["checked"] is True
    assert len(r["verdicts"]) == 3
    assert all(v["match"] is False for v in r["verdicts"])


def test_check_attribution_aligned_two_mismatches_fail(monkeypatch, tmp_path):
    """边界：3 点中 2 点不一致（>1）→ 不通过（钉死 <=1 门槛）"""
    v = _make_test_video(str(tmp_path / "t.mp4"), seconds=20, fps=10)
    _stub_attribution_frames(monkeypatch)
    monkeypatch.setattr(vision, "describe_video", lambda cfg, frames, prompt=None:
                        "[1] 一致\n[5] 不一致：广告\n[10] 不一致：歌词")
    r = vision.check_attribution_aligned({"enabled": True}, v, _attribution_lines(), 20)
    assert r["checked"] is True and r["passed"] is False
    assert sum(1 for v in r["verdicts"] if not v["match"]) == 2


def test_check_attribution_aligned_parse_failure_is_failsafe(monkeypatch, tmp_path):
    v = _make_test_video(str(tmp_path / "t.mp4"), seconds=20, fps=10)
    _stub_attribution_frames(monkeypatch)
    monkeypatch.setattr(vision, "describe_video", lambda cfg, frames, prompt=None: "无法解析的输出")
    r = vision.check_attribution_aligned({"enabled": True}, v, _attribution_lines(), 20)
    assert r["passed"] is False and r["verdicts"] == []
    assert r["checked"] is False and r["reason"] == "insufficient_verdicts"


def test_check_attribution_aligned_duplicate_rows_not_counted(monkeypatch, tmp_path):
    """同一采点重复 3 行不能顶替 3 个独立采点（去重后不足 3 → fail-safe）"""
    v = _make_test_video(str(tmp_path / "t.mp4"), seconds=20, fps=10)
    _stub_attribution_frames(monkeypatch)
    monkeypatch.setattr(vision, "describe_video", lambda cfg, frames, prompt=None:
                        "[5] 一致\n[5] 一致\n[5] 一致")
    r = vision.check_attribution_aligned({"enabled": True}, v, _attribution_lines(), 20)
    assert r["passed"] is False and r["checked"] is False
    assert r["reason"] == "insufficient_verdicts"
    assert len(r["verdicts"]) == 1


def test_check_attribution_aligned_off_sample_timestamps_ignored(monkeypatch, tmp_path):
    """时间戳全不在采样点 → 过滤后 0 点，不计入判定"""
    v = _make_test_video(str(tmp_path / "t.mp4"), seconds=20, fps=10)
    _stub_attribution_frames(monkeypatch)
    monkeypatch.setattr(vision, "describe_video", lambda cfg, frames, prompt=None:
                        "[100] 一致\n[101] 一致\n[102] 一致")
    r = vision.check_attribution_aligned({"enabled": True}, v, _attribution_lines(), 20)
    assert r["passed"] is False and r["checked"] is False
    assert r["verdicts"] == [] and r["reason"] == "insufficient_verdicts"


def test_check_attribution_aligned_insufficient_points(monkeypatch, tmp_path):
    """有效点仅 2 个（<3）→ checked=False，不给出通过结论"""
    v = _make_test_video(str(tmp_path / "t.mp4"), seconds=20, fps=10)
    _stub_attribution_frames(monkeypatch)
    monkeypatch.setattr(vision, "describe_video", lambda cfg, frames, prompt=None:
                        "[10] 一致\n[14] 一致")
    r = vision.check_attribution_aligned({"enabled": True}, v, _attribution_lines(), 20)
    assert r["passed"] is False and r["checked"] is False
    assert r["reason"] == "insufficient_verdicts" and len(r["verdicts"]) == 2


@pytest.mark.parametrize("duration,lines", [
    (0, [{"start": 0, "end": 1, "text": "内容"}]),
    (20, []),
])
def test_check_attribution_aligned_invalid_input(monkeypatch, duration, lines):
    """duration<=0 或 lines 空 → invalid_input（不抽帧）"""
    def _boom(*a, **k):
        raise AssertionError("无效输入不应抽帧")
    monkeypatch.setattr(vision, "extract_frames_at", _boom)
    r = vision.check_attribution_aligned({"enabled": True}, "missing.mp4", lines, duration)
    assert r == {"passed": False, "verdicts": [], "checked": False,
                 "reason": "invalid_input"}


def test_check_attribution_aligned_empty_frames_raises(monkeypatch, tmp_path):
    """空帧属于基础设施失败 → 上抛 VisionError（由调用方降级）"""
    v = _make_test_video(str(tmp_path / "t.mp4"), seconds=20, fps=10)
    monkeypatch.setattr(vision, "extract_frames_at", lambda p, ts: [])
    with pytest.raises(vision.VisionError):
        vision.check_attribution_aligned({"enabled": True}, v, _attribution_lines(), 20)


def test_check_attribution_aligned_uniform_points_and_prompt(monkeypatch):
    """points=3 时均匀采样（5%/50%/95%）且提示词写实际点数"""
    captured = {}

    def _extract(p, ts):
        captured["times"] = list(ts)
        return [(int(round(t)), b"jpeg") for t in ts]

    def _describe(cfg, frames, prompt=None):
        captured["prompt"] = prompt
        return "\n".join(f"[{ts}] 一致" for ts, _ in frames)

    monkeypatch.setattr(vision, "extract_frames_at", _extract)
    monkeypatch.setattr(vision, "describe_video", _describe)
    r = vision.check_attribution_aligned({"enabled": True}, "missing.mp4",
                                         _attribution_lines(), 20, points=3)
    assert captured["times"] == [1.0, 10.0, 19.0]
    assert "3 个时间点" in captured["prompt"]
    assert r["passed"] is True and r["checked"] is True


# ---------- 旧原语退役（M13 §8.3） ----------

def test_legacy_defense_primitives_retired():
    """旧字幕防线与旧多模态复检原语已整体退役：模块不再提供这些符号"""
    import core.pipeline as pipeline
    for name in ("check_subtitle_match", "parse_match_verdict", "MATCH_PROMPT",
                 "pick_edge_timestamps", "_fmt_excerpt", "find_content_gaps",
                 "pick_check_intervals", "plan_check_intervals", "check_transcript",
                 "CHECK_PROMPT", "PLAN_PROMPT"):
        assert not hasattr(vision, name), f"vision.{name} 应已删除"
    for name in ("_want_subtitle_check", "_vision_verify_subtitle", "_vision_check"):
        assert not hasattr(pipeline, name), f"pipeline.{name} 应已删除"


def test_window_primitives_kept():
    """窗口复检机制（档位复检执行层）保留：感知原语 + 窗口抽帧/复检/解析"""
    for name in ("extract_frames", "extract_frames_range", "extract_frames_at",
                 "describe_video", "vision_transcribe", "sample_window_frames",
                 "check_window", "parse_window_result", "check_attribution_aligned"):
        assert callable(getattr(vision, name, None)), name
