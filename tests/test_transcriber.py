import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
import transcriber

def test_load_whisper_not_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises(transcriber.WhisperNotInstalled):
        transcriber._load_whisper()

def test_detect_device_cpu_without_ctranslate2(monkeypatch):
    monkeypatch.setitem(sys.modules, "ctranslate2", None)
    assert transcriber.detect_device() == "cpu"

def test_download_audio(tmp_path, monkeypatch):
    calls = {}
    def fake_get(url, headers=None, stream=False, timeout=None):
        calls["url"] = url
        class R:
            def raise_for_status(self): pass
            def iter_content(self, chunk_size=1): yield b"1234"
        return R()
    monkeypatch.setattr(transcriber.requests, "get", fake_get)
    dest = transcriber.download_audio("http://x/audio.m4s", str(tmp_path / "a.m4s"))
    assert calls["url"] == "http://x/audio.m4s"
    assert open(dest, "rb").read() == b"1234"

def test_transcribe_gpu_failure_falls_back_cpu(monkeypatch):
    """检测到 CUDA 但库不可用（DLL 缺失）→ 自动回退 CPU 重试"""
    calls = []
    class FakeModel:
        def __init__(self, model_size, device, compute_type):
            calls.append((device, compute_type))
        def transcribe(self, audio_path, language="zh", vad_filter=False, log_progress=None, progress_callback=None):
            if calls[-1][0] == "cuda":
                raise RuntimeError("Library cublas64_12.dll is not found")
            class S: pass
            s = S(); s.start, s.end, s.text = 0.0, 1.0, "ok"
            return [s], None
    monkeypatch.setattr(transcriber, "_load_whisper", lambda: FakeModel)
    monkeypatch.setattr(transcriber, "detect_device", lambda: "cuda")
    out = transcriber.transcribe("x.m4s")
    assert calls == [("cuda", "float16"), ("cpu", "int8")]  # 先 GPU，失败回退 CPU
    assert out == [{"start": 0.0, "end": 1.0, "text": "ok"}]

def test_transcribe_progress_callback(monkeypatch):
    seen = []
    class FakeModel:
        def __init__(self, *a, **k): pass
        def transcribe(self, audio_path, language="zh", vad_filter=False, log_progress=None, progress_callback=None):
            if progress_callback:
                progress_callback(10, 100)
                progress_callback(100, 100)
            class S: pass
            s = S(); s.start, s.end, s.text = 0.0, 1.0, "hi"
            return [s], None
    monkeypatch.setattr(transcriber, "_load_whisper", lambda: FakeModel)
    transcriber.transcribe("x.m4s", progress_callback=lambda a, b: seen.append((a, b)))
    assert seen == [(10, 100), (100, 100)]

def test_clean_text_compresses_fillers():
    assert transcriber.clean_text("啊啊啊你好") == "啊你好"
    assert transcriber.clean_text("哈哈哈哈 对") == "哈哈 对"
    assert transcriber.clean_text("对对对 没错") == "对对 没错"
    assert transcriber.clean_text("这个  是   测试") == "这个 是 测试"
    assert transcriber.clean_text("正常句子") == "正常句子"  # 正常文本不变

def test_transcribe_applies_clean(monkeypatch):
    """转写输出经过 clean_text 清洗"""
    class FakeModel:
        def __init__(self, *a, **k): pass
        def transcribe(self, audio_path, language="zh", vad_filter=False, log_progress=None, progress_callback=None):
            class S: pass
            s = S(); s.start, s.end, s.text = 0.0, 1.0, "啊啊啊  大家好"
            return [s], None
    monkeypatch.setattr(transcriber, "_load_whisper", lambda: FakeModel)
    out = transcriber.transcribe("x.m4s")
    assert out[0]["text"] == "啊 大家好"

def test_transcribe_vad_fallback(monkeypatch):
    """vad_filter 滤空时自动降级为 vad_filter=False 重试"""
    calls = []
    class FakeModel:
        def __init__(self, *a, **k): pass
        def transcribe(self, audio_path, language="zh", vad_filter=False, log_progress=None, progress_callback=None):
            calls.append(vad_filter)
            if vad_filter:
                return [], None
            class S: pass
            s = S(); s.start, s.end, s.text = 0.0, 1.0, "hello"
            return [s], None
    monkeypatch.setattr(transcriber, "_load_whisper", lambda: FakeModel)
    out = transcriber.transcribe("x.m4s")
    assert calls == [True, False]  # 先 VAD，空则降级重试
    assert out == [{"start": 0.0, "end": 1.0, "text": "hello"}]

def test_transcribe_video_flow(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self): self.url_called = None
        def get_audio_url(self, bvid, cid): self.url_called = (bvid, cid); return "http://audio"
    client = FakeClient()
    monkeypatch.setattr(transcriber, "download_audio", lambda url, dest, headers=None: dest)
    monkeypatch.setattr(transcriber, "transcribe",
                        lambda path, model_size="medium", progress_callback=None, **kw: [{"start": 0.0, "end": 1.0, "text": "hi"}])
    out = transcriber.transcribe_video(client, "BV1GJ411x7h7", 1001)
    assert out == [{"start": 0.0, "end": 1.0, "text": "hi"}]
    assert client.url_called == ("BV1GJ411x7h7", 1001)


