"""命令行入口：跑用例、校验单个文件。

用法::

    python -m mql_spec cases/              # 跑全部用例，打印通过/失败
    python -m mql_spec cases/valid/full.json
    python -m mql_spec --schema            # 打印 JSON Schema 路径

用例约定：``cases/valid/*.json`` 的内容就是一份 MQL；``cases/invalid/*.json``
的内容是 ``{"mql": ..., "expect": "错误码", "note": "为什么这个例子重要"}``。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .static_ontology import StaticOntology
from .validator import Validator

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ONTOLOGY = ROOT / "cases" / "_ontology.json"


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_ontology() -> StaticOntology:
    if DEFAULT_ONTOLOGY.exists():
        return StaticOntology.from_dict(_load_json(DEFAULT_ONTOLOGY))
    # 没有 ontology 文件时的最小兜底，保证 CLI 仍可用
    return StaticOntology(
        metrics=("gmv", "order_count"),
        dimensions=("city", "order_date", "product_type"),
        time_properties=("order_date",),
    )


def _is_negative_case(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and "mql" in payload
        and "expect" in payload
    )


def run_one(validator: Validator, path: Path) -> tuple[bool, str]:
    """返回 (是否通过, 说明文字)。"""

    payload = _load_json(path)
    try:
        relative = path.relative_to(ROOT)
    except ValueError:
        relative = path

    if _is_negative_case(payload):
        result = validator.validate(payload["mql"])
        expected = payload["expect"]
        got = [e["code"] for e in result["errors"]]
        if expected in got:
            return True, f"{relative}  →  {expected}"
        where = payload.get("note", "")
        return False, (
            f"{relative}  →  期望 {expected}，实际 {got or '[]（校验通过）'}"
            + (f"\n        用例说明: {where}" if where else "")
        )

    result = validator.validate(payload)
    if result["ok"]:
        return True, f"{relative}  →  合法"
    codes = [e["code"] for e in result["errors"]]
    return False, f"{relative}  →  应该合法，实际报错 {codes}"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__)
        return 2

    if args[0] in ("--schema", "-s"):
        print(ROOT / "schema" / "mql.schema.json")
        return 0

    validator = Validator(_load_ontology())
    targets = [Path(a) for a in args]

    files: list[Path] = []
    for target in targets:
        if target.is_dir():
            files.extend(sorted(target.rglob("*.json")))
        elif target.is_file():
            files.append(target)
        else:
            print(f"找不到路径: {target}")
            return 2

    files = [f for f in files if not f.name.startswith("_")]

    passed = failed = 0
    for path in files:
        ok, message = run_one(validator, path)
        print(("PASS  " if ok else "FAIL  ") + message)
        if ok:
            passed += 1
        else:
            failed += 1
        # 连续失败时把完整错误打出来，方便定位
        if not ok:
            payload = _load_json(path)
            mql = payload["mql"] if _is_negative_case(payload) else payload
            for item in validator.validate(mql)["errors"]:
                print(f"        {item['code']} @ {item['path'] or '<root>'}: {item['message']}")

    print(f"\n{passed} passed, {failed} failed, {passed + failed} total")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
