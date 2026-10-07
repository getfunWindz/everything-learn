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
