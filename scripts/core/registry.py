"""适配器注册表：注册 / 查询 / URL 路由 / 自动发现"""
import builtins
import importlib
import pkgutil

_ADAPTERS = {}


def register(adapter_cls):
    """类装饰器：实例化并注册（同名覆盖）"""
    _ADAPTERS[adapter_cls.name] = adapter_cls()
    return adapter_cls


def get(name: str):
    return _ADAPTERS.get(name)


def all():
    return list(_ADAPTERS.values())


def match_url(url: str):
    for a in _ADAPTERS.values():
        try:
            if a.match(url):
                return a
        except Exception:
            continue
    return None


def default():
    """无 URL 匹配时的兜底适配器（M0 = bilibili，供名称搜索用）"""
    return _ADAPTERS.get("bilibili")


def reset():
    _ADAPTERS.clear()


def load_providers(package: str = "providers"):
    """发现 providers 包下所有适配器类并注册（下划线开头模块跳过）。
    扫描注册而非依赖 import 副作用：注册表被 reset 后仍然自愈。"""
    pkg = importlib.import_module(package)
    required = ("match", "resolve", "list_items", "fetch_meta", "fetch_content", "fetch_media_url")
    for m in pkgutil.iter_modules(pkg.__path__):
        if m.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{package}.{m.name}")
        for obj in vars(mod).values():
            if (isinstance(obj, type) and getattr(obj, "name", None)
                    and isinstance(getattr(obj, "kinds", None), set)
                    and builtins.all(callable(getattr(obj, r, None)) for r in required)):
                register(obj)
