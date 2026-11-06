# mql-spec

**一门很小的语言，用来表达"我要查哪个指标、按什么维度切、在什么范围内"。**

它不生成 SQL。它只负责判断：**一份查询请求，是合法的，还是不合法。**

```
MQL = Metrics Query Language（指标查询语言）
```

> 同名的还有 MongoDB Query Language 和 Marketing Qualified Lead，**这个仓库和它们没有任何关系**。
> 这个词在国内 NL2SQL 圈里已经有人用了（比如 Aloudata 的 NL2MQL2SQL 路径），不是我们发明的。见 [`SPEC.md` §11](SPEC.md)。

---

## 它解决什么问题

让模型写 SQL，出错的方式很难查：表名编造、join key 搞反、字段理解错——**而且不报错，只是数字不对**。

换一条路：让模型只输出上面那种结构化请求，SQL 由确定性代码生成。模型碰不到表名和字段名，也就编不出来。

那么问题来了：**怎么知道模型输出的请求是对的？**

这个仓库就是回答这个的。它是一份规范 + 一个 785 行的参考校验器：

```python
from mql_spec import Validator, StaticOntology

onto = StaticOntology(
    metrics=("gmv", "order_count"),
    dimensions=("city", "order_date"),
    time_properties=("order_date",),
)

result = Validator(onto).validate({
    "mql_version": "1.1",
    "metrics": [{"name": "gmv"}],
    "dimensions": [{"name": "city", "granularity": "month"}],
})
```

```python
{"ok": False,
 "errors": [{"code": "NOT_TIME_DIMENSION",
             "path": "dimensions[0].granularity",
             "message": "'city' 不是时间属性，不能指定粒度 —— 城市没有'月'这个粒度"}],
 "warnings": [{"code": "DEFAULT_TIME",
               "path": "time_range",
               "message": "未指定时间范围，编译器将使用默认时间（通常是 t-1）..."}]}
```

上面这份输出是真的跑出来的，不是示意。

---

## 快速开始

不需要任何依赖，Python 3.10+ 即可：

```bash
git clone <this-repo> && cd mql-spec
python -m mql_spec cases/          # 跑全部用例
```

```
PASS  cases/valid/basic.json          →  合法
PASS  cases/invalid/bad_operator_eq.json  →  BAD_OPERATOR
...
37 passed, 0 failed, 37 total
```

跑测试（需要 pytest）：

```bash
pip install -e ".[dev]"
pytest
```

---

## MQL 长什么样

```json
{
  "mql_version": "1.1",
  "metrics":    [{"name": "gmv", "alias": "成交额"}],
  "dimensions": [{"name": "city"},
                 {"name": "order_date", "granularity": "month"}],
  "filters":    [{"field": "channel", "operator": "in", "value": ["堂食", "外卖"]}],
  "time_range": {"start": "-3m", "end": "yesterday"},
  "compare":    {"type": "yoy"},
  "sort":       [{"field": "gmv", "order": "desc"}],
  "limit":      20
}
```

八个字段，`metrics` 是唯一必填的。完整规定见 [`SPEC.md`](SPEC.md)。

**这里没有任何一个表名、字段名、SQL 函数。** 不是靠提示词约束的，是这个语言里根本没有这些东西的位置。

---

## 它会拒绝什么

这一节是这个仓库的重点。**下面每一条都有对应的反例文件**，在 `cases/invalid/` 里，每个文件都带一句"为什么这个例子重要"。

### ① 未知字段，直接拒绝

```json
{"mql_version": "1.1", "metrics": [{"name": "gmv"}], "compare_period": "yoy"}
```
→ `UNKNOWN_FIELD`

很多实现的校验器只检查"已知字段合不合法"，不问"有没有我认不出的字段"。后果是：写的人以为要了同比，拿到的人以为数字里含同比，**谁都没报错**。

这类错误不表现为报错，只表现为"少算了一块"，在线上最难发现。

### ② 带 SQL 特征的写法，判为越界

```json
{"metrics": [{"name": "SUM(pay_amt)"}]}        → PHYSICAL_LEAK
{"metrics": [{"name": "dw.fact.pay_amt"}]}     → PHYSICAL_LEAK
{"metrics": [{"name": "sum"}]}                 → PHYSICAL_LEAK
{"metrics": [{"name": "app_realtime_gmv"}]}    → UNKNOWN_METRIC（不是 LEAK）
```

`PHYSICAL_LEAK` 和 `UNKNOWN_METRIC` 是两个码，因为处理方式不同：前者说明越界了，不该重试；后者可以带候选让上游重试。

