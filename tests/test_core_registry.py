import sys, os
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from core import registry


class FakeAdapter:
    name = "fake"
    kinds = {"video"}
    def match(self, url): return "fake.example" in url
    def resolve(self, q): return None
    def list_items(self, ref): return [ref]
    def fetch_meta(self, ref): return None
    def fetch_content(self, ref): return None
    def fetch_media_url(self, ref, media="audio", lowest=False): return None


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.reset()
    yield
    registry.reset()


def test_register_and_get():
    registry.register(FakeAdapter)
    assert registry.get("fake") is not None
    assert registry.get("nope") is None


def test_match_url():
    registry.register(FakeAdapter)
    assert registry.match_url("https://fake.example/v/1").name == "fake"
    assert registry.match_url("https://other.com/v/1") is None


def test_registry_lists():
    registry.register(FakeAdapter)
    assert [a.name for a in registry.all()] == ["fake"]


def test_default_falls_back_to_bilibili():
    class Bili:
        name = "bilibili"
        kinds = {"video"}
        def match(self, url): return False
        def resolve(self, q): return None
        def list_items(self, ref): return [ref]
        def fetch_meta(self, ref): return None
        def fetch_content(self, ref): return None
        def fetch_media_url(self, ref, media="audio", lowest=False): return None
    registry.register(Bili)
    assert registry.default().name == "bilibili"


def test_load_providers_self_heals_after_reset():
    """注册表被 reset 后重新 load_providers 仍能恢复（不依赖 import 副作用）"""
    registry.reset()
    registry.load_providers()
    assert registry.get("bilibili") is not None


def test_sniff_url_only_when_match_fails():
    """URL 路由兜底：sniff 适配器接管无特征 URL（仅 http/https；异常静默跳过）"""
    class Sniffer:
        name = "sniffable"
        kinds = {"audio"}
        def match(self, url): return False
        def sniff(self, url): return "mysite.example/feed" in url
        def resolve(self, q): return None
        def list_items(self, ref): return [ref]
        def fetch_meta(self, ref): return None
        def fetch_content(self, ref): return None
        def fetch_media_url(self, ref, media="audio", lowest=False): return None
    registry.register(Sniffer)
    assert registry.sniff_url("https://mysite.example/feed") is not None
    assert registry.sniff_url("mysite.example/feed") is None        # 非 http(s)
    assert registry.sniff_url("https://other.example/x") is None    # sniff 返回 False


def test_sniff_url_skips_exceptions():
    class Boom:
        name = "boom"
        kinds = {"audio"}
        def match(self, url): return False
        def sniff(self, url): raise RuntimeError("network")
        def resolve(self, q): return None
        def list_items(self, ref): return [ref]
        def fetch_meta(self, ref): return None
        def fetch_content(self, ref): return None
        def fetch_media_url(self, ref, media="audio", lowest=False): return None
    registry.register(Boom)
    assert registry.sniff_url("https://x.example/y") is None
