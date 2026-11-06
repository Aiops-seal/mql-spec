"""数据驱动测试：把 cases/ 下的每一份文件都跑一遍。

这是这个仓库最主要的测试 —— 规范改了、实现改了，这里必须还是绿的。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mql_spec import StaticOntology, Validator

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "cases"


def _load(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def validator() -> Validator:
    return Validator(StaticOntology.from_dict(_load(CASES / "_ontology.json")))


def _valid_files() -> list[Path]:
    return sorted((CASES / "valid").glob("*.json"))


def _invalid_files() -> list[Path]:
    return sorted((CASES / "invalid").glob("*.json"))


@pytest.mark.parametrize("path", _valid_files(), ids=lambda p: p.stem)
def test_valid_cases_are_accepted(validator: Validator, path: Path) -> None:
    result = validator.validate(_load(path))
    assert result["ok"], f"{path.name} 应该合法，实际报错: {result['errors']}"


@pytest.mark.parametrize("path", _invalid_files(), ids=lambda p: p.stem)
def test_invalid_cases_are_rejected_with_expected_code(
    validator: Validator, path: Path
) -> None:
    payload = _load(path)
    result = validator.validate(payload["mql"])
    codes = [e["code"] for e in result["errors"]]
    assert not result["ok"], f"{path.name} 应该被拒绝，实际通过了"
    assert payload["expect"] in codes, (
        f"{path.name} 期望 {payload['expect']}，实际 {codes}"
        f"\n  用例说明: {payload.get('note', '')}"
    )


def test_case_manifest_is_complete() -> None:
    """反例必须带 expect 和 note —— note 是这个仓库的价值所在，不许省略。"""

    assert _invalid_files(), "cases/invalid 是空的"
    for path in _invalid_files():
        payload = _load(path)
        assert "mql" in payload, f"{path.name} 缺少 mql"
        assert "expect" in payload, f"{path.name} 缺少 expect"
        assert payload.get("note"), f"{path.name} 缺少 note：每个反例都要说明它为什么重要"