def test_transcribe_url_flow(monkeypatch):
    """平台无关入口：给媒体直链 → 下载 → 转写"""
    calls = {}
    monkeypatch.setattr(transcriber, "download_audio",
                        lambda url, dest, headers=None: calls.setdefault("url", url) or dest)
    monkeypatch.setattr(transcriber, "transcribe",
                        lambda path, model_size="medium", **kw: [{"start": 0.0, "end": 1.0, "text": "hi"}])
    out = transcriber.transcribe_url("http://media/audio.m4s", model_size="small", duration_sec=10)
    assert out == [{"start": 0.0, "end": 1.0, "text": "hi"}]
    assert calls["url"] == "http://media/audio.m4s"


def test_transcribe_url_passes_language(monkeypatch):
    """language 透传（X 等多语言平台用 None 自动检测；默认仍 zh 保持既有行为）"""
    captured = {}
    monkeypatch.setattr(transcriber, "download_audio",
                        lambda url, dest, headers=None: dest)

    def fake_transcribe(path, **kw):
        captured.update(kw)
        return []

    monkeypatch.setattr(transcriber, "transcribe", fake_transcribe)
    transcriber.transcribe_url("http://media/audio.m4s", language=None)
    assert captured.get("language") is None


# ---------- VAD 策略：长视频默认禁用，可手动打开 ----------

def _fake_model_factory(calls):
    class FakeModel:
        def __init__(self, *a, **k): pass
        def transcribe(self, audio_path, language="zh", vad_filter=False, log_progress=None, progress_callback=None):
            calls.append(vad_filter)
            class S: pass
            s = S(); s.start, s.end, s.text = 0.0, 1.0, "ok"
            return [s], None
    return FakeModel

def test_vad_plan_forced_on():
    assert transcriber._vad_plan(True, 7200) == [True]
    assert transcriber._vad_plan(False, 10) == [False]

def test_vad_plan_long_video_default_off():
    assert transcriber._vad_plan(None, 7200) == [False]       # 长视频默认禁 VAD（不回退）
    assert transcriber._vad_plan(None, 600) == [True, False]  # 短视频保持原兜底

def test_transcribe_long_video_disables_vad(monkeypatch):
    calls = []
    monkeypatch.setattr(transcriber, "_load_whisper", lambda: _fake_model_factory(calls))
    monkeypatch.setattr(transcriber, "detect_device", lambda: "cpu")
    transcriber.transcribe("x.m4s", duration_sec=7200)
    assert calls == [False]

def test_transcribe_vad_manual_on(monkeypatch):
    calls = []
    monkeypatch.setattr(transcriber, "_load_whisper", lambda: _fake_model_factory(calls))
    monkeypatch.setattr(transcriber, "detect_device", lambda: "cpu")
    transcriber.transcribe("x.m4s", duration_sec=7200, vad=True)
    assert calls == [True]

def test_default_model_is_medium():
    import inspect
    assert inspect.signature(transcriber.transcribe).parameters["model_size"].default == "medium"


def test_download_audio_headers_passthrough(tmp_path, monkeypatch):
    """媒体 CDN 平台化请求头：显式 headers 生效；缺省用内置 HEADERS（B站向）"""
    import transcriber
    seen = {}

    class R:
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=None): yield b"x"

    def fake_get(url, headers=None, stream=None, timeout=None):
        seen["headers"] = headers
        return R()
    monkeypatch.setattr(transcriber.requests, "get", fake_get)
    dest = str(tmp_path / "a.bin")
    transcriber.download_audio("http://x/1", dest, headers={"X": "1"})
    assert seen["headers"] == {"X": "1"}
    transcriber.download_audio("http://x/1", dest)
    assert seen["headers"] is transcriber.HEADERS


def test_extract_audio_clip(tmp_path):
    pytest.importorskip("av")
    import numpy as np
    import av as _av
    # 生成 3 秒 16k 单声道 wav，分段常数振幅（非零信号：0-1s=6554，1-2s=16384，2-3s=26214）
    pcm = np.concatenate([
        np.full(16000, 6554, dtype=np.int16),
        np.full(16000, 16384, dtype=np.int16),
        np.full(16000, 26214, dtype=np.int16),
    ])
    container = _av.open(str(tmp_path / "a.wav"), mode="w")
    stream = container.add_stream("pcm_s16le", rate=16000)
    for i in range(0, len(pcm), 1024):
        block = pcm[i:i + 1024]
        if len(block) < 1024:
            block = np.pad(block, (0, 1024 - len(block)))
        frame = _av.AudioFrame.from_ndarray(
            block.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = 16000
        for p in stream.encode(frame):
            container.mux(p)
    for p in stream.encode():
        container.mux(p)
    container.close()
    from transcriber import extract_audio_clip
    samples, sr = extract_audio_clip(str(tmp_path / "a.wav"), 1.0, 2.0)
    assert sr == 16000                                  # API 契约
    assert samples.dtype == np.float32                  # 归一化输出类型
    assert 0.1 < float(np.abs(samples).max()) <= 1.0    # 有信号且已 /32768 归一化
    assert 12000 <= len(samples) <= 20000               # ≈1s @16k（帧粒度）


def test_transcribe_samples_uses_model(monkeypatch):
    import numpy as np
    import transcriber

    class Seg:
        start, end, text = 0.0, 1.0, " 你好，世界 "

    class FakeModel:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, samples, language=None, vad_filter=False):
            assert isinstance(samples, np.ndarray)
            return [Seg()], None

    monkeypatch.setattr(transcriber, "_load_whisper", lambda: FakeModel)
    monkeypatch.setattr(transcriber, "detect_device", lambda: "cpu")
    out = transcriber.transcribe_samples(np.zeros(16000, dtype=np.float32),
                                         model_size="small", language="zh")
    assert "你好" in out
