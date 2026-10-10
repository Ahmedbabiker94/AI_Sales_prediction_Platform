"""Walmart training orchestration and the explicitly approved DQ-01 exception."""

from src.data.processing.quality import QualityAction
from src.data.processing.sales_quality import build_general_sales_quality_policy
from src.data.processing.training_admission import (
    WarningAuthorization, evaluate_training_admission, enforce_training_admission,
)
from src.data.processing.walmart_quality import detect_walmart_quality

WALMART_TRAINING_CONTEXT = "walmart"
NEGATIVE_SALES_AUTHORIZATION = WarningAuthorization(
    execution_context=WALMART_TRAINING_CONTEXT,
    issue_code="NEGATIVE_VALUE",
    field="Weekly_Sales",
    metric="sales",
    action=QualityAction.KEEP,
)


def evaluate_walmart_training_admission(issues, policy=None):
    """Select sales policy and authorize only the approved KEEP warning."""
    return evaluate_training_admission(
        issues,
        policy if policy is not None else build_general_sales_quality_policy(),
        execution_context=WALMART_TRAINING_CONTEXT,
        warning_authorizations=(NEGATIVE_SALES_AUTHORIZATION,),
    )


def admit_walmart_training(df):
    """Detect once, evaluate once, enforce once; preserve the input frame."""
    issues = detect_walmart_quality(df)
    result = evaluate_walmart_training_admission(issues)
    enforce_training_admission(issues, result)
    return result
