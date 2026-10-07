"""共享契约 harness：所有适配器（含自进化生成的）都必须通过 run_contract"""


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
    return problems
