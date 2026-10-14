"""适配器契约测试：M0 起所有适配器（含自进化生成的）都必须通过"""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

from ._fakes import FakeAdapter
from .harness import run_contract
from core.adapter import SourceAdapter, SearchNeeded  # noqa: F401  （SearchNeeded 供适配器使用）


def test_contract_accepts_protocol_shape():
    assert run_contract(FakeAdapter()) == []
    assert isinstance(FakeAdapter(), SourceAdapter)


def test_contract_rejects_incomplete():
    class Bad:
        pass
    problems = run_contract(Bad())
    assert any("name" in p for p in problems)
    assert any("kinds" in p for p in problems)
    assert any("match" in p for p in problems)


def test_contract_requires_media_channel_kwargs():
    """统一多模态复检通道：fetch_media_url 必须接受 media/lowest（core 用它们取画面/音轨）"""
    class NoKwargs(FakeAdapter):
        def fetch_media_url(self, ref):        # 缺 media/lowest → 通道不可用
            return None
    problems = run_contract(NoKwargs())
    assert any("fetch_media_url" in p for p in problems)
