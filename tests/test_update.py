"""update 助手测试（2026-10-17）：git 调用全 mock；stash/恢复/标签位恢复/dry-run"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
import update as upd


def _git_mock(handlers, calls):
    """handlers: [(pred, resp)]；未匹配 → (0, \"\", \"\")"""
    def fake(args):
        calls.append(list(args))
        for pred, resp in handlers:
            if pred(list(args)):
                return resp
        return (0, "", "")
    return fake


def _clean(monkeypatch, onboarded=True, restore=None):
    calls = []
    handlers = [
        (lambda a: a[:2] == ["status", "--porcelain"], (0, "", "")),
        (lambda a: a == ["rev-parse", "--short", "HEAD"], (0, "abc1234", "")),
        (lambda a: a == ["pull", "--ff-only"], (0, "Updating 111..222 Fast-forward", "")),
    ]
    monkeypatch.setattr(upd, "_git", _git_mock(handlers, calls))
    monkeypatch.setattr(upd, "_read_onboarded", lambda: onboarded)
    restored = []
    if restore is None:
        def restore():
            restored.append(True)
            return "恢复引导标签位 0（onboard --complete）"
    monkeypatch.setattr(upd, "_restore_onboarded", restore)
    return calls, restored


def test_update_clean_tree_pulls_and_restores(monkeypatch):
    calls, restored = _clean(monkeypatch)
    r = upd.update()
    assert r["ok"] and not r.get("dry_run")
    assert ["pull", "--ff-only"] in calls
    assert restored                       # 已引导 → 恢复标签位
    assert any("恢复" in s for s in r["steps"])


def test_update_discards_skill_flag_then_restores(monkeypatch):
    calls = []
    handlers = [
        (lambda a: a[:2] == ["status", "--porcelain"], (0, " M SKILL.md", "")),
        (lambda a: a == ["rev-parse", "--short", "HEAD"], (0, "abc1234", "")),
        (lambda a: a == ["pull", "--ff-only"], (0, "Updating 1..2", "")),
    ]
    monkeypatch.setattr(upd, "_git", _git_mock(handlers, calls))
    monkeypatch.setattr(upd, "_read_onboarded", lambda: True)
    monkeypatch.setattr(upd, "_restore_onboarded", lambda: "恢复引导标签位 0（onboard --complete）")
    r = upd.update()
    assert ["checkout", "--", "SKILL.md"] in calls
    assert not any(a and a[0] == "stash" for a in calls)   # 仅标签位脏 → 不需要 stash
    assert r["ok"]


def test_update_stashes_other_changes_and_pops(monkeypatch):
    calls = []
    handlers = [
        (lambda a: a[:2] == ["status", "--porcelain"], (0, " M README.md\n?? notes.txt", "")),
        (lambda a: a == ["rev-parse", "--short", "HEAD"], (0, "abc", "")),
        (lambda a: a[:2] == ["stash", "push"], (0, "Saved working directory", "")),
        (lambda a: a == ["stash", "pop"], (0, "Dropped", "")),
        (lambda a: a == ["pull", "--ff-only"], (0, "Updating 1..2", "")),
    ]
    monkeypatch.setattr(upd, "_git", _git_mock(handlers, calls))
    monkeypatch.setattr(upd, "_read_onboarded", lambda: False)
    r = upd.update()
    assert any(a[:2] == ["stash", "push"] for a in calls)
    assert ["stash", "pop"] in calls
    assert r["ok"]


def test_update_pull_failure_restores_stash_and_raises(monkeypatch):
    calls = []
    handlers = [
        (lambda a: a[:2] == ["status", "--porcelain"], (0, " M README.md", "")),
        (lambda a: a[:2] == ["stash", "push"], (0, "Saved", "")),
        (lambda a: a == ["pull", "--ff-only"], (1, "", "fatal: Not possible to fast-forward")),
        (lambda a: a == ["stash", "pop"], (0, "Dropped", "")),
    ]
    monkeypatch.setattr(upd, "_git", _git_mock(handlers, calls))
    monkeypatch.setattr(upd, "_read_onboarded", lambda: True)
    with pytest.raises(upd.UpdateError):
        upd.update()
    assert ["stash", "pop"] in calls      # pull 失败 → 恢复暂存，不留丢失风险


def test_update_dry_run_no_changes(monkeypatch):
    calls = []
    handlers = [
        (lambda a: a[:2] == ["status", "--porcelain"], (0, " M SKILL.md", "")),
        (lambda a: a == ["rev-parse", "--short", "HEAD"], (0, "abc", "")),
    ]
    monkeypatch.setattr(upd, "_git", _git_mock(handlers, calls))
    monkeypatch.setattr(upd, "_read_onboarded", lambda: True)
    r = upd.update(dry_run=True)
    assert r["dry_run"] is True
    assert ["pull", "--ff-only"] not in calls
    assert not any(a and a[0] in ("checkout", "stash") for a in calls)


def test_update_not_git_repo_raises(monkeypatch):
    calls = []
    monkeypatch.setattr(upd, "_git", _git_mock(
        [(lambda a: a[:2] == ["status", "--porcelain"], (1, "", "fatal: not a git repository"))], calls))
    with pytest.raises(upd.UpdateError):
        upd.update()


def test_update_not_onboarded_skips_restore(monkeypatch):
    calls, restored = _clean(monkeypatch, onboarded=False)
    r = upd.update()
    assert r["ok"] and not restored
    assert any("未引导" in s for s in r["steps"])


def test_plan_parses_porcelain_first_line_leading_space(monkeypatch):
    """回归：_git 的 strip 曾吃掉 porcelain 首行前导空格（首个文件少一字符）"""
    class P:
        returncode = 0
        stdout = " M SKILL.md\n?? notes.txt\n"
        stderr = ""

    monkeypatch.setattr(upd.subprocess, "run", lambda *a, **k: P())
    monkeypatch.setattr(upd, "_read_onboarded", lambda: False)
    st = upd.plan()
    assert st["dirty_files"] == ["SKILL.md", "notes.txt"]
    assert st["skill_dirty"] is True and st["others"] == ["notes.txt"]
