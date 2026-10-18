# -*- coding: utf-8 -*-
"""契约测试生成器"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import gen_contract_test as gen


def test_render_contains_contract(tmp_path):
    text = gen.render("demo")
    assert "run_contract" in text and 'name == "demo"' in text
    assert "pytest.skip" in text


def test_generate_writes_file_and_refuses_overwrite(tmp_path):
    p = gen.generate("demo", dest_dir=str(tmp_path))
    assert os.path.exists(p) and "test_contract_demo" in open(p, encoding="utf-8").read()
    try:
        gen.generate("demo", dest_dir=str(tmp_path))
        assert False
    except FileExistsError:
        pass
    p2 = gen.generate("demo", dest_dir=str(tmp_path), force=True)
    assert p2 == p


def test_generate_rejects_bad_name(tmp_path):
    try:
        gen.generate("bad name!", dest_dir=str(tmp_path))
        assert False
    except ValueError:
        pass
