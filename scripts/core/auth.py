"""登录态自动恢复：把「取 cookie」这类非用户必须操作自动化。

- 已登录的持久 profile → headless 读取 cookie 并写文件（无窗口、快）
- 未登录 → 仅在允许交互（终端）时打开一次浏览器登录；非交互 harness 不阻塞
- 进程内按平台去重，避免同一 run 反复开浏览器
"""
import sys

_ENSURED = {}

# 认证类错误关键词（用于 pipeline 自动重试）；40362 是限流，不是登录态问题
AUTH_HINTS = ("cookie", "未登录", "需要登录", "登录态", "登录失效",
              "安全验证", "432", "401")


def looks_like_auth_error(exc) -> bool:
    msg = str(exc or "")
    if "40362" in msg:
        return False
    low = msg.lower()
    return any(h.lower() in low for h in AUTH_HINTS)


def _do_export(platform: str, interactive: bool) -> bool:
    import cookie_export
    return bool(cookie_export.auto_export(platform, interactive=interactive))


def ensure(platform: str, interactive: bool = None) -> bool:
    """确保平台登录态：已尝试过直接返回结果；默认仅在交互终端允许弹窗登录"""
    if platform in _ENSURED:
        return _ENSURED[platform]
    if interactive is None:
        try:
            interactive = bool(sys.stdin.isatty())
        except Exception:
            interactive = False
    try:
        ok = bool(_do_export(platform, interactive))
    except Exception:
        ok = False
    _ENSURED[platform] = ok
    return ok


def reset() -> None:
    """清空去重缓存（测试/长驻调用方在手动登录后调用）"""
    _ENSURED.clear()
