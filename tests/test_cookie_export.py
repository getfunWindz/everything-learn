import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from cookie_export import (build_cookie_string, required_present, cookie_file_for,
                           discover_platforms, load_platforms, _valid_spec)


def test_build_cookie_string():
    assert build_cookie_string([("z_c0", "x"), ("__zse_ck", "y")]) == "z_c0=x; __zse_ck=y"
    assert build_cookie_string([]) == ""


def test_required_present():
    assert required_present({"z_c0": "x", "d_c0": "y"}, ["z_c0"]) is True
    assert required_present({"d_c0": "y"}, ["z_c0", "__zse_ck"]) is False
    assert required_present({}, ["z_c0"]) is False


def test_cookie_file_for_known_and_unknown():
    assert cookie_file_for("zhihu").endswith(".zhihu_cookie")
    assert cookie_file_for("bilibili").endswith(".bili_cookie")
    try:
        cookie_file_for("nope")
        assert False
    except SystemExit:
        pass


def test_discover_includes_builtin_platforms():
    """标配：规格由 provider 模块声明（COOKIE_EXPORT），工具扫描发现"""
    platforms = discover_platforms()
    assert {"zhihu", "bilibili"} <= set(platforms)
    assert platforms["zhihu"]["file"] == ".zhihu_cookie"
    assert "__zse_ck" in platforms["zhihu"]["ready"]
    assert platforms["bilibili"]["required"] == ["SESSDATA"]
    assert load_platforms(refresh=True) is not None


def test_valid_spec_filter():
    ok = {"url": "u", "read_url": "r", "required": ["a"], "ready": ["a"], "wanted": ["a"], "file": "f"}
    assert _valid_spec(ok) is True
    assert _valid_spec({}) is False
    assert _valid_spec({**ok, "required": []}) is False   # 空 required = 不是登录态判定
    assert _valid_spec({**ok, "file": ""}) is False
    assert _valid_spec("not-a-dict") is False
