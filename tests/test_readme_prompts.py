# -*- coding: utf-8 -*-
"""README 侧交付校验：中英提示词表、SKILL 链接、独立文件移除与版本。"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
README = ROOT / "README.md"


def _section(text, heading):
    match = re.search(
        rf"^### {re.escape(heading)}.*?\n(.*?)(?=^### |^## |\Z)",
        text, re.M | re.S,
    )
    assert match, f"README 缺少小节：{heading}"
    return match.group(1)


def _prompt_rows(section):
    return [line for line in section.splitlines() if line.startswith("| ") and "everything-learn" in line]


def test_chinese_prompt_table_has_five_rows():
    section = _section(README.read_text(encoding="utf-8"), "典型提示词（范例）")
    rows = _prompt_rows(section)
    assert len(rows) == 5, rows
    for row in rows:
        cells = [cell.strip() for cell in row.split("|")[1:-1]]
        assert len(cells) == 3, row
        assert cells[0] and cells[1] and cells[2], row


def test_english_prompt_table_has_five_rows():
    section = _section(README.read_text(encoding="utf-8"), "Prompt examples")
    rows = _prompt_rows(section)
    assert len(rows) == 5, rows


def test_skill_links_readme_and_has_anchor_sentence():
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "README.md#典型提示词范例" in skill
    assert "用户提到 everything-learn" in skill
    assert "references/prompts.md" not in skill


def test_standalone_prompts_file_removed():
    assert not (ROOT / "references" / "prompts.md").exists()
    for name in ("README.md", "SKILL.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "references/prompts.md" not in text, name


def test_changelog_unreleased_and_version_unchanged():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [Unreleased]" in changelog
    assert "README" in changelog.split("## [0.4.23]")[0]
    from scripts.version import VERSION
    assert VERSION == "0.4.23"
