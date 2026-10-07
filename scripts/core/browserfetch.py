"""浏览器辅助取数（Cloudflare 强风控平台）：Playwright 有头 + 持久化 profile。

- profile 与 cookie 导出共用 scripts/.browser_profile_<browser>（可积累 CF 信任）；
  首次可能弹窗，CF 交互挑战可由用户一次性通过；默认窗口移至屏外避免打扰。
- 单例上下文：一次运行内复用，减少挑战次数；失败退避重试并抛可读异常。
- 懒加载 playwright；未安装时给出安装提示。
"""
import json
import os
import time

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # scripts/
_OFFSCREEN = ["--window-position=-2600,-2600", "--window-size=1200,800",
              "--disable-blink-features=AutomationControlled"]
_ctx = {"pw": None, "context": None, "page": None}


class BrowserFetchError(ValueError):
    """浏览器取数失败（CF 挑战/超时/结构变化）"""


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
        raise BrowserFetchError(f"响应不是 JSON：{str(e)[:80]}")


def _ensure_page(browser: str = "msedge"):
    if _ctx["page"] is not None:
        return _ctx["page"]
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise BrowserFetchError("未安装 playwright：pip install playwright（无需 playwright install）")
    profile = os.path.join(_HERE, f".browser_profile_{browser}")
    pw = sync_playwright().start()
    try:
        ctx = pw.chromium.launch_persistent_context(profile, channel=browser, headless=False,
                                                    args=_OFFSCREEN)
    except Exception as e:
        pw.stop()
        raise BrowserFetchError(f"浏览器启动失败：{str(e)[:120]}")
    _ctx.update(pw=pw, context=ctx, page=(ctx.pages[0] if ctx.pages else ctx.new_page()))
    return _ctx["page"]


def fetch_json(url: str, warm_url: str = None, attempts: int = 2, retry_wait: float = 8.0) -> dict:
    """浏览器内 fetch(url) → JSON；warm_url 先访问（过 CF 挑战）；失败退避重试"""
    last = None
    for i in range(max(1, attempts)):
        try:
            page = _ensure_page()
            if warm_url:
                page.goto(warm_url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3000)
            result = page.evaluate(
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


def shutdown():
    """关闭浏览器（进程退出/测试用）"""
    try:
        if _ctx["context"]:
            _ctx["context"].close()
        if _ctx["pw"]:
            _ctx["pw"].stop()
    except Exception:
        pass
    _ctx.update(pw=None, context=None, page=None)
