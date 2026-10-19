# -*- coding: utf-8 -*-
"""A 侧交付校验：5 条范例的结构、锚点、命令真实性，以及文档引用。"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
PROMPTS = ROOT / "references" / "prompts.md"

REQUIRED_FIELDS = ["**定位**", "**提示词（可照抄）**", "**期望行为", "**不该发生**", "**变体"]


def _sections(text):
    return re.split(r"^## 场景 ", text, flags=re.M)[1:]


def _cli_subcommands():
    out = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "scripts" / "el.py"), "--help"],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
    )
    match = re.search(r"\{([a-z,\-]+)\}", out.stdout)
    assert match, out.stdout[:400]
    return set(match.group(1).split(","))


def test_prompts_file_has_five_scenarios():
    assert PROMPTS.exists(), "references/prompts.md 缺失"
    assert len(_sections(PROMPTS.read_text(encoding="utf-8"))) == 5


def test_every_scenario_has_anchor_and_fields():
    for section in _sections(PROMPTS.read_text(encoding="utf-8")):
        assert section.count("everything-learn") >= 3, section[:40]
        for field in REQUIRED_FIELDS:
            assert field in section, f"{field} 缺失于 {section[:40]}"


def test_commands_reference_real_cli_subcommands():
    text = PROMPTS.read_text(encoding="utf-8")
    used = set(re.findall(r"el\.py\s+([a-z\-]+)", text))
    assert used, "没有任何 el.py 命令"
    assert used <= _cli_subcommands(), f"未知子命令: {used - _cli_subcommands()}"


def test_skill_md_links_prompts_and_has_anchor_sentence():
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "典型提示词" in skill
    assert "references/prompts.md" in skill
    assert "用户提到 everything-learn" in skill


def test_readme_links_prompts():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "references/prompts.md" in readme


def test_changelog_has_unreleased_and_version_unchanged():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [Unreleased]" in changelog
    from scripts.version import VERSION
    assert VERSION == "0.4.23"
