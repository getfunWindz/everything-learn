# -*- coding: utf-8 -*-
"""D1: 收藏夹批处理 favs-scan
- 拉取收藏夹全部视频（自动分页）+ 元信息（播放/UP主）
- 关键词过滤（AI 相关性等主题聚类）
- 播放量优先级排序（建议处理顺序）
- 失效/不可见视频统计（声明数 vs 实际返回数）
- 快照保存（JSON，供后续增量对比）

过滤/聚类/排序/快照实现已提炼到 core.scan（全平台共用，本模块保持兼容导出）。
"""
import time

from core.scan import AI_KEYWORDS, filter_items, sort_by_priority
from core.scan import make_snapshot as _make_snapshot


def make_snapshot(items: list, out_dir: str = "") -> str:
    """favs 兼容包装：快照文件前缀保持 favs（原有行为不变）"""
    return _make_snapshot(items, out_dir=out_dir, prefix="favs")


def scan_favorites(client, media_id: int, declared_count: int = 0,
                   ps: int = 20) -> list:
    """拉取收藏夹全部视频 → [{bvid,title,duration,view,owner,desc}]（自动分页 + 元信息）"""
    items, pn = [], 1
    while True:
        page = client.get_fav_medias(media_id, pn=pn, ps=ps)
        if not page:
            break
        items.extend(page)
        if len(page) < ps:
            break
        pn += 1
    # 补充元信息（播放量/UP主），单条失败不影响整体
    for it in items:
        try:
            info = client.get_video_info(it["bvid"])
            it["view"] = info.stat.get("view", 0)
            it["owner"] = info.owner
            it["desc"] = (info.desc or "")[:100]
        except Exception:
            it["view"], it["owner"], it["desc"] = 0, "", ""
        time.sleep(0.3)
    return items


def cmd_favs_scan(args, client=None) -> None:
    """bili favs-scan <fav_id> [--filter kw1,kw2] [--priority] [--out 目录] [--snapshot]"""
    from providers.bilibili import ApiClient
    client = client or ApiClient()
    folders = client.get_fav_folders()
    fav = args.fav
    folder = None
    if fav.isdigit():
        folder = next((f for f in folders if str(f["id"]) == fav), None)
    else:
        folder = next((f for f in folders if fav in f["title"]), None)
    if not folder:
        raise SystemExit(f"未找到收藏夹：{fav}（现有：{', '.join(f['title'] for f in folders)}）")

    print(f"收藏夹「{folder['title']}」声明 {folder.get('media_count', '?')} 个视频，正在拉取…")
    items = scan_favorites(client, folder["id"], folder.get("media_count", 0))
    declared = folder.get("media_count", len(items))
    print(f"实际获取 {len(items)} 个（{declared - len(items)} 个失效/不可见）")

    keywords = [k.strip() for k in (args.filter or ",".join(AI_KEYWORDS)).split(",") if k.strip()]
    hit = filter_items(items, keywords)
    print(f"\n命中主题（{len(hit)}/{len(items)} 个）：")
    ranked = sort_by_priority(hit) if args.priority else hit
    for i, it in enumerate(ranked, 1):
        view = f"{it['view']/10000:.1f}万" if it.get("view", 0) >= 10000 else it.get("view", 0)
        print(f"{i:3d}. [{view}播放] {it['title']}（{it.get('owner','?')}）{it['bvid']}")

    if args.snapshot:
        path = make_snapshot(items, out_dir=args.out or "")
        print(f"\n快照已保存：{path}")