注意最后一行：**没有前缀黑名单**。`dwd_` / `ads_` 这类规则依赖你的数仓命名习惯，习惯一换就漏。主防线是白名单——物理字段天然不在注册表里。

### ③ 粒度只能挂在时间属性上

```json
{"dimensions": [{"name": "city", "granularity": "month"}]}     → NOT_TIME_DIMENSION
```

**这条是我实测过的一个坑。** 常见的校验器只检查 `granularity` 是不是那五个合法值之一，不检查维度本身是不是时间类型。于是上面这个请求顺利通过校验——城市没有"月"这个粒度。

到下游才炸算是好的。更糟的是下游不炸，默默按城市分组、把月粒度丢了，数字看起来还挺正常。

### ④ 要对比，就得说清跟什么比

```json
{"metrics": [{"name": "gmv"}], "compare": {"type": "yoy"}}     → BAD_COMPARE
```

没有 `time_range`、也没有带粒度的时间维度——编译器此时只能猜是按月比还是按天比，猜错就是错数。

### ⑤ SQL 符号写法，不认

```json
{"operator": "="}     → BAD_OPERATOR
{"operator": "!="}    → BAD_OPERATOR
```

`!=` / `<>` / `!==` 有歧义，十个枚举值没有。

---

## 目录结构

```
mql-spec/
├── SPEC.md              ← 主文件。这门语言的完整定义
├── schema/
│   └── mql.schema.json  ← JSON Schema，只覆盖结构和枚举
├── mql_spec/
│   ├── protocol.py      ← 本体接口：校验器只依赖这个，不依赖具体本体
│   ├── validator.py     ← 参考校验器
│   ├── errors.py        ← 错误码，调用方据此分支
│   └── static_ontology.py
├── cases/
│   ├── valid/*.json     ← 10 个合法例子
│   └── invalid/*.json   ← 27 个反例，每个都带 expect + note
└── tests/
```

**`SPEC.md` 才是这个仓库的产品，`validator.py` 是它的可执行形式。** 两者对不上时，以 `cases/` 为准。

---

## 关于校验器

785 行，只依赖一个协议，不依赖任何数据库、模型或框架。行数比我一开始估计的多——因为每个分支都配了说明，而说明比代码长。

它不是"给你用的"——你的本体、你的错误处理、你的日志都需要接。它的作用是：

1. **让规范可执行。** 一条规定如果写不进校验器、也没有对应用例，那它就是一句空话。
2. **提供一份可以直接抄的实现。** 你要是有自己的校验器，可以拿这 37 个用例去测它。

设计上有三个决定，可能和你的直觉不一样：

**错误码不带 `ok` 之外的语义，靠 `code` 分支。** 返回的是结构化对象，不是字符串列表。"指标未注册"应该反馈给用户并追问，"物理渗入"应该直接拒绝且不允许重试——这个区别只能靠 `code` 判断，靠 message 做字符串匹配太脆弱。

**阶段 2 和 3 一次报全所有错误，不短路。** 调用方通常是模型，只报第一个错误会让它一轮一轮试，每轮都是一次推理成本和延迟。

**不写时间范围不是错误，但会有一条 warning。** 编译器会套用默认时间（通常是 t-1），这可能不是本意。用 warning 提醒，而不是拒绝——因为"就要默认时间"是一个合理的意图。

---

## 它不做什么

- ❌ **不生成 SQL。** 表选择、JOIN、聚合、方言——全是编译器的事
- ❌ **不连数据库。** 校验只需要本体，不需要执行
- ❌ **不管权限。** 谁能看什么由上游决定，这里只校验请求的形状
- ❌ **不做语义理解。** 它不认识中文，输入里只有一个 JSON
- ❌ **不支持比率、指标嵌套、窗口函数、子查询。** 见 [`SPEC.md` §7](SPEC.md)，那里也写了为什么

**它确实很窄。** 窄是刻意的：窄到 schema 能一页写完、校验器能 785 行写完、读者能在 30 秒内看懂。代价写在 [`SPEC.md` §8](SPEC.md) 里，不是免责声明，是真实的取舍。

---

## 一个提醒

**"通过校验"不等于"口径选对"。**

用户说"GMV"，请求里写了 `{"name": "gmv"}`，校验通过。但如果用户语境里是下单口径，而 `gmv` 在本体里是支付口径——校验器永远不会报错，因为它只检查名字存不存在。

形式正确和语义正确是两层。这个仓库只负责第一层。第二层要靠本体里写清口径、以及在拿不准的时候**问用户**，而不是静默选一个。

---

## License

MIT
