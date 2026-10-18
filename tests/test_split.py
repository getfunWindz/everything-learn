# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import split


def test_split_text_boundaries_and_overlap():
    text = "".join(f"[{i}.0-{i+1}.0] " + "字" * 100 + "\n" for i in range(50))
    parts = split.split_text(text, chunk_chars=2000, overlap_chars=300)
    assert len(parts) >= 2
    assert all(len(p) > 0 for p in parts)
    # 重叠：上一块尾部内容出现在下一块开头附近
    assert parts[0][-100:].strip() in parts[1][:500] or parts[1][:100].strip() in parts[0]


def test_split_dir_manifest(tmp_path):
    d = tmp_path / "item"
    d.mkdir()
    (d / "content.txt").write_text("[0.0-10.0] a\n[10.0-20.0] b\n" + "x" * 5000, encoding="utf-8")
    m = split.split_dir(str(d), chunk_chars=2000, overlap_chars=200)
    assert m["parts"] and (d / m["parts"][0]["file"]).exists()
    assert (d / "split_manifest.json").exists()
    assert m["parts"][0]["start"] == 0.0
