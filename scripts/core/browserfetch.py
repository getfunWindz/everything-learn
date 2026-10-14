"""浏览器辅助取数（Cloudflare 强风控平台 + 签名平台账号级取数）：Playwright 有头 + 持久化 profile。

- profile 与 cookie 导出共用 scripts/.browser_profile_<browser>（可积累 CF 信任与登录态）；
  首次可能弹窗，挑战/登录可由用户一次性通过；默认窗口移至屏外避免打扰。
- 进程级单例（同一 profile 单浏览器实例）；同一运行内复用，减少挑战次数。
- BrowserSession：platform 决定缓速节奏；interactive=True 窗口可见（登录/验证码用）；
  提供 goto / fetch_json / 原语扩展点，各平台只写薄脚本。
- 懒加载 playwright；未安装时给出安装提示。
"""
import json
import os
import sys
import time
import atexit

from core import throttle

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # scripts/
_STEALTH = ["--disable-blink-features=AutomationControlled"]
_OFFSCREEN = _STEALTH + ["--window-position=-2600,-2600", "--window-size=1200,800"]
_VISIBLE = _STEALTH + ["--window-position=80,80", "--window-size=1280,900",
                       "--no-first-run", "--no-default-browser-check"]
_ctx = {"pw": None, "context": None, "page": None, "interactive": None}


def _launch_args(interactive: bool) -> list:
    """启动参数：可见模式必须显式给屏内位置——profile 会记忆上一次的离屏位置"""
    return list(_VISIBLE) if interactive else list(_OFFSCREEN)


class BrowserFetchError(ValueError):
    """浏览器取数失败。kind: blocked(CF/风控) / structure_changed(结构或未登录)
    / login_expired / timeout / risk_control"""

    def __init__(self, msg, kind="blocked"):
        super().__init__(msg)
        self.kind = kind


def strip_json_prefix(text: str) -> str:
    """Medium/CF 常在 JSON 前加 )]}while(1);</x> 等前缀 → 从第一个 { 截取"""
    i = (text or "").find("{")
    return text[i:] if i >= 0 else (text or "")


def parse_fetch_result(result: dict) -> dict:
    """浏览器内 fetch 的 {status,text} → dict（失败抛可读异常）"""
    status, text = int(result.get("status") or 0), result.get("text") or ""
    if status != 200 or "Just a moment" in text or "Attention Required" in text:
        raise BrowserFetchError(f"被 Cloudflare 拦截或响应异常（HTTP {status}）")
    try:
        return json.loads(strip_json_prefix(text))
    except Exception as e:
        raise BrowserFetchError(f"响应不是 JSON：{str(e)[:80]}", kind="structure_changed")


def _ensure_visible(page):
    """CDP 强制窗口归位屏内（双保险：覆盖 profile 记忆的离屏位置）"""
    try:
        cdp = page.context.new_cdp_session(page)
        win = cdp.send("Browser.getWindowForTarget")
        cdp.send("Browser.setWindowBounds", {
            "windowId": win["windowId"],
            "bounds": {"left": 80, "top": 80, "width": 1280, "height": 900,
                       "windowState": "normal"}})
    except Exception:
        pass


