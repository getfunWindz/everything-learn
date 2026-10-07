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
    def fetch_media_url(self, ref, lowest=False): return None


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
        def fetch_media_url(self, ref, lowest=False): return None
    registry.register(Bili)
    assert registry.default().name == "bilibili"
