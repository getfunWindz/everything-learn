# -*- coding: utf-8 -*-
"""一键初始化：目录/知识库/依赖同意/引导置位"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
import setup as setup_mod
from core import config
import onboard


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", str(cfg))
    skill = tmp_path / "SKILL.md"
    skill.write_text("<!-- ⛳ ONBOARDING: 1 -->\n<!-- ⛔ READ-GATE: ONBOARDING: 1 -->\n",
                     encoding="utf-8")
    monkeypatch.setattr(onboard, "SKILL_PATH", str(skill))
    monkeypatch.setattr(setup_mod, "_missing", lambda: [])
    return tmp_path


def test_setup_writes_config_and_flags(isolated):
    out = isolated / "notes"
    assert setup_mod.run(out=str(out), yes=True, kb=False) == 0
    cfg = config.load_config()
    assert cfg["out_dir"] == str(out.resolve())
    assert cfg["onboarded"] is True
    assert cfg["kb"]["enabled"] is False
    assert "ONBOARDING: 0" in (isolated / "SKILL.md").read_text(encoding="utf-8")


def test_setup_kb_opt_in(isolated):
    assert setup_mod.run(out=str(isolated / "n"), yes=True, kb=True) == 0
    assert config.load_config()["kb"]["enabled"] is True


def test_setup_install_requires_consent(isolated, monkeypatch):
    calls = []
    monkeypatch.setattr(setup_mod, "_missing", lambda: [{"name": "yt-dlp"}])
    monkeypatch.setattr(setup_mod, "_pip_install", lambda names, runner=None: calls.append(names) or True)
    setup_mod.run(out=str(isolated / "n"), yes=True, install=False)
    assert calls == []
    setup_mod.run(out=str(isolated / "n"), yes=True, install=True)
    assert calls == [["yt-dlp"]]


def test_cli_setup(isolated):
    import el
    with pytest.raises(SystemExit) as e:
        el.main(["setup", "--out", str(isolated / "n"), "--yes", "--no-kb"])
    assert e.value.code == 0
    assert config.load_config()["out_dir"] == str((isolated / "n").resolve())