def _launch(browser: str, interactive: bool) -> dict:
    """真实启动浏览器（测试可注入等价 launcher 替换）"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise BrowserFetchError("未安装 playwright：pip install playwright（无需 playwright install）")
    profile = os.path.join(_HERE, f".browser_profile_{browser}")
    args = _launch_args(interactive)
    pw = sync_playwright().start()
    try:
        ctx = pw.chromium.launch_persistent_context(profile, channel=browser, headless=False,
                                                    args=args)
    except Exception as e:
        pw.stop()
        raise BrowserFetchError(f"浏览器启动失败：{str(e)[:120]}")
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    if interactive:
        _ensure_visible(page)
    return {"pw": pw, "context": ctx, "page": page}


def _ensure_page(browser="msedge", interactive=False, launcher=None):
    if _ctx["page"] is not None:
        if _ctx.get("interactive") == interactive:
            return _ctx["page"]
        shutdown()                                     # 离屏↔可见 模式切换 → 重启
        print("  · 浏览器启动模式切换（离屏↔可见），已重启", file=sys.stderr)
    launch = launcher or _launch
    parts = launch(browser, interactive)
    _ctx.update(parts)
    _ctx["interactive"] = interactive
    return _ctx["page"]


class BrowserSession:
    """浏览器取数会话：进程级单例复用；platform 决定缓速；interactive=True 窗口可见。"""

    def __init__(self, platform="default", browser="msedge", interactive=False, launcher=None):
        self.platform = platform
        self.browser = browser
        self.interactive = interactive
        self._launcher = launcher                        # 测试注入 seam
        self._page = None

    @property
    def page(self):
        if self._page is None:
            self._page = _ensure_page(self.browser, self.interactive, self._launcher)
        return self._page

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False                                     # 单例由 shutdown() 统一关闭

    def goto(self, url, wait_until="domcontentloaded"):
        throttle.wait(self.platform)
        self.page.goto(url, wait_until=wait_until, timeout=45000)

    def evaluate(self, js):
        return self.page.evaluate(js)

    def fetch_json(self, url, warm_url=None, attempts=2, retry_wait=8.0) -> dict:
        """浏览器内 fetch(url) → JSON；warm_url 先访问（过 CF 挑战）；失败退避重试"""
        last = None
        for i in range(max(1, attempts)):
            try:
                if warm_url:
                    self.goto(warm_url)
                    self.page.wait_for_timeout(3000)
                throttle.wait(self.platform)
                result = self.page.evaluate(
                    "async (u) => { const r = await fetch(u, {credentials:'include'});"
                    " return {status: r.status, text: (await r.text()).slice(0, 2000000)}; }", url)
                return parse_fetch_result(result)
            except BrowserFetchError as e:
                last = e
            except Exception as e:
                last = BrowserFetchError(str(e)[:120])
            if i < attempts - 1:
                time.sleep(retry_wait)
        raise last

    def tap_json(self, url_pattern, action=None, limit=50, settle_ms=1500, min_results=1) -> list:
        """旁路拦截：监听网络响应，执行 action（滚动/切页签/等待），收集 URL 匹配的 JSON。

        签名请求由页面自身发出（不逆向签名）；未捕获到任何匹配响应时抛
        structure_changed（未登录/接口路径变化/风控空转）。"""
        import re as _re
        collected = []

        def _on_response(resp):
            try:
                if len(collected) >= limit:
                    return
                if not _re.search(url_pattern, getattr(resp, "url", "") or ""):
                    return
                collected.append(resp.json())
            except Exception:
                pass

        self.page.on("response", _on_response)
        try:
            if action:
                action()
            self.page.wait_for_timeout(settle_ms)
        finally:
            self.page.remove_listener("response", _on_response)
        if len(collected) < max(0, min_results):
            raise BrowserFetchError(
                f"未捕获到匹配 {url_pattern} 的 JSON 响应（可能未登录/接口变化/风控）",
                kind="structure_changed")
        return collected

    def scroll(self, times=1, step_px=800):
        """人类化滚动：每次滚动后按平台缓速间隔等待（间隔含抖动）"""
        for _ in range(max(0, times)):
            self.page.evaluate(f"window.scrollBy(0, {int(step_px)})")
            throttle.wait(self.platform)

    def scroll_wheel(self, times=1, step_px=3000, x=640, y=500):
        """真实滚轮滚动（对虚拟化列表/懒加载可靠，能触发页面自身分页请求）"""
        try:
            self.page.mouse.move(int(x), int(y))
        except Exception:
            pass
        for _ in range(max(0, times)):
            self.page.mouse.wheel(0, int(step_px))
            throttle.wait(self.platform)

    def click(self, selector, timeout=5000):
        """点击元素（Playwright 自带等待；找不到抛异常由调用方转可读错误）"""
        self.page.click(selector, timeout=timeout)

    def pause(self, ms):
        """显式等待（页面水合/动画/轮询间隔用）"""
        self.page.wait_for_timeout(int(ms))

    def html(self) -> str:
        """当前页面完整 HTML（SSR 解析用）"""
        return self.page.content()

    def has_cookie(self, name) -> bool:
        """cookie 存在性（httpOnly 可见；登录探针用）"""
        try:
            return any(c.get("name") == name for c in self.page.context.cookies())
        except Exception:
            return False

    def login_probe(self, ok_marker=None, bad_marker=None) -> bool:
        """登录态探针：bad_marker 命中 → False；ok_marker 未命中 → False；都不传 → True"""
        html = self.page.content()
        if bad_marker and bad_marker in html:
            return False
        if ok_marker and ok_marker not in html:
            return False
        return True


def fetch_json(url: str, warm_url: str = None, attempts: int = 2, retry_wait: float = 8.0) -> dict:
    """兼容入口（Medium 用）：等价 BrowserSession('default').fetch_json(...)"""
    with BrowserSession("default") as s:
        return s.fetch_json(url, warm_url=warm_url, attempts=attempts, retry_wait=retry_wait)


def shutdown():
    """关闭浏览器（进程退出/测试用）"""
    try:
        if _ctx["context"]:
            _ctx["context"].close()
        if _ctx["pw"]:
            _ctx["pw"].stop()
    except Exception:
        pass
    _ctx.update(pw=None, context=None, page=None, interactive=None)


atexit.register(shutdown)   # 护栏：解释器退出/异常退出时自动关闭浏览器，避免残留
