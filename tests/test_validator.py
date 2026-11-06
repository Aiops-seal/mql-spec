"""行为级测试：针对校验器的具体设计决策，而不是单个用例。

这些断言每一条都对应 SPEC.md 里的一段话。文档和代码对不上时，这里会红。
"""

from __future__ import annotations

from typing import Sequence

from mql_spec import ErrorCode, StaticOntology, Validator

V = "1.1"

ONTO = StaticOntology(
    metrics=("gmv", "order_count", "net_gmv"),
    dimensions=("city", "order_date", "channel"),
    time_properties=("order_date",),
)


def codes(result: dict) -> list[str]:
    return [e["code"] for e in result["errors"]]


# ---------------------------------------------------------------- 严格模式


def test_unknown_top_level_field_is_rejected() -> None:
    """这是本实现与很多同类实现最重要的分歧：未知字段必须报错，不能忽略。

    如果这里改成"只警告"，那么 {"compare": "yoy"} 这种请求会静默通过，
    SQL 照出，同比没有 —— 而且没有人报错。
    """

    result = Validator(ONTO).validate(
        {"mql_version": V, "metrics": [{"name": "gmv"}], "compare_period": "yoy"}
    )
    assert not result["ok"]
    assert ErrorCode.UNKNOWN_FIELD.value in codes(result)


def test_unknown_sub_field_is_rejected() -> None:
    result = Validator(ONTO).validate(
        {"mql_version": V, "metrics": [{"name": "gmv", "agg": "sum"}]}
    )
    assert ErrorCode.UNKNOWN_FIELD.value in codes(result)


def test_unknown_field_error_lists_legal_fields() -> None:
    result = Validator(ONTO).validate(
        {"mql_version": V, "metrics": [{"name": "gmv"}], "groupby": []}
    )
    error = next(e for e in result["errors"] if e["code"] == "UNKNOWN_FIELD")
    assert "mql_version" in error["candidates"]


# ---------------------------------------------------------------- 阶段划分


def test_structural_failure_stops_early() -> None:
    """阶段 1 遇到无法解析的输入时立即终止，不产生噪声错误。"""

    result = Validator(ONTO).validate(["not", "an", "object"])
    assert len(result["errors"]) == 1
    assert result["errors"][0]["code"] == ErrorCode.MQL_SHAPE.value


def test_semantic_errors_are_collected_not_short_circuited() -> None:
    """阶段 2/3 要一次报全，否则调用方（通常是模型）要一轮一轮试。"""

    result = Validator(ONTO).validate(
        {
            "mql_version": V,
            "metrics": [{"name": "not_a_metric"}],
            "dimensions": [{"name": "city", "granularity": "month"}],
            "filters": [{"field": "channel", "operator": "=", "value": "x"}],
        }
    )
    got = set(codes(result))
    assert ErrorCode.UNKNOWN_METRIC.value in got
    assert ErrorCode.NOT_TIME_DIMENSION.value in got
    assert ErrorCode.BAD_OPERATOR.value in got
    assert len(result["errors"]) >= 3


# ---------------------------------------------------------------- 物理渗入


def test_physical_leak_takes_priority_over_unknown_metric() -> None:
    """带 SQL 特征的写法判 PHYSICAL_LEAK，不是 UNKNOWN_METRIC。

    两者的处理方式不同：UNKNOWN_METRIC 可以带候选让上游重试，
    PHYSICAL_LEAK 说明越界了，不该重试。
    """

    result = Validator(ONTO).validate(
        {"mql_version": V, "metrics": [{"name": "SUM(pay_amt)"}]}
    )
    assert codes(result) == [ErrorCode.PHYSICAL_LEAK.value]


def test_bare_sql_function_is_a_leak() -> None:
    result = Validator(ONTO).validate({"mql_version": V, "metrics": [{"name": "sum"}]})
    assert ErrorCode.PHYSICAL_LEAK.value in codes(result)


def test_wildcard_free_metric_name_is_not_a_leak() -> None:
    """order_count 含 count，但它是整词里的一个片段，不能误判。"""

    result = Validator(ONTO).validate(
        {"mql_version": V, "metrics": [{"name": "order_count"}]}
    )
    assert result["ok"], result["errors"]


def test_no_prefix_blacklist() -> None:
    """白名单不需要知道数仓命名规范。app_ 开头的名字只是一个未注册指标。"""

    result = Validator(ONTO).validate(
        {"mql_version": V, "metrics": [{"name": "app_realtime_gmv"}]}
    )
    assert ErrorCode.UNKNOWN_METRIC.value in codes(result)
    assert ErrorCode.PHYSICAL_LEAK.value not in codes(result)


# ---------------------------------------------------------------- 候选建议


def test_unknown_metric_carries_candidates() -> None:
    result = Validator(ONTO).validate({"mql_version": V, "metrics": [{"name": "gvm"}]})
    error = result["errors"][0]
    assert error["code"] == ErrorCode.UNKNOWN_METRIC.value
    assert "gmv" in error["candidates"]


def test_candidate_count_is_bounded() -> None:
    result = Validator(ONTO, max_candidates=1).validate(
        {"mql_version": V, "metrics": [{"name": "gm"}]}
    )
    assert len(result["errors"][0]["candidates"]) <= 1


