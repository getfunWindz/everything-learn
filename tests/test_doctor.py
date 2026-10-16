# -*- coding: utf-8 -*-
"""C1/C2: doctor 环境自检与 GPU DLL 自动注入测试"""
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from transcriber import _ensure_nvidia_paths
import doctor


class FakeApiClient:
    """模拟 ApiClient：可注入 nav 结果与异常"""

    def __init__(self, nav_ok=True):
        self._nav_ok = nav_ok

    def check_login(self):
        return self._nav_ok


# ---------- C2: GPU DLL 自动注入 ----------

def test_ensure_nvidia_paths_idempotent():
    """重复调用不重复注入、不抛异常"""
    saved = os.environ.get("PATH", "")
    try:
        _ensure_nvidia_paths()
        p1 = os.environ["PATH"]
        _ensure_nvidia_paths()
        assert os.environ["PATH"] == p1, "重复调用不应重复注入"
        assert "nvidia" in os.environ["PATH"].lower() or saved == p1, "应至少尝试注入"
    finally:
        os.environ["PATH"] = saved


def test_ensure_nvidia_paths_injects_real_paths():
    """本机装有 nvidia pip 包时，路径应真实存在且注入"""
    import site
    sp = site.getsitepackages()[0]
    real = os.path.join(sp, "nvidia", "cublas", "bin")
    if os.path.isdir(real):
        saved = os.environ.get("PATH", "")
        try:
            _ensure_nvidia_paths()
            assert real.lower() in os.environ["PATH"].lower(), "cuBLAS 路径应被注入"
        finally:
            os.environ["PATH"] = saved


# ---------- C1: doctor 检查函数 ----------

def test_check_cookie_ok_and_bad():
    assert doctor.check_cookie(FakeApiClient(nav_ok=True)) is True
    assert doctor.check_cookie(FakeApiClient(nav_ok=False)) is False


def test_check_cookie_handles_exception():
    class BoomClient:
        def check_login(self):
            raise RuntimeError("network error")

    assert doctor.check_cookie(BoomClient()) is False


def test_check_models_missing_cache(monkeypatch, tmp_path):
    """HF 缓存目录不存在时 doctor 不崩溃（CI runner 上实测踩过）"""
    missing = str(tmp_path / "nope" / "hub")
    monkeypatch.setattr(doctor.os.path, "expanduser", lambda p: missing)
    r = doctor.check_models()
    assert r["models"] == [] and r["cache_dir"] == missing


def test_check_whisper_version_compat():
    """faster-whisper 可用且 transcribe 支持 log_progress（≥1.2 API）"""
    pytest.importorskip("faster_whisper")
    status = doctor.check_whisper()
    assert status["installed"] is True
    assert status["compatible"] is True, "faster-whisper 应兼容 log_progress API"


def test_check_gpu_reports_dll_status():
    """GPU 检查应返回 cuda 可用性与 cublas/cudnn DLL 探测结果"""
    result = doctor.check_gpu()
    assert "device" in result
    assert "cublas_ok" in result
    assert "cudnn_ok" in result
    # 本机若检测到 cuda 设备但缺 DLL，应给出修复提示（不抛异常）
    if result["device"] == "cuda":
        assert isinstance(result["cublas_ok"], bool)
        assert isinstance(result["cudnn_ok"], bool)


def test_doctor_main_runs():
    """doctor main 应以 0 退出且输出诊断行（不触发网络，靠注入）"""
    code = doctor.main(["cookie", "gpu", "whisper"])
    assert code == 0


def test_check_vision_reports_tier_policy(monkeypatch):
    """D2：doctor 展示 recheck.tier_policy（旧 subtitle_check 死键不再宣传）"""
    from core import config as cfg
    monkeypatch.setattr(cfg, "load_config",
                        lambda: {"vision": {"enabled": True},
                                 "recheck": {"tier_policy": "tier2"}})
    v = doctor.check_vision()
    assert v["enabled"] is True and v["tier_policy"] == "tier2"
    assert "subtitle_check" not in v


