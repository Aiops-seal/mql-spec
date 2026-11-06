"""本体接口。

校验器只依赖这个协议，不依赖任何具体本体实现。
换句话说：**换一个本体，不需要改校验器；换一个校验器，不需要改本体。**

实现这个协议所需的最小样例见 ``mql_spec.static_ontology.StaticOntology``。
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable


@runtime_checkable
class OntologyProtocol(Protocol):
    """校验期间需要问本体的全部问题。"""

    def has_metric(self, name: str) -> bool:
        """该指标是否已注册。"""

    def metric_names(self) -> Sequence[str]:
        """已注册的全部指标名，用于生成候选建议。"""

    def has_dimension(self, name: str) -> bool:
        """该属性是否可作为分组键。"""

    def dimension_names(self) -> Sequence[str]:
        """可作为分组键的全部属性名。"""

    def has_filter_field(self, name: str) -> bool:
        """该属性是否可作为过滤条件。

        注意：不是所有维度都能过滤，有些计算属性只能分组。
        """

    def filter_field_names(self) -> Sequence[str]:
        """可作为过滤条件的全部属性名。"""

    def is_time_property(self, name: str) -> bool:
        """该属性是否为时间类型。

        ``granularity`` 只能挂在时间属性上，这个判断是那条约束的唯一依据。
        """
