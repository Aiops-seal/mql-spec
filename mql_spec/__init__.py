"""MQL v1.1 —— 参考校验器与规范实现。

MQL = Metrics Query Language（指标查询语言）。
与本包无关的同名缩写：MongoDB Query Language、Marketing Qualified Lead。
"""

from .errors import CompilerErrorCode, ErrorCode, ValidationError
from .protocol import OntologyProtocol
from .static_ontology import StaticOntology
from .validator import MAX_LIMIT, SUPPORTED_VERSIONS, Validator

__version__ = "1.1.0"

__all__ = [
    "CompilerErrorCode",
    "ErrorCode",
    "MAX_LIMIT",
    "OntologyProtocol",
    "SUPPORTED_VERSIONS",
    "StaticOntology",
    "ValidationError",
    "Validator",
    "__version__",
]
