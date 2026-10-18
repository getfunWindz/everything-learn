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
import json
import os
import pkgutil
import sys
import time

try:                                    # GBK 控制台不因 emoji/符号崩溃
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

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


def parse_cookie_text(text: str) -> dict:
    """解析多种 cookie 文本：`k=v; k2=v2`、Netscape cookies.txt、JSON（dict/list）"""
    text = (text or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            if all(isinstance(v, str) for v in data.values()):
                return {str(k): v for k, v in data.items()}
            if isinstance(data.get("cookies"), list):
                data = data["cookies"]
        if isinstance(data, list):
            out = {}
            for it in data:
                if isinstance(it, dict) and it.get("name") is not None:
                    out[str(it["name"])] = str(it.get("value", ""))
            return out
    except Exception:
        pass
    cookies = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("	")
        if len(parts) >= 7:                         # Netscape cookies.txt
            cookies[parts[5]] = parts[6]
            continue
        for kv in line.split(";"):
            if "=" not in kv:
                continue
            k, v = kv.split("=", 1)
            k, v = k.strip(), v.strip()
            if k:
                cookies[k] = v
    return cookies


def import_cookie(platform: str, text: str) -> int:
    """从文本导入 cookie（无显示环境/CI 用）：缺 required → 2；成功 → 0"""
    cfg = load_platforms().get(platform)
    if not cfg:
        cookie_file_for(platform)                   # 复用统一报错
        return 2
    cookies = parse_cookie_text(text)
    missing = [n for n in cfg["required"] if not cookies.get(n)]
    if missing:
        print(f"导入失败：缺少必需 cookie {'/'.join(missing)}（{platform}）", file=sys.stderr)
        return 2
    if not required_present(cookies, cfg.get("ready") or []):
        lack = [n for n in (cfg.get("ready") or []) if not cookies.get(n)]
        print(f"提示：缺少就绪 cookie {'/'.join(lack)}，可能影响风控通过（仍写入）", file=sys.stderr)
    return _write_cookie_file(cookie_file_for(platform), cfg, cookies)


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
    try:
        from core.fsutil import restrict_file
        restrict_file(out)
    except Exception:
        pass
    print(f"已写入 {len(pairs)} 个 cookie → {out}（{', '.join(n for n, _ in pairs)}）")
    return 0


def _profile_dir(browser: str) -> str:
    return os.path.join(_HERE, f".browser_profile_{browser}")


def _launch_ctx(p, browser: str, headless: bool):
    """按 msedge → chrome → chromium 尝试；返回 (实际 channel, context)"""
    browser = browser or "auto"
    channels = [browser] if browser not in ("auto", "chromium") else ["msedge", "chrome", "chromium"]
    if browser == "chromium":
        channels = ["chromium"]
    last = None
    for ch in channels:
        try:
            ctx = p.chromium.launch_persistent_context(
                _profile_dir(ch), channel=ch, headless=headless,
                args=["--disable-blink-features=AutomationControlled"])
            return ch, ctx
        except Exception as e:
            last = e
    raise last or RuntimeError("没有可用的浏览器")


def _read_profile_cookies(platform: str, browser: str = "auto"):
    """headless 读取持久 profile 的 cookie（不弹窗）；不可用/未安装 → {}"""
    cfg = load_platforms().get(platform)
    if not cfg:
        return {}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {}
    try:
        with sync_playwright() as p:
            ch, ctx = _launch_ctx(p, browser, headless=True)
            try:
                return _read_cookies(ctx, cfg["read_url"]) or {}
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
    except Exception:
        return {}


def auto_export(platform: str, browser: str = "auto", interactive: bool = True,
                timeout: int = 90) -> bool:
    """自动恢复登录态：headless 命中 → 写文件；未登录 → 仅交互模式弹窗一次"""
    cfg = load_platforms().get(platform)
    if not cfg:
        return False
    cookies = _read_profile_cookies(platform, browser)
    if cookies and required_present(cookies, cfg["required"]):
        return _write_cookie_file(cookie_file_for(platform), cfg, cookies) == 0
    if not interactive:
        print(f"自动登录态未就绪：{platform}（持久 profile 未登录）。"
              f"请在终端运行一次 `el.py cookie {platform}` 完成登录；此后可全自动复用。",
              file=sys.stderr)
        return False
    print(f"未检测到 {platform} 登录态 → 打开浏览器（首次需登录一次）...", file=sys.stderr)
    return export(platform, browser, timeout) == 0


def export(platform: str, browser: str = "auto", timeout: int = 300) -> int:
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
    out = cookie_file_for(platform)
    print(f"打开浏览器（{browser}；首次需在窗口内登录 {platform}；已登录则自动导出）...")
    try:
        with sync_playwright() as p:
            _ch, ctx = _launch_ctx(p, browser, headless=False)
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
    ap.add_argument("--browser", default="auto", choices=["auto", "msedge", "chrome", "chromium"])
    ap.add_argument("--timeout", type=int, default=300, help="等待登录秒数（默认 300）")
    ap.add_argument("--auto", action="store_true",
                    help="先尝试 headless 复用已登录 profile（无窗口）；未登录才打开窗口")
    ap.add_argument("--import", dest="import_file", metavar="FILE",
                    help="从文件导入 cookie（k=v;… / Netscape / JSON；- 表示 stdin）")
    ap.add_argument("--cookie-string", default=None, help="直接传入 cookie 字符串导入")
    args = ap.parse_args(argv)
    if args.list:
        for name, spec in sorted(load_platforms().items()):
            print(f"{name:>10} | {spec['file']:>16} | {spec['url']}")
        return 0
    if not args.platform:
        ap.error("需要平台标识（或用 --list 查看可用平台）")
    if args.cookie_string or args.import_file:
        if args.cookie_string:
            text = args.cookie_string
        elif args.import_file == "-":
            text = sys.stdin.read()
        else:
            with open(args.import_file, encoding="utf-8") as f:
                text = f.read()
        return import_cookie(args.platform, text)
    if args.auto:
        return 0 if auto_export(args.platform, args.browser, interactive=True,
                                timeout=args.timeout) else 1
    return export(args.platform, args.browser, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
