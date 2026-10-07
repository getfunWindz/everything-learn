import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from cookie_export import build_cookie_string, required_present, cookie_file_for, PLATFORMS


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


def test_platform_configs_have_required_cookies():
    assert PLATFORMS["zhihu"]["required"] == ["z_c0"]
    assert "SESSDATA" in PLATFORMS["bilibili"]["required"]
    assert "__zse_ck" in PLATFORMS["zhihu"]["ready"]  # 就绪判定需含挑战 cookie
    assert PLATFORMS["zhihu"]["probe_url"].startswith("https://")  # 内容页触发风控脚本
    for cfg in PLATFORMS.values():
        assert cfg["url"].startswith("https://") and cfg["domain"] and cfg["file"]
