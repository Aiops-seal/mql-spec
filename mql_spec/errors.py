"""错误码与错误对象。

调用方应该根据 `code` 决定行为，而不是对 message 做字符串匹配。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    """MQL 校验的全部错误码。枚举值冻结，新增需走 major 版本。"""

    # ---- 阶段 1：结构 ----
    MQL_SHAPE = "MQL_SHAPE"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"

    # ---- 阶段 2：词汇 ----
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    BAD_OPERATOR = "BAD_OPERATOR"
    BAD_GRANULARITY = "BAD_GRANULARITY"
    BAD_COMPARE = "BAD_COMPARE"
    BAD_VALUE = "BAD_VALUE"

    # ---- 阶段 3：语义 ----
    UNKNOWN_METRIC = "UNKNOWN_METRIC"
    UNKNOWN_PROPERTY = "UNKNOWN_PROPERTY"
    PHYSICAL_LEAK = "PHYSICAL_LEAK"
    NOT_TIME_DIMENSION = "NOT_TIME_DIMENSION"


class CompilerErrorCode(str, Enum):
    """不由本规范产生、但调用方需要统一处理的错误码。

    它们来自编译器，列在这里只是为了让调用方有一份完整清单。
    """

    NO_CANDIDATE_TABLE = "NO_CANDIDATE_TABLE"
    UNSOLVABLE = "UNSOLVABLE"


@dataclass(frozen=True)
class ValidationError:
    """一条校验错误。

    Attributes:
        code: 错误码，调用方据此分支处理。
        path: 出错位置，如 ``metrics[0].name``。空字符串表示整个请求。
        message: 人类可读说明，不保证稳定，**不要用来做逻辑判断**。
        candidates: 可选的候选建议，仅在"猜错了但可能想说的是别的"时出现。
    """

    code: ErrorCode
    path: str
    message: str
    candidates: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code.value,
            "path": self.path,
            "message": self.message,
        }
        if self.candidates:
            out["candidates"] = list(self.candidates)
        return out

    def __str__(self) -> str:  # pragma: no cover - 仅调试用
        tail = f" 候选: {list(self.candidates)}" if self.candidates else ""
        return f"[{self.code.value}] {self.path}: {self.message}{tail}"


def err(
    code: ErrorCode,
    path: str,
    message: str,
    candidates: tuple[str, ...] | list[str] = (),
) -> ValidationError:
    return ValidationError(code=code, path=path, message=message, candidates=tuple(candidates))
