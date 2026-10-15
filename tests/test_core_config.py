import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
from core import config as cfg


def test_default_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "none.json"))
    c = cfg.load_config()
    assert c["whisper_model"] == "medium"
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
    assert c["whisper_model"] == "medium" and c["out_dir"] == ""


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
    assert rc["tier2_score"] == 0.55
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
