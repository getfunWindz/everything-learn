import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
from core import config as cfg


def test_default_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "none.json"))
    c = cfg.load_config()
    assert c["whisper_model"] == "small"      # fast 档（出厂默认；引导建议升 medium）
    assert c["onboarded"] is False
    assert c["out_dir"] == ""
    assert c["hf_endpoint"] == ""
    assert c["hf_disable_xet"] is True


def test_new_policy_defaults(tmp_path, monkeypatch):
    """whisper/vad/retry/vision 的 everything-learn 默认策略"""
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "none.json"))
    c = cfg.load_config()
    assert c["whisper_vad"] == "auto" and c["vad_long_sec"] == 1800
    assert c["api_retries"] == 4 and c["api_retry_base"] == 1.5
    assert c["auth"] == {}
    assert c["vision"]["subtitle_check"] == "auto"


def test_recheck_cross_check_defaults(tmp_path, monkeypatch):
    """NIT-1：双 ASR 交叉比对默认参数落地 config.recheck（可发现性），与签名口径一致"""
    import inspect
    from core import recheck
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "none.json"))
    rc = cfg.load_config()["recheck"]
    assert rc["cross_check"] is True
    assert rc["cross_window_sec"] == 15
    assert rc["cross_threshold"] == 0.65
    params = inspect.signature(recheck.cross_check).parameters
    assert (params["window_sec"].default, params["threshold"].default) == (15, 0.65)


def test_load_custom_config(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"out_dir": "D:/笔记", "whisper_model": "large-v3"}), encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(p))
    c = cfg.load_config()
    assert c["out_dir"] == "D:/笔记" and c["whisper_model"] == "large-v3"
    assert c["hf_endpoint"] == ""  # 缺失字段用默认


def test_ensure_creates_default(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(p))
    path = cfg.ensure_config()
    assert os.path.exists(path)
    c = json.load(open(path, encoding="utf-8"))
    assert c["whisper_model"] == "small" and c["out_dir"] == ""


def test_apply_hf_env(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    cfg.apply_hf_env({"hf_endpoint": "https://hf-mirror.com", "hf_disable_xet": True})
    assert os.environ.get("HF_ENDPOINT") == "https://hf-mirror.com"
    assert os.environ.get("HF_HUB_DISABLE_XET") == "1"
    monkeypatch.setenv("HF_ENDPOINT", "https://custom")
    cfg.apply_hf_env({"hf_endpoint": "https://other", "hf_disable_xet": True})
    assert os.environ.get("HF_ENDPOINT") == "https://custom"


def test_vision_config_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "none.json"))
    v = cfg.load_config()["vision"]
    assert v["enabled"] is False
    assert v["model"] == "gpt-4o-mini"
    assert v["max_frames"] == 30 and v["max_rounds"] == 2
    assert v["subtitle_check"] == "auto"


def test_vision_config_partial_merge(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"vision": {"enabled": True, "api_key": "sk-x", "model": "qwen-vl-max"}}),
                 encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(p))
    v = cfg.load_config()["vision"]
    assert v["enabled"] is True and v["model"] == "qwen-vl-max"
    assert v["max_rounds"] == 2  # 缺失字段用默认


# ---------- out_dir 硬性规则 ----------

def test_out_dir_hard_rule_empty_raises(tmp_path, monkeypatch):
    """未配置且无 CLI 覆盖 → 拒绝运行（硬性要求用户自选）"""
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "none.json"))
    with pytest.raises(cfg.ConfigError):
        cfg.resolve_out_dir(None)


def test_out_dir_cli_overrides_config(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"out_dir": "D:/在配置里"}), encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(p))
    assert cfg.resolve_out_dir("D:/CLI") == "D:/CLI"
    assert cfg.resolve_out_dir(None) == "D:/在配置里"


import json

def test_recheck_defaults_present():
    from core import config
    rc = config.DEFAULT_CONFIG["recheck"]
    assert rc["tier_policy"] == "auto"
    assert rc["tier2_score"] == 0.45
    assert rc["frame_fps"] == 3
    assert rc["window_sec"] == 30
    assert rc["max_check_frames"] == 20000
    assert rc["auto_approve_amount"] == 0.3
    assert rc["confirm_over_sec"] == 3600
    assert rc["tier3"] == {"allow": False, "max_density": 1.5, "max_gap_sec": 15}
    assert config.DEFAULT_CONFIG["vision"]["pricing"]["tokens_per_frame"] == 600


def test_load_config_deep_merges_nested(tmp_path, monkeypatch):
    from core import config
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"recheck": {"tier2_score": 0.7, "tier3": {"allow": True}},
                             "vision": {"pricing": {"currency": "CNY"}}}), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", str(p))
    cfg = config.load_config()
    assert cfg["recheck"]["tier2_score"] == 0.7
    assert cfg["recheck"]["window_sec"] == 30            # 缺省补齐
    assert cfg["recheck"]["tier3"]["allow"] is True      # 用户覆盖
    assert cfg["recheck"]["tier3"]["max_density"] == 1.5 # 嵌套缺省补齐
    assert cfg["vision"]["pricing"]["currency"] == "CNY"
    assert cfg["vision"]["pricing"]["input_per_1m"] == 0.3


def test_write_user_config_merges_and_preserves(tmp_path, monkeypatch):
    """write_user_config：只改指定键，既有配置保留；load_config 可读回"""
    p = tmp_path / "config.json"
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(p))
    p.write_text(json.dumps({"out_dir": "D:/笔记"}), encoding="utf-8")
    cfg.write_user_config({"onboarded": True, "whisper_model": "medium"})
    raw = json.load(open(p, encoding="utf-8"))
    assert raw["out_dir"] == "D:/笔记"
    assert raw["onboarded"] is True and raw["whisper_model"] == "medium"
    c = cfg.load_config()
    assert c["onboarded"] is True and c["whisper_model"] == "medium"


def test_write_user_config_deep_merges_nested(tmp_path, monkeypatch):
    """嵌套配置只改指定子键：setup --no-kb 不得清空 kb.path（2026-10-18 收尾修复）"""
    p = tmp_path / "config.json"
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(p))
    cfg.write_user_config({"kb": {"enabled": True, "path": "D:/custom/kb.sqlite3"}})
    cfg.write_user_config({"kb": {"enabled": False}})
    raw = json.load(open(p, encoding="utf-8"))
    assert raw["kb"]["enabled"] is False
    assert raw["kb"]["path"] == "D:/custom/kb.sqlite3"


def test_write_user_config_recovers_corrupt(tmp_path, monkeypatch):
    """损坏 JSON → 以空基底重建（不抛异常）"""
    p = tmp_path / "config.json"
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(p))
    p.write_text("{broken", encoding="utf-8")
    cfg.write_user_config({"onboarded": True})
    assert json.load(open(p, encoding="utf-8")) == {"onboarded": True}
