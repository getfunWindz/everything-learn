"""适配器契约 harness：M0 起所有适配器（含自进化生成的）都必须通过"""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

from ._fakes import FakeAdapter
from core.adapter import SourceAdapter, SearchNeeded  # noqa: F401  （SearchNeeded 供适配器使用）


def run_contract(adapter) -> list:
    """对任意适配器执行契约检查；返回问题列表（空 = 通过）"""
    problems = []
    if not getattr(adapter, "name", ""):
        problems.append("缺少 name")
    if not isinstance(getattr(adapter, "kinds", None), set) or not adapter.kinds:
        problems.append("kinds 必须为非空 set")
    for m in ("match", "resolve", "list_items", "fetch_meta", "fetch_content", "fetch_media_url"):
        if not callable(getattr(adapter, m, None)):
            problems.append(f"缺少方法 {m}")
    return problems


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
