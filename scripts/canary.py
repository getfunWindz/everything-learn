"""真实平台 canary：定期探测 12 平台是否仍可用（离线 CI 替代不了）。

用法：
    python scripts/canary.py                 # 默认 meta 级（resolve + fetch_meta），快
    python scripts/canary.py --full          # 额外拉取内容（慢）
    python scripts/canary.py --only v2ex,zhihu --json
    python scripts/canary.py --out logs/canary.json

设计：每个样本用子进程隔离 + 超时；必需样本失败 → 退出码 1；可选（易风控）只记录。
建议用 cron / Windows 任务计划定期跑，不要放进 GitHub CI（网络与反爬不可控）。
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))

SAMPLES = [
    {"name": "bilibili", "required": True, "url": "https://www.bilibili.com/video/BV1164d6qEtA"},
    {"name": "zhihu", "required": True, "url": "https://zhuanlan.zhihu.com/p/62628781"},
    {"name": "weibo", "required": True, "url": "https://m.weibo.cn/status/4199408960166799"},
    {"name": "wechat_mp", "required": True, "url": "https://mp.weixin.qq.com/s/-cThzr5N2w3IEYYf-duCDA"},
    {"name": "ted", "required": True, "url": "https://www.ted.com/talks/tim_urban_inside_the_mind_of_a_master_procrastinator"},
    {"name": "medium", "required": True, "url": "https://medium.com/@dhirendrac/deepseek-v4-1-flash-is-here-whats-new-and-is-v4-pro-next-5bd6447f7e49"},
    {"name": "podcast", "required": True, "url": "https://feeds.npr.org/500005/podcast.xml"},
    {"name": "v2ex", "required": True, "url": "https://www.v2ex.com/t/1249681"},
    {"name": "youtube", "required": False, "url": "https://www.youtube.com/watch?v=arj7oStGLkU"},
    {"name": "douyin", "required": False, "url": "https://www.douyin.com/video/7279377986187431202"},
    {"name": "x_twitter", "required": False, "url": "https://x.com/NASA/status/2108563438901350773"},
    {"name": "xiaohongshu", "required": False, "url": "https://www.xiaohongshu.com/explore/6ad3819e000000001c00c564?xsec_token=AB-BR-Oeuuhv1-0gFg5VSr-8M7vOXnjYmwxqm92Pgz1Os=&xsec_source=pc_search"},
]
_BY_NAME = {s["name"]: s for s in SAMPLES}


def _probe(name: str, full: bool) -> dict:
    from core import registry
    s = _BY_NAME[name]
    registry.load_providers()
    a = registry.match_url(s["url"]) or registry.sniff_url(s["url"])
    if a is None:
        raise RuntimeError("无适配器匹配")
    ref = a.resolve(s["url"])
    meta = a.fetch_meta(ref)
    out = {"name": name, "status": "ok", "platform": a.name,
           "title": (meta.title or "")[:60], "author": (meta.author or "")[:40],
           "duration": meta.duration}
    if full:
        res = a.fetch_content(ref)
        out["content_status"] = res.status
        out["items"] = len(res.blocks or res.segments or [])
    return out


def summarize(results: list) -> dict:
    required = [r for r in results if _BY_NAME.get(r["name"], {}).get("required", True)]
    failed = [r for r in required if r.get("status") != "ok"]
    return {"ok": not failed, "total": len(results), "required": len(required),
            "failed": [r["name"] for r in failed],
            "optional_failed": [r["name"] for r in results
                                if r.get("status") != "ok" and r not in required]}


def _now() -> str:
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="真实平台 canary")
    ap.add_argument("--one", help=argparse.SUPPRESS)          # 子进程内部用
    ap.add_argument("--full", action="store_true", help="额外拉取内容")
    ap.add_argument("--only", default="", help="逗号分隔的平台名")
    ap.add_argument("--timeout", type=int, default=120, help="单样本超时秒数")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default="", help="结果 JSON 路径")
    args = ap.parse_args(argv)

    if args.one:                                              # 子进程：只输出一行 JSON
        try:
            print(json.dumps(_probe(args.one, args.full), ensure_ascii=False))
            return 0
        except Exception as e:
            print(json.dumps({"name": args.one, "status": "error",
                              "error": f"{type(e).__name__}: {str(e)[:160]}"}, ensure_ascii=False))
            return 1

    names = [n.strip() for n in args.only.split(",") if n.strip()] or [s["name"] for s in SAMPLES]
    results = []
    for name in names:
        if name not in _BY_NAME:
            results.append({"name": name, "status": "error", "error": "unknown sample"})
            continue
        cmd = [sys.executable, "-X", "utf8", __file__, "--one", name]
        if args.full:
            cmd.append("--full")
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               timeout=args.timeout)
            line = (p.stdout or "").strip().splitlines()[-1] if p.stdout.strip() else ""
            results.append(json.loads(line) if line else
                           {"name": name, "status": "error", "error": "no output"})
        except subprocess.TimeoutExpired:
            results.append({"name": name, "status": "error", "error": f"timeout {args.timeout}s"})
        except Exception as e:
            results.append({"name": name, "status": "error", "error": str(e)[:160]})

    summary = summarize(results)
    payload = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
               "full": args.full, "summary": summary, "results": results}
    out = args.out or os.path.join(_HERE, "logs", f"canary-{_now()}.json")
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        json.dump(payload, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except OSError:
        out = ""
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for r in results:
            mark = "OK " if r.get("status") == "ok" else "ERR"
            detail = r.get("title") or r.get("error") or ""
            print(f"[{mark}] {r['name']:<12} {detail[:70]}")
        print(f"\n必需失败：{summary['failed'] or '无'}；可选失败：{summary['optional_failed'] or '无'}")
        if out:
            print(f"结果：{out}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
