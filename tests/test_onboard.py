"""onboard 模块测试：SKILL.md 双标签位读写 + config.onboarded 状态机（2026-10-17）"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
import onboard as ob
from core import config as cfg

SKILL_TEMPLATE = """# everything-learn

<!-- ⛳ ONBOARDING: 1 -->
说明：1=未完成引导；0=已完成。

## 正常调用工作流
（步骤……）

<!-- ⛔ READ-GATE: ONBOARDING: 1 -->
说明：ONBOARDING=0 → 到此为止；=1 → 必读下方。

## 初次调用逻辑
（引导……）
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    skill = tmp_path / "SKILL.md"
    skill.write_text(SKILL_TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(ob, "SKILL_PATH", str(skill))
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "config.json"))
    cfg.write_user_config({"out_dir": "D:/笔记"})
    return skill


def test_set_flags_rewrites_both_markers(env):
    assert ob.set_flags(0) is True
    text = env.read_text(encoding="utf-8")
    assert "<!-- ⛳ ONBOARDING: 0 -->" in text
    assert "<!-- ⛔ READ-GATE: ONBOARDING: 0 -->" in text
    assert "ONBOARDING: 1 -->" not in text
    assert "## 正常调用工作流" in text and "## 初次调用逻辑" in text   # 其他文本不动


def test_onboard_output_counts_and_platforms(env, capsys):
    """首启输出必须与现状一致：12 平台 / 五项确认点（①-⑤）"""
    import types
    import el
    el.cmd_onboard(types.SimpleNamespace(complete=False, reset=False, json=False))
    out = capsys.readouterr().out
    assert "12 平台" in out
    assert "五项确认" in out
    assert "11 平台" not in out
    for mark in ("①", "②", "③", "④", "⑤"):
        assert mark in out


def test_set_flags_missing_file_tolerated(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "SKILL_PATH", str(tmp_path / "none.md"))
    assert ob.set_flags(0) is False


def test_read_status(env):
    st = ob.read_status()
    assert st["onboarded"] is False and st["flag"] == 1
    assert st["out_dir"] == "D:/笔记" and st["whisper_model"] == "small"
    ob.complete()
    st2 = ob.read_status()
    assert st2["onboarded"] is True and st2["flag"] == 0


def test_complete_requires_out_dir(env):
    cfg.write_user_config({"out_dir": ""})
    with pytest.raises(ob.OnboardError) as e:
        ob.complete()
    assert ("产出目录" in str(e.value)) or ("out_dir" in str(e.value))


def test_complete_and_reset_idempotent(env):
    r1 = ob.complete()
    r2 = ob.complete()
    assert r1["onboarded"] is True and r2["onboarded"] is True
    assert ob.read_status()["flag"] == 0
    r3 = ob.reset()
    assert r3["onboarded"] is False and ob.read_status()["flag"] == 1


def test_complete_without_skill_marks_config(tmp_path, monkeypatch):
    """SKILL.md 缺失：仍写 config（flag_written=False），不抛"""
    monkeypatch.setattr(ob, "SKILL_PATH", str(tmp_path / "none.md"))
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "config.json"))
    cfg.write_user_config({"out_dir": "D:/x"})
    r = ob.complete()
    assert r["onboarded"] is True and r["flag_written"] is False


def test_set_flags_retries_transient_permission_error(env, monkeypatch):
    """Windows 偶发：os.replace PermissionError（文件瞬时锁定）→ 自动重试成功"""
    import os as _os
    calls = {"n": 0}
    real_replace = _os.replace

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError("locked")
        return real_replace(src, dst)

    monkeypatch.setattr(ob.os, "replace", flaky)
    assert ob.set_flags(0) is True
    assert calls["n"] == 2
