"""适配器注册表：注册 / 查询 / URL 路由 / 自动发现"""
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
    """自动发现 providers 包下所有适配器模块（下划线开头/模板跳过）"""
    pkg = importlib.import_module(package)
    for m in pkgutil.iter_modules(pkg.__path__):
        if not m.name.startswith("_"):
            importlib.import_module(f"{package}.{m.name}")
