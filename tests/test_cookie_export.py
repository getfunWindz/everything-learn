import sys, os
import time
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


def test_write_cookie_file_wildcard(tmp_path):
    """wanted=["*"] → 全量导出（风控 cookie 集不确定的平台用）"""
    from cookie_export import _write_cookie_file
    out = str(tmp_path / "c.txt")
    assert _write_cookie_file(out, {"wanted": ["*"]}, {"b": "2", "a": "1"}) == 0
    assert open(out, encoding="utf-8").read().strip() == "a=1; b=2"


def test_valid_spec_verify_optional_and_checked():
    """verify 为可选字段；声明时必须是 {url, expect} 非空"""
    ok = {"url": "u", "read_url": "r", "required": ["a"], "ready": ["a"],
          "wanted": ["a"], "file": "f"}
    assert _valid_spec({**ok, "verify": {"url": "https://x/api", "expect": '"login":true'}}) is True
    assert _valid_spec(ok) is True                       # 不声明也合法（向后兼容）
    assert _valid_spec({**ok, "verify": {}}) is False
    assert _valid_spec({**ok, "verify": {"url": "u"}}) is False
    assert _valid_spec({**ok, "verify": {"expect": "x"}}) is False


def test_weibo_spec_declares_login_verify():
    """防假登录：SUB 游客也有 → 微博必须用登录校验（/api/config login:true）"""
    platforms = discover_platforms()
    v = platforms["weibo"].get("verify") or {}
    assert v.get("url", "").endswith("/api/config")
    assert '"login":true' in (v.get("expect") or "")


def test_wait_for_verify_success_and_timeout():
    from cookie_export import _wait_for_verify

    class FakePage:
        def __init__(self, seq):
            self.seq = list(seq)
            self.calls = 0

        def evaluate(self, expr, arg):
            self.calls += 1
            return self.seq[min(self.calls - 1, len(self.seq) - 1)]

        def wait_for_timeout(self, ms):
            pass

    page = FakePage(['{"login":false}', '{"login":true}'])
    ok, err = _wait_for_verify(page, {"url": "https://x/api", "expect": '"login":true'},
                               time.time() + 30)
    assert ok is True and err == "" and page.calls == 2

    ok2, err2 = _wait_for_verify(FakePage(['{"login":false}']),
                                 {"url": "https://x/api", "expect": '"login":true'}, 0)
    assert ok2 is False and "登录校验" in err2
