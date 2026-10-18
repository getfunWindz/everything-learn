# -*- coding: utf-8 -*-
"""敏感文件权限（POSIX）：config / cookie 600"""
import os
import stat
import sys
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
import cookie_export
from core import config


@pytest.mark.skipif(os.name != "posix", reason="POSIX chmod only")
def test_config_created_0600(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", str(path))
    config.ensure_config()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX chmod only")
def test_cookie_written_0600(tmp_path, monkeypatch):
    out = tmp_path / "c.txt"
    monkeypatch.setattr(cookie_export, "cookie_file_for", lambda p: str(out))
    assert cookie_export.import_cookie("bilibili", "SESSDATA=x") == 0
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
