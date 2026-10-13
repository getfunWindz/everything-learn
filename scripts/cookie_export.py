"""cookie 一键导出（标配）：规格由适配器声明（providers/<platform>.py 的 COOKIE_EXPORT），本脚本驱动浏览器流程。

用法：
    python -X utf8 scripts/cookie_export.py <platform> [--browser msedge|chrome] [--timeout 300]
    python -X utf8 scripts/cookie_export.py --list
    # 统一入口：python -X utf8 scripts/el.py cookie <platform>

- 平台差异（登录入口/读取域/就绪 cookie/挑战页）全部写在 provider 模块里（见 ADAPTER_SPEC §4）；
  本文件零平台硬编码，新平台 = 只加 providers/<name>.py 的 COOKIE_EXPORT。
- 依赖 playwright（可选）：pip install playwright；无需 playwright install（复用系统 Edge/Chrome）。
- 首次运行打开新窗口，登录一次即可；profile 存于 scripts/.browser_profile_<browser>（gitignored）。
"""
import argparse
import importlib
import os
import pkgutil
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REQUIRED_FIELDS = ("url", "read_url", "required", "ready", "wanted", "file")
_PLATFORMS = None


def _valid_spec(spec) -> bool:
    if not (isinstance(spec, dict) and all(spec.get(k) for k in _REQUIRED_FIELDS)):
        return False
    v = spec.get("verify")
    if v is not None:
        if not (isinstance(v, dict) and v.get("url") and v.get("expect")):
            return False
    return True


def discover_platforms() -> dict:
    """扫描 providers/*.py 收集 COOKIE_EXPORT（下划线模块跳过；导入失败/字段不全跳过）"""
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)
    import providers as pkg
    found = {}
    for m in pkgutil.iter_modules(pkg.__path__):
        if m.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"providers.{m.name}")
        except Exception:
            continue
        spec = getattr(mod, "COOKIE_EXPORT", None)
        if _valid_spec(spec):
            found[m.name] = spec
    return found


def load_platforms(refresh: bool = False) -> dict:
    global _PLATFORMS
    if refresh or _PLATFORMS is None:
        _PLATFORMS = discover_platforms()
    return _PLATFORMS


def build_cookie_string(pairs) -> str:
    return "; ".join(f"{k}={v}" for k, v in pairs)


def required_present(cookies: dict, required) -> bool:
    return all(cookies.get(name) for name in required)


def cookie_file_for(platform: str) -> str:
    platforms = load_platforms()
    if platform not in platforms:
        avail = "/".join(sorted(platforms)) or "（无，需在 provider 模块声明 COOKIE_EXPORT）"
        print(f"不支持的平台：{platform}（已声明导出的平台：{avail}）", file=sys.stderr)
        raise SystemExit(2)
    return os.path.join(_HERE, platforms[platform]["file"])


def _read_cookies(ctx, url):
    """读 cookie；窗口被关闭（context 已死）返回 None"""
    try:
        return {c["name"]: c["value"] for c in ctx.cookies(url)}
    except Exception:
        return None


def _wait_for_verify(page, spec: dict, deadline: float):
    """页面内 fetch verify.url（带凭据），轮询直到响应包含 expect 子串。
    返回 (True, "") / (False, 可读错误)。用于「游客也有关键 cookie」的平台（如微博 SUB）。"""
    expr = ("async (u) => { const r = await fetch(u, {credentials:'include'});"
            " return await r.text(); }")
    while time.time() < deadline:
        try:
            text = page.evaluate(expr, spec["url"]) or ""
        except Exception:
            text = ""
        if spec["expect"] in text:
            return True, ""
        try:
            page.wait_for_timeout(2000)
        except Exception:
            time.sleep(2)
    return False, ("登录校验未通过：未检测到有效登录态（可能是游客 cookie）。"
                   "请确认已在窗口内完成登录后重试；本次未写入 cookie 文件。")


def _write_cookie_file(out: str, cfg: dict, cookies: dict) -> int:
    wanted = cfg["wanted"]
    if "*" in wanted:                      # 全量导出（风控 JS 生成的 cookie 集不确定时用）
        pairs = [(n, cookies[n]) for n in sorted(cookies)]
    else:
        pairs = [(n, cookies[n]) for n in wanted if cookies.get(n)]
    with open(out, "w", encoding="utf-8") as f:
        f.write(build_cookie_string(pairs) + "\n")
    print(f"已写入 {len(pairs)} 个 cookie → {out}（{', '.join(n for n, _ in pairs)}）")
    return 0


def export(platform: str, browser: str = "msedge", timeout: int = 300) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("未安装 playwright：请先执行  pip install playwright\n"
              "（不需要 playwright install，直接复用系统 Edge/Chrome）", file=sys.stderr)
        return 2
    cfg = load_platforms().get(platform)
    if cfg is None:
        cookie_file_for(platform)   # 复用统一报错（不支持的平台 → SystemExit 2）
        return 2
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
                # probe_url：完整加载一个内容页（触发风控 JS，生成完整/新鲜 cookie 集）
                if cfg.get("probe_url"):
                    try:
                        page.goto(cfg["probe_url"], wait_until="domcontentloaded")
                        page.wait_for_timeout(4000)   # 等风控脚本写入 cookie（后续 ready 轮询兑底）
                    except Exception:
                        pass
                cookies = _read_cookies(ctx, cfg["read_url"]) or cookies
                if not required_present(cookies, cfg["ready"]):
                    cookies, err = wait_for(cfg["ready"], cfg.get("probe_url") or cfg["read_url"],
                                            "若窗口内出现安全验证，请手动完成一次后重试。")
                    if cookies is None:
                        print(err, file=sys.stderr)
                        return 3
                if cfg.get("verify"):
                    try:
                        page.goto(cfg.get("probe_url") or cfg["url"],
                                  wait_until="domcontentloaded")
                    except Exception:
                        pass
                    verified, verr = _wait_for_verify(page, cfg["verify"], deadline)
                    if not verified:
                        print(verr, file=sys.stderr)
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


def main(argv=None):
    ap = argparse.ArgumentParser(description="cookie 一键导出（Playwright + 系统浏览器）")
    ap.add_argument("platform", nargs="?", help="平台标识（如 zhihu/bilibili）")
    ap.add_argument("--list", action="store_true", help="列出已声明 cookie 导出的平台")
    ap.add_argument("--browser", default="msedge", choices=["msedge", "chrome"])
    ap.add_argument("--timeout", type=int, default=300, help="等待登录秒数（默认 300）")
    args = ap.parse_args(argv)
    if args.list:
        for name, spec in sorted(load_platforms().items()):
            print(f"{name:>10} | {spec['file']:>16} | {spec['url']}")
        return 0
    if not args.platform:
        ap.error("需要平台标识（或用 --list 查看可用平台）")
    return export(args.platform, args.browser, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
