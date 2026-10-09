"""Phase 0.5 - Quality processing contracts."""
from enum import Enum
from typing import Any
from pydantic import Field
from src.data.contracts.common import ContractModel

class QualityAction(str, Enum):
    KEEP = "keep"
    REPAIR = "repair"
    IMPUTE = "impute"
    NORMALIZE = "normalize"
    DEDUPLICATE = "deduplicate"
    QUARANTINE = "quarantine"
    REJECT = "reject"

class QualityIssueType(str, Enum):
    MISSING = "missing"
    DUPLICATE = "duplicate"
    INVALID = "invalid"
    ANOMALY = "anomaly"
    INCONSISTENT = "inconsistent"

class QualitySeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

class SourceType(str, Enum):
    CSV = "csv"
    ERP = "erp"
    API = "api"
    DATABASE = "database"
    S3 = "s3"
    EXCEL = "excel"
    DOCUMENT = "document"

class QualityConditions(ContractModel):
    field: str | None = Field(default=None, min_length=1, max_length=128)
    metric: str | None = Field(default=None, min_length=1, max_length=128)
    source_type: SourceType | None = None

class QualityIssue(ContractModel):
    issue_code: str = Field(min_length=1, max_length=128)
    issue_type: QualityIssueType
    severity: QualitySeverity
    field: str | None = Field(default=None, min_length=1, max_length=128)
    affected_count: int = Field(default=0, ge=0)
    message: str = Field(min_length=1, max_length=1024)
    rule_id: str | None = Field(default=None, min_length=1, max_length=128)
    details: dict[str, Any] = Field(default_factory=dict)

class QualityRule(ContractModel):
    rule_id: str = Field(min_length=1, max_length=128)
    issue_code: str = Field(min_length=1, max_length=128)
    action: QualityAction
    enabled: bool = True
    conditions: QualityConditions = Field(default_factory=QualityConditions)
    parameters: dict[str, Any] = Field(default_factory=dict)

class QualityPolicy(ContractModel):
    policy_id: str = Field(min_length=1, max_length=128)
    version: str = Field(default="1.0.0", min_length=1, max_length=32)
    rules: list[QualityRule] = Field(min_length=1)
    market_profile_code: str | None = Field(default=None, min_length=2, max_length=16)

class QualityGateDecision(str, Enum):
    PASS = "pass"
    PASS_WITH_WARNINGS = "pass_with_warnings"
    REJECT = "reject"

class QualityGateResult(ContractModel):
    decision: QualityGateDecision
    reason: str = Field(min_length=1, max_length=1024)
    issue_codes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
