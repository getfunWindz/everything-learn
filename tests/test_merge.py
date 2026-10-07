# -*- coding: utf-8 -*-
"""D2: 合集目录合并测试"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import mergeutil


def _make_p_dir(root, pname, content):
    d = os.path.join(root, pname)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "content.txt"), "w", encoding="utf-8") as f:
        f.write(content)
    return d


def test_merge_copies_missing_pages():
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "src")
        dst = os.path.join(td, "dst")
        _make_p_dir(src, "P01", "P01内容")
        _make_p_dir(src, "P02", "P02内容")
        _make_p_dir(dst, "P01", "P01旧内容")
        copied, skipped = mergeutil.merge_dirs(src, dst)
        assert copied == ["P02"], f"应复制缺失的 P02：{copied}"
        assert skipped == ["P01"], f"P01 已存在应跳过：{skipped}"
        assert open(os.path.join(dst, "P02", "content.txt"), encoding="utf-8").read() == "P02内容"
        # P01 不被覆盖
        assert open(os.path.join(dst, "P01", "content.txt"), encoding="utf-8").read() == "P01旧内容"
