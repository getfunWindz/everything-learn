import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from core.scan import (AI_KEYWORDS, refs_to_items, filter_items, group_by_theme,
                       sort_by_priority, make_snapshot)
from core.models import ItemRef


def test_refs_to_items_normalizes():
    refs = [ItemRef(platform="x", item_id="a1", url="u1", sub_id="1", title="大模型入门",
                    extra={"desc": "LLM 教程", "view": 1200}),
            ItemRef(platform="x", item_id="a2", url="u2", sub_id="2", title="吉他谱",
                    extra={"like": 30})]
    items = refs_to_items(refs)
    assert items[0]["title"] == "大模型入门" and items[0]["view"] == 1200
    assert items[1]["view"] == 30          # like 兜底为热度


def test_filter_and_priority():
    items = [{"title": "GPT 教程", "desc": "", "view": 100},
             {"title": "音乐现场", "desc": "", "view": 900},
             {"title": "深度学习", "desc": "transformer", "view": 500}]
    hit = filter_items(items, ["gpt", "transformer"])
    assert [i["title"] for i in hit] == ["GPT 教程", "深度学习"]
    ranked = sort_by_priority(hit)
    assert [i["view"] for i in ranked] == [500, 100]


def test_group_by_theme_and_snapshot(tmp_path):
    items = [{"title": "篮球教学", "desc": "", "view": 1},
             {"title": "LLM 部署", "desc": "", "view": 2}]
    groups = group_by_theme(items)
    assert len(groups["体育"]) == 1 and len(groups["AI/编程"]) == 1
    path = make_snapshot(items, out_dir=str(tmp_path), prefix="zhihu")
    assert path.endswith(".json") and "zhihu_snapshot_" in os.path.basename(path)
    assert json.load(open(path, encoding="utf-8"))["count"] == 2
