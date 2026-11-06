"""一个用普通字典实现的本体，用于测试与演示。

生产环境应该实现 ``OntologyProtocol``，从你的指标平台/语义层取数据。
这个类只是让校验器在没有真实本体时也能跑起来。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class StaticOntology:
    """从静态集合构建的本体。

    Example:
        >>> onto = StaticOntology(
        ...     metrics=("gmv", "order_count"),
        ...     dimensions=("city", "order_date"),
        ...     time_properties=("order_date",),
        ... )
        >>> onto.has_metric("gmv")
        True
    """

    metrics: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()
    filter_fields: tuple[str, ...] | None = None
    time_properties: tuple[str, ...] = ()
    _metric_set: frozenset[str] = field(init=False, repr=False, default=frozenset())
    _dim_set: frozenset[str] = field(init=False, repr=False, default=frozenset())
    _filter_set: frozenset[str] = field(init=False, repr=False, default=frozenset())
    _time_set: frozenset[str] = field(init=False, repr=False, default=frozenset())

    def __post_init__(self) -> None:
        object.__setattr__(self, "_metric_set", frozenset(self.metrics))
        object.__setattr__(self, "_dim_set", frozenset(self.dimensions))
        filters = self.dimensions if self.filter_fields is None else self.filter_fields
        object.__setattr__(self, "_filter_set", frozenset(filters))
        object.__setattr__(self, "_time_set", frozenset(self.time_properties))

    # ---- OntologyProtocol ----
    def has_metric(self, name: str) -> bool:
        return name in self._metric_set

    def metric_names(self) -> Sequence[str]:
        return self.metrics

    def has_dimension(self, name: str) -> bool:
        return name in self._dim_set

    def dimension_names(self) -> Sequence[str]:
        return self.dimensions

    def has_filter_field(self, name: str) -> bool:
        return name in self._filter_set

    def filter_field_names(self) -> Sequence[str]:
        return tuple(sorted(self._filter_set))

    def is_time_property(self, name: str) -> bool:
        return name in self._time_set

    # ---- 便利构造 ----
    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StaticOntology":
        """从形如 ``{"metrics": [...], "dimensions": [...], ...}`` 的字典构造。"""

        def _tuple(key: str) -> tuple[str, ...]:
            value = payload.get(key) or ()
            if not isinstance(value, Sequence) or isinstance(value, str):
                raise TypeError(f"ontology['{key}'] 必须是字符串序列")
            return tuple(str(v) for v in value)

        raw_filters = payload.get("filter_fields")
        return cls(
            metrics=_tuple("metrics"),
            dimensions=_tuple("dimensions"),
            filter_fields=None if raw_filters is None else _tuple("filter_fields"),
            time_properties=_tuple("time_properties"),
        )