# ---------------------------------------------------------------- 粒度约束


def test_granularity_requires_time_property() -> None:
    """上游实现只校验 granularity 是否在枚举内，不校验维度是不是时间类型。

    结果 {"name": "city", "granularity": "month"} 能过校验 —— 城市没有"月"。
    """

    result = Validator(ONTO).validate(
        {
            "mql_version": V,
            "metrics": [{"name": "gmv"}],
            "dimensions": [{"name": "city", "granularity": "month"}],
        }
    )
    assert codes(result) == [ErrorCode.NOT_TIME_DIMENSION.value]


def test_granularity_allowed_on_time_property() -> None:
    result = Validator(ONTO).validate(
        {
            "mql_version": V,
            "metrics": [{"name": "gmv"}],
            "dimensions": [{"name": "order_date", "granularity": "week"}],
        }
    )
    assert result["ok"], result["errors"]


# ---------------------------------------------------------------- 对比期间


def test_compare_requires_a_determinable_period() -> None:
    result = Validator(ONTO).validate(
        {"mql_version": V, "metrics": [{"name": "gmv"}], "compare": {"type": "yoy"}}
    )
    assert ErrorCode.BAD_COMPARE.value in codes(result)


def test_compare_ok_with_time_range() -> None:
    result = Validator(ONTO).validate(
        {
            "mql_version": V,
            "metrics": [{"name": "gmv"}],
            "time_range": {"start": "-1m", "end": "yesterday"},
            "compare": {"type": "yoy"},
        }
    )
    assert result["ok"], result["errors"]


def test_compare_ok_with_time_dimension() -> None:
    result = Validator(ONTO).validate(
        {
            "mql_version": V,
            "metrics": [{"name": "gmv"}],
            "dimensions": [{"name": "order_date", "granularity": "month"}],
            "compare": {"type": "yoy"},
        }
    )
    assert result["ok"], result["errors"]


# ---------------------------------------------------------------- 警告


def test_missing_time_range_produces_a_warning() -> None:
    """不写时间不是错误，但必须提醒：编译器会套用默认时间，这可能不是本意。"""

    result = Validator(ONTO).validate({"mql_version": V, "metrics": [{"name": "gmv"}]})
    assert result["ok"]
    assert any(w["code"] == "DEFAULT_TIME" for w in result["warnings"])


def test_explicit_time_range_has_no_warning() -> None:
    result = Validator(ONTO).validate(
        {
            "mql_version": V,
            "metrics": [{"name": "gmv"}],
            "time_range": {"start": "-7d", "end": "today"},
        }
    )
    assert result["warnings"] == []


# ---------------------------------------------------------------- 可替换性


class DictOntology:
    """一个不符合 OntologyProtocol 签名风格、但结构匹配的本体。

    用来验证校验器真的只依赖协议，不依赖具体实现。
    """

    def __init__(self) -> None:
        self._metrics = {"active_user_cnt"}
        self._dims = {"product_line"}

    def has_metric(self, name: str) -> bool:
        return name in self._metrics

    def metric_names(self) -> Sequence[str]:
        return tuple(self._metrics)

    def has_dimension(self, name: str) -> bool:
        return name in self._dims

    def dimension_names(self) -> Sequence[str]:
        return tuple(self._dims)

    def has_filter_field(self, name: str) -> bool:
        return name in self._dims

    def filter_field_names(self) -> Sequence[str]:
        return tuple(self._dims)

    def is_time_property(self, name: str) -> bool:
        return False


def test_validator_works_with_any_ontology_implementation() -> None:
    """换一个本体不需要改校验器 —— 这是它能被单独发布的理由。"""

    validator = Validator(DictOntology())
    ok = validator.validate({"mql_version": V, "metrics": [{"name": "active_user_cnt"}]})
    assert ok["ok"], ok["errors"]

    bad = validator.validate({"mql_version": V, "metrics": [{"name": "gmv"}]})
    assert ErrorCode.UNKNOWN_METRIC.value in codes(bad)


# ---------------------------------------------------------------- 返回结构


def test_result_shape_is_stable() -> None:
    result = Validator(ONTO).validate({"mql_version": V, "metrics": [{"name": "gmv"}]})
    assert set(result) == {"ok", "errors", "warnings"}
    assert isinstance(result["ok"], bool)
    assert isinstance(result["errors"], list)


def test_error_shape_is_stable() -> None:
    result = Validator(ONTO).validate({"mql_version": V, "metrics": [{"name": "zzz"}]})
    error = result["errors"][0]
    assert set(error) >= {"code", "path", "message"}
    assert error["path"] == "metrics[0].name"


def test_never_raises_on_garbage_input() -> None:
    """校验器是边界组件，任何输入都不该抛异常 —— 抛了调用方就没法优雅降级。"""

    validator = Validator(ONTO)
    garbage = [None, 1, 1.5, True, "", [], {}, [1, 2], {"metrics": "x"}, {"metrics": [1]}]
    for item in garbage:
        result = validator.validate(item)
        assert isinstance(result["ok"], bool)