def test_check_vision_defaults_when_unconfigured(monkeypatch):
    from core import config as cfg
    monkeypatch.setattr(cfg, "load_config", lambda: {})
    v = doctor.check_vision()
    assert v["enabled"] is False and v["tier_policy"] == "auto"


def test_doctor_main_shows_tier_policy_not_dead_key(monkeypatch, capsys):
    """D2：doctor 输出复检档位策略，不再出现 subtitle_check/字幕复检模式 死键宣传"""
    monkeypatch.setattr(doctor, "check_cookie", lambda client=None: True)
    monkeypatch.setattr(doctor, "check_gpu",
                        lambda: {"device": "cpu", "cublas_ok": False,
                                 "cudnn_ok": False, "hint": ""})
    monkeypatch.setattr(doctor, "check_whisper",
                        lambda: {"installed": False, "compatible": False,
                                 "version": "", "hint": ""})
    monkeypatch.setattr(doctor, "check_models", lambda: {"models": [], "cache_dir": "x"})
    monkeypatch.setattr(doctor, "check_vision", lambda: {"enabled": True, "tier_policy": "tier1"})
    monkeypatch.setattr(doctor, "check_ytdlp",
                        lambda: {"installed": False, "version": "", "hint": ""})
    monkeypatch.setattr(doctor, "describe_out_dir",
                        lambda: {"configured": False, "ok": False, "path": ""})
    assert doctor.main(["cookie"]) == 0
    out = capsys.readouterr().out
    assert "复检档位策略=tier1" in out
    assert "subtitle_check" not in out and "字幕复检模式" not in out


def test_doctor_main_survives_gbk_console(monkeypatch):
    """Windows GBK 控制台：emoji 输出不应崩溃（stdout 重配置 utf-8/replace）"""
    import io
    monkeypatch.setattr(doctor, "check_cookie", lambda client=None: True)
    wrapper = io.TextIOWrapper(io.BytesIO(), encoding="gbk", errors="strict")
    monkeypatch.setattr(sys, "stdout", wrapper)
    assert doctor.main(["cookie"]) == 0


def test_check_ytdlp_installed():
    pytest.importorskip("yt_dlp")
    r = doctor.check_ytdlp()
    assert r["installed"] is True and r["version"]


def test_describe_out_dir_unconfigured(monkeypatch):
    """未配置 out_dir → configured=False（doctor 不得把 cwd 误报为可用输出目录）"""
    from core import config as cfg
    monkeypatch.setattr(cfg, "load_config", lambda: {"out_dir": ""})
    r = doctor.describe_out_dir()
    assert r["configured"] is False and r["ok"] is False


def test_describe_out_dir_configured(tmp_path, monkeypatch):
    from core import config as cfg
    target = str(tmp_path / "学习笔记")
    monkeypatch.setattr(cfg, "load_config", lambda: {"out_dir": target})
    r = doctor.describe_out_dir()
    assert r["configured"] is True and r["ok"] is True and r["path"] == target


def test_check_ytdlp_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "yt_dlp", None)
    r = doctor.check_ytdlp()
    assert r["installed"] is False and "pip install" in r["hint"]


def test_missing_optional_lists_missing(monkeypatch, tmp_path):
    import json as _json
    import doctor
    from core import config as cfg
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "none.json"))
    monkeypatch.setattr(doctor, "_has_module", lambda name: False)
    names = [d["name"] for d in doctor.missing_optional()]
    assert any("faster-whisper" in n for n in names)
    assert any("yt-dlp" in n for n in names)
    assert any("playwright" in n for n in names)
    assert any("vision" in n for n in names)


def test_missing_optional_empty_when_ready(monkeypatch, tmp_path):
    import json as _json
    import doctor
    from core import config as cfg
    p = tmp_path / "config.json"
    p.write_text(_json.dumps({"vision": {"enabled": True}}), encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(p))
    monkeypatch.setattr(doctor, "_has_module", lambda name: True)
    assert doctor.missing_optional() == []
