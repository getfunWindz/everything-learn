"""共享契约 harness：所有适配器（含自进化生成的）都必须通过 run_contract"""
import inspect


def run_contract(adapter) -> list:
    """对任意适配器执行契约检查；返回问题列表（空 = 通过）"""
    problems = []
    if not getattr(adapter, "name", ""):
        problems.append("缺少 name")
    if not isinstance(getattr(adapter, "kinds", None), set) or not adapter.kinds:
        problems.append("kinds 必须为非空 set")
    for m in ("match", "resolve", "list_items", "fetch_meta", "fetch_content", "fetch_media_url"):
        if not callable(getattr(adapter, m, None)):
            problems.append(f"缺少方法 {m}")
    fm = getattr(adapter, "fetch_media_url", None)
    if callable(fm):
        try:
            params = inspect.signature(fm).parameters
            var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
            if not var_kw and not {"media", "lowest"} <= set(params):
                problems.append("fetch_media_url 需接受 media/lowest 关键字参数"
                                "（统一多模态复检通道：core 用 media='video', lowest=True 抽帧）")
        except (TypeError, ValueError):
            pass
    return problems
