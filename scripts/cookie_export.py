"""cookie 一键导出：Playwright 打开系统浏览器（项目专用 profile）→ 登录 → 写入 cookie 文件。

用法：
    python -X utf8 scripts/cookie_export.py zhihu [--browser msedge|chrome] [--timeout 300]

- 依赖 playwright（可选）：pip install playwright；无需 playwright install（复用系统 Edge/Chrome）。
- 首次运行会打开新窗口，登录一次即可；profile 存于 scripts/.browser_profile_<browser>（gitignored）。
- 已登录状态下再运行：几秒内自动导出（可反复使用，cookie 失效时重跑即可）。
"""
import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))

PLATFORMS = {
    "zhihu": {
        "url": "https://www.zhihu.com/signin",
        "read_url": "https://www.zhihu.com",
        "probe_url": "https://zhuanlan.zhihu.com/p/62628781",   # 内容页：浏览器会自动过挑战并生成 __zse_ck
        "domain": ".zhihu.com",
        "required": ["z_c0"],
        "ready": ["z_c0", "__zse_ck"],
        "wanted": ["d_c0", "__zse_ck", "z_c0"],
        "file": ".zhihu_cookie",
    },
    "bilibili": {
        "url": "https://passport.bilibili.com/login",
        "read_url": "https://www.bilibili.com",
        "probe_url": "",
        "domain": ".bilibili.com",
        "required": ["SESSDATA"],
        "ready": ["SESSDATA"],
        "wanted": ["SESSDATA", "bili_jct", "buvid3"],
        "file": ".bili_cookie",
    },
}


def build_cookie_string(pairs) -> str:
    return "; ".join(f"{k}={v}" for k, v in pairs)


def required_present(cookies: dict, required) -> bool:
    return all(cookies.get(name) for name in required)


def cookie_file_for(platform: str) -> str:
    if platform not in PLATFORMS:
        print(f"不支持的平台：{platform}（可选：{'/'.join(sorted(PLATFORMS))}）", file=sys.stderr)
        raise SystemExit(2)
    return os.path.join(_HERE, PLATFORMS[platform]["file"])


def _read_cookies(ctx, url):
    """读 cookie；窗口被关闭（context 已死）返回 None"""
    try:
        return {c["name"]: c["value"] for c in ctx.cookies(url)}
    except Exception:
        return None


def _write_cookie_file(out: str, cfg: dict, cookies: dict) -> int:
    pairs = [(n, cookies[n]) for n in cfg["wanted"] if cookies.get(n)]
    with open(out, "w", encoding="utf-8") as f:
        f.write(build_cookie_string(pairs) + "\n")
    print(f"已写入 {len(pairs)} 个 cookie → {out}（{', '.join(n for n, _ in pairs)}）")
    return 0


def export(platform: str, browser: str, timeout: int) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("未安装 playwright：请先执行  pip install playwright\n"
              "（不需要 playwright install，直接复用系统 Edge/Chrome）", file=sys.stderr)
        return 2
    cfg = PLATFORMS[platform]
    profile = os.path.join(_HERE, f".browser_profile_{browser}")
    out = cookie_file_for(platform)
    print(f"打开 {browser}（首次需在窗口内登录 {platform}；已登录则自动导出）...")
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                profile, channel=browser, headless=False,
                args=["--disable-blink-features=AutomationControlled"])
            deadline = time.time() + timeout
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()

                def wait_for(names, nav_url, hint):
                    """轮询直到 names 全部出现；返回 (cookies, "") 或 (None, 错误原因)"""
                    if nav_url:
                        try:
                            page.goto(nav_url, wait_until="domcontentloaded")
                        except Exception:
                            pass
                    while True:
                        cookies = _read_cookies(ctx, cfg["read_url"])
                        if cookies is None:
                            return None, "浏览器窗口已被关闭，导出中止（重新运行即可）。"
                        if required_present(cookies, names):
                            return cookies, ""
                        if time.time() >= deadline:
                            missing = [n for n in names if not cookies.get(n)]
                            return None, f"超时：未获取到 {'/'.join(missing)}。{hint}"
                        print(f"等待中...（{'/'.join(names)}）", file=sys.stderr)
                        try:
                            page.wait_for_timeout(2000)
                        except Exception:
                            time.sleep(2)   # 页面被关但浏览器还在：退化为轮询

                cookies = _read_cookies(ctx, cfg["read_url"]) or {}
                if not required_present(cookies, cfg["required"]):
                    cookies, err = wait_for(cfg["required"], cfg["url"],
                                            "请完成登录（窗口需保持打开）。")
                    if cookies is None:
                        print(err, file=sys.stderr)
                        return 3
                if not required_present(cookies, cfg["ready"]):
                    # 登录已就绪 → 打开内容页：真实浏览器会自动过风控挑战并生成 __zse_ck（实测 3s 内）
                    nav = cfg.get("probe_url") or cfg["read_url"]
                    cookies, err = wait_for(cfg["ready"], nav,
                                            "若窗口内出现安全验证，请手动完成一次后重试。")
                    if cookies is None:
                        print(err, file=sys.stderr)
                        return 3
                return _write_cookie_file(out, cfg, cookies)
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
    except Exception as e:
        print(f"浏览器启动/运行失败：{type(e).__name__}: {str(e)[:160]}\n"
              f"提示：确认已安装 {browser}（或改用 --browser chrome），并先执行 pip install playwright",
              file=sys.stderr)
        return 4


def main():
    ap = argparse.ArgumentParser(description="cookie 一键导出（Playwright + 系统浏览器）")
    ap.add_argument("platform", choices=sorted(PLATFORMS))
    ap.add_argument("--browser", default="msedge", choices=["msedge", "chrome"])
    ap.add_argument("--timeout", type=int, default=300, help="等待登录秒数（默认 300）")
    args = ap.parse_args()
    raise SystemExit(export(args.platform, args.browser, args.timeout))


if __name__ == "__main__":
    main()
