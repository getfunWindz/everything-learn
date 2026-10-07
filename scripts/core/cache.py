"""核心缓存：按 (platform, item_id, sub_id) 保存字幕/转写结果到 scripts/cache/"""
import json
import os

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")


def _cache_path(platform: str, item_id: str, sub_id: str = "") -> str:
    name = f"{platform}_{item_id}"
    if sub_id:
        name += f"_{sub_id}"
    return os.path.join(CACHE_DIR, name + ".json")


def get_cached(platform: str, item_id: str, sub_id: str = ""):
    """命中缓存 → [{start,end,text}]；未命中/损坏 → None"""
    path = _cache_path(platform, item_id, sub_id)
    if not os.path.exists(path):
        return None
    try:
        return json.load(open(path, encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def set_cached(platform: str, item_id: str, sub_id: str, lines: list) -> None:
    """写入缓存（原子写：先写临时文件再改名，防中断损坏）"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = _cache_path(platform, item_id, sub_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lines, f, ensure_ascii=False)
    os.replace(tmp, path)


def clear_cache(platform: str = "", item_id: str = "", sub_id: str = "") -> int:
    """清空全部或指定缓存；返回删除文件数"""
    if platform:
        p = _cache_path(platform, item_id, sub_id)
        if os.path.exists(p):
            os.remove(p)
            return 1
        return 0
    n = 0
    if os.path.isdir(CACHE_DIR):
        for f in os.listdir(CACHE_DIR):
            if f.endswith(".json"):
                os.remove(os.path.join(CACHE_DIR, f))
                n += 1
    return n
