"""生成适配器契约测试：python scripts/gen_contract_test.py <adapter_name> [--force]

- 输出 tests/providers/test_<name>.py（已存在则不覆盖，除非 --force）。
- 生成内容只做契约校验（run_contract）；fixture/真实冒烟仍需按 ADAPTER_TEMPLATE 补。
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR = os.path.join(os.path.dirname(_HERE), "tests", "providers")

_TEMPLATE = '''# -*- coding: utf-8 -*-
"""契约测试（自动生成）：{name} 适配器

补全建议（按 docs/ADAPTER_TEMPLATE.md）：
1. tests/fixtures/{name}/ 取样（列表/详情/字幕/正文）
2. 纯函数解析单测（不联网）
3. 真实链接冒烟记录（风控平台如实记录失败）
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import pytest
from core import registry
from tests.contract.harness import run_contract


def _adapter():
    registry.load_providers()
    for a in registry.all():
        if a.name == "{name}":
            return a
    pytest.skip("适配器 {name} 未注册（先实现 providers/{name}.py）")


def test_contract_{name}():
    assert run_contract(_adapter()) == []
'''


def render(name: str) -> str:
    return _TEMPLATE.format(name=name)


def generate(name: str, dest_dir: str = None, force: bool = False) -> str:
    name = (name or "").strip()
    if not name or not name.replace("_", "").isalnum():
        raise ValueError("适配器名需为字母/数字/下划线")
    dest_dir = dest_dir or TESTS_DIR
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, f"test_{name}.py")
    if os.path.exists(path) and not force:
        raise FileExistsError(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render(name))
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成适配器契约测试")
    ap.add_argument("name", help="适配器名（与 provider 的 name 一致）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的测试文件")
    ap.add_argument("--out-dir", default=None, help="输出目录（默认 tests/providers）")
    args = ap.parse_args(argv)
    try:
        path = generate(args.name, args.out_dir, args.force)
    except FileExistsError as e:
        print(f"已存在：{e}（加 --force 覆盖）", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"参数错误：{e}", file=sys.stderr)
        return 2
    print(f"已生成：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
