"""MQL v1.1 参考校验器。

设计要点（详见 SPEC.md）：

1. 校验分三个阶段 —— 结构 / 词汇 / 语义。
2. 只有"结构无法继续解析"才终止；其余阶段收集全部错误后一次返回。
   原因是调用方通常是模型：只报第一个错误会让它一轮一轮试。
3. **未知字段直接报错**，不忽略、不警告。这是与很多同类实现最重要的分歧。
4. 物理渗入的判定以白名单为主防线，形状检测为副防线 —— 白名单不需要知道你的数仓命名规范。
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Iterable, Sequence

from .errors import ErrorCode, ValidationError, err
from .protocol import OntologyProtocol

# ---------------------------------------------------------------- 常量

SUPPORTED_VERSIONS: tuple[str, ...] = ("1.1",)
MAX_LIMIT = 10_000

TOP_LEVEL_FIELDS = frozenset(
    {
        "mql_version",
        "metrics",
        "dimensions",
        "filters",
        "time_range",
        "compare",
        "sort",
        "limit",
    }
)
METRIC_FIELDS = frozenset({"name", "version", "alias"})
DIMENSION_FIELDS = frozenset({"name", "granularity"})
FILTER_FIELDS = frozenset({"field", "operator", "value"})
TIME_RANGE_FIELDS = frozenset({"start", "end"})
COMPARE_FIELDS = frozenset({"type", "offset"})
SORT_FIELDS = frozenset({"field", "order"})

OPERATORS = frozenset(
    {"eq", "neq", "gt", "gte", "lt", "lte", "in", "not_in", "like", "between"}
)
ARRAY_OPERATORS = frozenset({"in", "not_in", "between"})
GRANULARITIES = frozenset({"day", "week", "month", "quarter", "year"})
COMPARE_TYPES = frozenset({"yoy", "mom", "wow"})
SORT_ORDERS = frozenset({"asc", "desc"})

# 相对时间：today / yesterday / now / -7d / -2w / -3m / -1q / -1y
_RELATIVE_TIME = re.compile(r"^(today|yesterday|now|-\d+[dwmqy])$")
_ABSOLUTE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 物理渗入的"形状"信号：括号、空白、点号
_LEAK_SHAPE = re.compile(r"[()\[\]{}.\s]")
_SQL_FUNCTIONS = frozenset(
    {
        "sum", "count", "avg", "max", "min", "distinct",
        "cast", "coalesce", "round", "abs", "concat", "substr",
        "date_trunc", "strftime", "extract", "case", "when",
    }
)


# ---------------------------------------------------------------- 工具


def _typename(value: Any) -> str:
    """给错误信息用的类型名，比 type().__name__ 友好。"""

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _leak_reason(name: str) -> str | None:
    """判断一个名字是否带物理/SQL 特征。返回原因，无则返回 None。"""

    if _LEAK_SHAPE.search(name):
        return "含 SQL 特征字符（括号 / 空白 / 点号）"
    if name.strip().lower() in _SQL_FUNCTIONS:
        return f"是 SQL 函数名 {name.strip()!r}，不是业务指标"
    return None


def _items(mql: dict[str, Any], key: str) -> Iterable[tuple[int, dict[str, Any]]]:
    """遍历某个数组字段里的对象元素，跳过类型不对的（那些已在阶段 1 报过）。"""

    value = mql.get(key)
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        if isinstance(item, dict):
            yield index, item


def _str_field(item: dict[str, Any], key: str) -> str | None:
    value = item.get(key)
    return value if isinstance(value, str) and value else None


# ---------------------------------------------------------------- 主类


class Validator:
    """MQL 校验器。

    Example:
        >>> from mql_spec import Validator, StaticOntology
        >>> onto = StaticOntology(metrics=("gmv",), dimensions=("city",))
        >>> result = Validator(onto).validate(
        ...     {"mql_version": "1.1", "metrics": [{"name": "gmv"}]}
        ... )
        >>> result["ok"]
        True
    """

    def __init__(self, ontology: OntologyProtocol, *, max_candidates: int = 5) -> None:
        self.ontology = ontology
        self.max_candidates = max_candidates

    # ------------------------------------------------------------ 公开入口

    def validate(self, mql: Any) -> dict[str, Any]:
        """校验一份 MQL。

        Returns:
            ``{"ok": bool, "errors": [dict], "warnings": [dict]}``
        """
        errors: list[ValidationError] = []
        warnings: list[dict[str, str]] = []

        fatal = self._stage_shape(mql, errors)
        if not fatal:
            self._stage_vocabulary(mql, errors)
            self._stage_semantics(mql, errors)
            self._collect_warnings(mql, warnings)

        return {
            "ok": not errors,
            "errors": [e.to_dict() for e in errors],
            "warnings": warnings,
        }

    # ------------------------------------------------------------ 阶段 1：结构

    def _stage_shape(self, mql: Any, errors: list[ValidationError]) -> bool:
        """检查结构。返回 True 表示无法继续解析，应立即终止。"""

        if not isinstance(mql, dict):
            errors.append(
                err(
                    ErrorCode.MQL_SHAPE,
                    "",
                    f"MQL 必须是一个 JSON 对象，收到 {_typename(mql)}",
                )
            )
            return True

        self._check_version(mql, errors)

        metrics = mql.get("metrics")
        if metrics is None:
            errors.append(
                err(
                    ErrorCode.MQL_SHAPE,
                    "metrics",
                    "缺少必填字段 metrics —— 没有指标的请求不是指标查询",
                )
            )
            return True
        if not isinstance(metrics, list):
            errors.append(
                err(
                    ErrorCode.MQL_SHAPE,
                    "metrics",
                    f"metrics 必须是数组，收到 {_typename(metrics)}",
                )
            )
            return True
        if not metrics:
            errors.append(
                err(ErrorCode.MQL_SHAPE, "metrics", "metrics 不能为空数组")
            )
            return True

        # 以下是可继续的错误
        for index, item in enumerate(metrics):
            self._check_named_item(item, f"metrics[{index}]", errors)

        for key in ("dimensions", "filters", "sort"):
            value = mql.get(key)
            if value is None:
                continue
            if not isinstance(value, list):
                errors.append(
                    err(
                        ErrorCode.MQL_SHAPE,
                        key,
                        f"{key} 必须是数组，收到 {_typename(value)}",
                    )
                )
                continue
            for index, item in enumerate(value):
                if key == "dimensions":
                    self._check_named_item(item, f"{key}[{index}]", errors)
                elif key == "filters":
                    self._check_filter_item(item, f"{key}[{index}]", errors)
                else:
                    self._check_sort_item(item, f"{key}[{index}]", errors)

        self._check_time_range_shape(mql, errors)
        self._check_compare_shape(mql, errors)
        self._check_limit(mql, errors)
        return False

    def _check_version(self, mql: dict[str, Any], errors: list[ValidationError]) -> None:
        version = mql.get("mql_version")
        if version is None:
            errors.append(
                err(
                    ErrorCode.MQL_SHAPE,
                    "mql_version",
                    "缺少必填字段 mql_version —— 版本必须显式声明，不靠字段存在性推断",
                )
            )
        elif not isinstance(version, str):
            errors.append(
                err(
                    ErrorCode.MQL_SHAPE,
                    "mql_version",
                    f"mql_version 必须是字符串，收到 {_typename(version)}",
                )
            )
        elif version not in SUPPORTED_VERSIONS:
            errors.append(
                err(
                    ErrorCode.UNSUPPORTED_VERSION,
                    "mql_version",
                    f"不支持的版本 {version!r}，当前只支持 {list(SUPPORTED_VERSIONS)}",
                    candidates=SUPPORTED_VERSIONS,
                )
            )

    def _check_named_item(
        self, item: Any, path: str, errors: list[ValidationError]
    ) -> None:
        if not isinstance(item, dict):
            errors.append(
                err(ErrorCode.MQL_SHAPE, path, f"必须是对象，收到 {_typename(item)}")
            )
            return
        name = _str_field(item, "name")
        if name is None:
            errors.append(
                err(ErrorCode.MQL_SHAPE, f"{path}.name", "缺少必填字段 name，或不是非空字符串")
            )

    def _check_filter_item(
        self, item: Any, path: str, errors: list[ValidationError]
    ) -> None:
        if not isinstance(item, dict):
            errors.append(
                err(ErrorCode.MQL_SHAPE, path, f"必须是对象，收到 {_typename(item)}")
            )
            return
        if _str_field(item, "field") is None:
            errors.append(
                err(ErrorCode.MQL_SHAPE, f"{path}.field", "缺少必填字段 field，或不是非空字符串")
            )
        if _str_field(item, "operator") is None:
            errors.append(
                err(
                    ErrorCode.MQL_SHAPE,
                    f"{path}.operator",
                    "缺少必填字段 operator，或不是非空字符串",
                )
            )
        if "value" not in item:
            errors.append(err(ErrorCode.MQL_SHAPE, f"{path}.value", "缺少必填字段 value"))

    def _check_sort_item(
        self, item: Any, path: str, errors: list[ValidationError]
    ) -> None:
        if not isinstance(item, dict):
            errors.append(
                err(ErrorCode.MQL_SHAPE, path, f"必须是对象，收到 {_typename(item)}")
            )
            return
        if _str_field(item, "field") is None:
            errors.append(
                err(ErrorCode.MQL_SHAPE, f"{path}.field", "缺少必填字段 field，或不是非空字符串")
            )
        if _str_field(item, "order") is None:
            errors.append(
                err(ErrorCode.MQL_SHAPE, f"{path}.order", "缺少必填字段 order，或不是非空字符串")
            )

    def _check_time_range_shape(
        self, mql: dict[str, Any], errors: list[ValidationError]
    ) -> None:
        value = mql.get("time_range")
        if value is None:
            return
        if not isinstance(value, dict):
            errors.append(
                err(
                    ErrorCode.MQL_SHAPE,
                    "time_range",
                    f"time_range 必须是对象，收到 {_typename(value)}",
                )
            )
            return
        for key in ("start", "end"):
            raw = value.get(key)
            if raw is not None and not isinstance(raw, str):
                errors.append(
                    err(
                        ErrorCode.MQL_SHAPE,
                        f"time_range.{key}",
                        f"必须是字符串，收到 {_typename(raw)}",
                    )
                )

    def _check_compare_shape(
        self, mql: dict[str, Any], errors: list[ValidationError]
    ) -> None:
        value = mql.get("compare")
        if value is None:
            return
        if not isinstance(value, dict):
            hint = ""
            if isinstance(value, str):
                hint = " —— 正确写法是 {\"type\": \"yoy\"}，不是一个字符串"
            errors.append(
                err(
                    ErrorCode.MQL_SHAPE,
                    "compare",
                    f"compare 必须是对象，收到 {_typename(value)}{hint}",
                )
            )
            return
        if _str_field(value, "type") is None:
            errors.append(
                err(
                    ErrorCode.MQL_SHAPE,
                    "compare.type",
                    "缺少必填字段 compare.type，或不是非空字符串",
                )
            )
        offset = value.get("offset")
        if offset is not None:
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 1:
                errors.append(
                    err(
                        ErrorCode.MQL_SHAPE,
                        "compare.offset",
                        f"offset 必须是正整数，收到 {offset!r}",
                    )
                )

    def _check_limit(self, mql: dict[str, Any], errors: list[ValidationError]) -> None:
        value = mql.get("limit")
        if value is None:
            return
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(
                err(
                    ErrorCode.MQL_SHAPE,
                    "limit",
                    f"limit 必须是整数，收到 {_typename(value)}",
                )
            )
            return
        if value < 1:
            errors.append(
                err(ErrorCode.MQL_SHAPE, "limit", f"limit 必须大于 0，收到 {value}")
            )
        elif value > MAX_LIMIT:
            errors.append(
                err(
                    ErrorCode.MQL_SHAPE,
                    "limit",
                    f"limit 超过上限 {MAX_LIMIT}，收到 {value} —— 过大的 limit 会绕过下游的行数保护",
                )
            )

    # ------------------------------------------------------------ 阶段 2：词汇

    def _stage_vocabulary(
        self, mql: dict[str, Any], errors: list[ValidationError]
    ) -> None:
        self._check_unknown_fields(mql, errors)

        for index, item in _items(mql, "dimensions"):
            granularity = item.get("granularity")
            if granularity is not None and granularity not in GRANULARITIES:
                errors.append(
                    err(
                        ErrorCode.BAD_GRANULARITY,
                        f"dimensions[{index}].granularity",
                        f"非法粒度 {granularity!r}",
                        candidates=sorted(GRANULARITIES),
                    )
                )

        for index, item in _items(mql, "filters"):
            self._check_filter_value(item, f"filters[{index}]", errors)

        for index, item in _items(mql, "sort"):
            order = item.get("order")
            if order is not None and order not in SORT_ORDERS:
                errors.append(
                    err(
                        ErrorCode.BAD_VALUE,
                        f"sort[{index}].order",
                        f"排序方向只允许 asc / desc，收到 {order!r}",
                        candidates=sorted(SORT_ORDERS),
                    )
                )

        self._check_time_range_values(mql, errors)
        self._check_compare_type(mql, errors)

    def _check_unknown_fields(
        self, mql: dict[str, Any], errors: list[ValidationError]
    ) -> None:
        """严格模式：schema 之外的字段一律报错。

        这是本实现与很多同类实现最重要的分歧 —— 静默忽略未知字段会让
        "用户以为要了同比、实际没要"这类错误一路跑到结果里。
        """

        def report(unknown: set[str], path: str, allowed: frozenset[str]) -> None:
            for name in sorted(unknown):
                errors.append(
                    err(
                        ErrorCode.UNKNOWN_FIELD,
                        f"{path}.{name}" if path else name,
                        f"未知字段 {name!r} —— 本规范不接受未定义的字段，"
                        f"合法字段为 {sorted(allowed)}",
                        candidates=sorted(allowed),
                    )
                )

        report(set(mql) - TOP_LEVEL_FIELDS, "", TOP_LEVEL_FIELDS)

        for index, item in _items(mql, "metrics"):
            report(set(item) - METRIC_FIELDS, f"metrics[{index}]", METRIC_FIELDS)
        for index, item in _items(mql, "dimensions"):
            report(set(item) - DIMENSION_FIELDS, f"dimensions[{index}]", DIMENSION_FIELDS)
        for index, item in _items(mql, "filters"):
            report(set(item) - FILTER_FIELDS, f"filters[{index}]", FILTER_FIELDS)
        for index, item in _items(mql, "sort"):
            report(set(item) - SORT_FIELDS, f"sort[{index}]", SORT_FIELDS)

        time_range = mql.get("time_range")
        if isinstance(time_range, dict):
            report(set(time_range) - TIME_RANGE_FIELDS, "time_range", TIME_RANGE_FIELDS)

        compare = mql.get("compare")
        if isinstance(compare, dict):
            report(set(compare) - COMPARE_FIELDS, "compare", COMPARE_FIELDS)

    def _check_filter_value(
        self, item: dict[str, Any], path: str, errors: list[ValidationError]
    ) -> None:
        if "value" not in item:
            return  # 已在阶段 1 报过
        operator = _str_field(item, "operator")
        if operator is None:
            return  # 已在阶段 1 报过
        if operator not in OPERATORS:
            errors.append(
                err(
                    ErrorCode.BAD_OPERATOR,
                    f"{path}.operator",
                    f"非法操作符 {operator!r} —— MQL 不接受 SQL 写法，只接受枚举值",
                    candidates=sorted(OPERATORS),
                )
            )
            return

        value = item["value"]
        if operator in ("in", "not_in"):
            if not isinstance(value, list):
                errors.append(
                    err(
                        ErrorCode.BAD_VALUE,
                        f"{path}.value",
                        f"操作符 {operator} 的 value 必须是数组，收到 {_typename(value)}",
                    )
                )
            elif not value:
                errors.append(
                    err(ErrorCode.BAD_VALUE, f"{path}.value", f"操作符 {operator} 的 value 不能是空数组")
                )
        elif operator == "between":
            if not isinstance(value, list):
                errors.append(
                    err(
                        ErrorCode.BAD_VALUE,
                        f"{path}.value",
                        f"操作符 between 的 value 必须是长度为 2 的数组，收到 {_typename(value)}",
                    )
                )
            elif len(value) != 2:
                errors.append(
                    err(
                        ErrorCode.BAD_VALUE,
                        f"{path}.value",
                        f"操作符 between 的 value 长度必须为 2，收到 {len(value)}",
                    )
                )
        elif operator == "like":
            if not isinstance(value, str):
                errors.append(
                    err(
                        ErrorCode.BAD_VALUE,
                        f"{path}.value",
                        f"操作符 like 的 value 必须是字符串，收到 {_typename(value)}",
                    )
                )
        else:  # 标量比较
            if isinstance(value, (list, dict)):
                errors.append(
                    err(
                        ErrorCode.BAD_VALUE,
                        f"{path}.value",
                        f"操作符 {operator} 的 value 必须是标量，收到 {_typename(value)}",
                    )
                )

    def _check_time_range_values(
        self, mql: dict[str, Any], errors: list[ValidationError]
    ) -> None:
        time_range = mql.get("time_range")
        if not isinstance(time_range, dict):
            return
        kinds: dict[str, str] = {}
        for key in ("start", "end"):
            raw = time_range.get(key)
            if not isinstance(raw, str):
                continue
            if _RELATIVE_TIME.match(raw):
                kinds[key] = "relative"
            elif _ABSOLUTE_TIME.match(raw):
                kinds[key] = "absolute"
            else:
                errors.append(
                    err(
                        ErrorCode.BAD_VALUE,
                        f"time_range.{key}",
                        f"无法识别的时间表达式 {raw!r} —— 只接受 today/yesterday/now/-Nd/-Nw/-Nm/-Nq/-Ny "
                        f"或 YYYY-MM-DD",
                    )
                )
        if len(set(kinds.values())) > 1:
            errors.append(
                err(
                    ErrorCode.BAD_VALUE,
                    "time_range",
                    f"相对时间与绝对时间不得混用，当前 start={kinds.get('start')} / end={kinds.get('end')}",
                )
            )

    def _check_compare_type(
        self, mql: dict[str, Any], errors: list[ValidationError]
    ) -> None:
        compare = mql.get("compare")
        if not isinstance(compare, dict):
            return
        ctype = _str_field(compare, "type")
        if ctype is not None and ctype not in COMPARE_TYPES:
            errors.append(
                err(
                    ErrorCode.BAD_COMPARE,
                    "compare.type",
                    f"非法对比类型 {ctype!r}",
                    candidates=sorted(COMPARE_TYPES),
                )
            )

    # ------------------------------------------------------------ 阶段 3：语义

    def _stage_semantics(
        self, mql: dict[str, Any], errors: list[ValidationError]
    ) -> None:
        for index, item in _items(mql, "metrics"):
            name = _str_field(item, "name")
            if name is None:
                continue
            self._check_registered(
                name, f"metrics[{index}].name", "metric", errors
            )

        for index, item in _items(mql, "dimensions"):
            name = _str_field(item, "name")
            if name is None:
                continue
            self._check_registered(
                name, f"dimensions[{index}].name", "dimension", errors
            )
            self._check_granularity_target(name, item, f"dimensions[{index}]", errors)

        for index, item in _items(mql, "filters"):
            name = _str_field(item, "field")
            if name is None:
                continue
            self._check_registered(
                name, f"filters[{index}].field", "filter", errors
            )

        self._check_sort_targets(mql, errors)
        self._check_compare_period(mql, errors)

    def _check_registered(
        self,
        name: str,
        path: str,
        kind: str,
        errors: list[ValidationError],
    ) -> None:
        """白名单校验 + 形状检测。

        先看形状：带 SQL 特征的名字判 PHYSICAL_LEAK（不该重试，直接拒绝）。
        再看白名单：不在注册表里的判 UNKNOWN_*（可以带候选让调用方重试）。
        """

        leak = _leak_reason(name)
        if leak is not None:
            errors.append(
                err(
                    ErrorCode.PHYSICAL_LEAK,
                    path,
                    f"{name!r} {leak} —— MQL 只接受本体注册过的业务名字",
                )
            )
            return

        if kind == "metric":
            ok = self.ontology.has_metric(name)
            universe: Sequence[str] = self.ontology.metric_names()
            code = ErrorCode.UNKNOWN_METRIC
            label = "指标"
        elif kind == "dimension":
            ok = self.ontology.has_dimension(name)
            universe = self.ontology.dimension_names()
            code = ErrorCode.UNKNOWN_PROPERTY
            label = "分组维度"
        else:
            ok = self.ontology.has_filter_field(name)
            universe = self.ontology.filter_field_names()
            code = ErrorCode.UNKNOWN_PROPERTY
            label = "过滤字段"

        if ok:
            return

        close = difflib.get_close_matches(name, list(universe), n=self.max_candidates, cutoff=0.5)
        errors.append(
            err(
                code,
                path,
                f"{label} {name!r} 未在本体注册",
                candidates=close,
            )
        )

    def _check_granularity_target(
        self,
        name: str,
        item: dict[str, Any],
        path: str,
        errors: list[ValidationError],
    ) -> None:
        """granularity 只能挂在时间属性上。

        很多实现只检查 granularity 是不是那五个合法值，不检查维度本身是不是
        时间类型 —— 结果 ``{"name": "city", "granularity": "month"}`` 能过校验，
        到下游才炸，或者更糟：下游不炸，静默按城市分组、把月粒度丢了。
        """

        if item.get("granularity") is None:
            return
        if not self.ontology.has_dimension(name):
            return  # 已在 _check_registered 报过
        if not self.ontology.is_time_property(name):
            errors.append(
                err(
                    ErrorCode.NOT_TIME_DIMENSION,
                    f"{path}.granularity",
                    f"{name!r} 不是时间属性，不能指定粒度 —— 城市没有'月'这个粒度",
                )
            )

    def _check_sort_targets(
        self, mql: dict[str, Any], errors: list[ValidationError]
    ) -> None:
        metric_names = {
            n for _, item in _items(mql, "metrics") if (n := _str_field(item, "name"))
        }
        dimension_names = {
            n for _, item in _items(mql, "dimensions") if (n := _str_field(item, "name"))
        }
        available = metric_names | dimension_names

        for index, item in _items(mql, "sort"):
            field = _str_field(item, "field")
            if field is None:
                continue
            if field not in available:
                errors.append(
                    err(
                        ErrorCode.UNKNOWN_PROPERTY,
                        f"sort[{index}].field",
                        f"排序字段 {field!r} 未出现在本次查询的 metrics 或 dimensions 中",
                        candidates=sorted(available),
                    )
                )

    def _check_compare_period(
        self, mql: dict[str, Any], errors: list[ValidationError]
    ) -> None:
        """compare 存在时必须能确定比较期间，否则编译器只能猜。"""

        compare = mql.get("compare")
        if not isinstance(compare, dict):
            return
        if _str_field(compare, "type") not in COMPARE_TYPES:
            return  # 类型已非法，不再叠加错误

        time_range = mql.get("time_range")
        has_range = isinstance(time_range, dict) and bool(
            time_range.get("start") or time_range.get("end")
        )

        has_time_dim = False
        for _, item in _items(mql, "dimensions"):
            name = _str_field(item, "name")
            if name is None or item.get("granularity") is None:
                continue
            if self.ontology.has_dimension(name) and self.ontology.is_time_property(name):
                has_time_dim = True
                break

        if not has_range and not has_time_dim:
            errors.append(
                err(
                    ErrorCode.BAD_COMPARE,
                    "compare",
                    "compare 存在但无法确定比较期间：既没有 time_range，"
                    "也没有带 granularity 的时间维度 —— 编译器只能猜按月还是按天比",
                )
            )

    # ------------------------------------------------------------ 警告

    def _collect_warnings(
        self, mql: dict[str, Any], warnings: list[dict[str, str]]
    ) -> None:
        time_range = mql.get("time_range")
        if not isinstance(time_range, dict) or not (
            time_range.get("start") or time_range.get("end")
        ):
            warnings.append(
                {
                    "code": "DEFAULT_TIME",
                    "path": "time_range",
                    "message": "未指定时间范围，编译器将使用默认时间（通常是 t-1）—— 如果这不是本意，请显式给出 time_range",
                }
            )
