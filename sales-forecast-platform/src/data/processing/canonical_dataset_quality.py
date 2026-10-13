"""Bounded canonical detection; no mutation, policy evaluation or admission."""
from collections.abc import Iterable
from decimal import Decimal
import math

import pandas as pd
import numpy as np

from src.data.processing.quality import QualityIssue, QualityIssueType, QualitySeverity
from src.data.processing.tabular_quality import detect_tabular_quality

_REQUIRED = ('series_id', 'period_start', 'period_end', 'frequency', 'metric', 'value',
             'unit', 'currency', 'value_status', 'source_record_id', 'lineage_id')


def detect_canonical_dataset_quality(batches: Iterable[pd.DataFrame], mapping, record_key):
    """Aggregate only bounded issue summaries; duplicate keys are disk-backed.

    The repository separately verifies capture completion, payloads and lineage.
    Decimal finite/sign checks are exact: no float intermediary. Universal sales
    meanings are never inferred from negative values.
    """
    counts = {}
    total = 0
    expected_columns = None
    def add(code, kind, field=None, count=1, severity=QualitySeverity.ERROR):
        key = (code, kind, field, severity)
        counts[key] = counts.get(key, 0) + int(count)
    for frame in batches:
        total += len(frame)
        columns = tuple(frame.columns)
        if expected_columns is None: expected_columns = columns
        elif columns != expected_columns: add('SCHEMA_FAILURE', QualityIssueType.INVALID)
        for issue in detect_tabular_quality(frame, required_columns=_REQUIRED, required_numeric_columns=(),
                nonnull_columns=('series_id','period_end','metric','value','unit','source_record_id','lineage_id') + mapping.required_auxiliary_fields,
                date_column='period_start', business_key=()):
            add(issue.issue_code, issue.issue_type, issue.field, issue.affected_count, issue.severity)
        if not set(_REQUIRED) <= set(frame): continue
        for field in frame.columns:
            for value in frame[field]:
                if type(value) is str:
                    try:value.encode('utf-8',errors='strict')
                    except UnicodeError:add('INVALID_UNICODE',QualityIssueType.INVALID,field)
                elif type(value) in (dict,list,tuple):add('UNSUPPORTED_FIELD_TYPE',QualityIssueType.INVALID,field)
        for field, wanted in [('metric',mapping.target_metric),('unit',mapping.measurement.unit),
                              ('frequency',mapping.frequency.value),('value_status','actual')]:
            bad = ~frame[field].eq(wanted)
            if bad.any(): add('SCHEMA_FAILURE', QualityIssueType.INCONSISTENT, field, bad.sum())
        if mapping.currency_required and frame.currency.isna().any():
            add('MISSING_CURRENCY', QualityIssueType.MISSING,'currency',frame.currency.isna().sum())
        mismatch = ~frame.currency.eq(mapping.measurement.currency)
        if mapping.measurement.currency is None: mismatch = frame.currency.notna()
        if mismatch.any(): add('CURRENCY_MISMATCH',QualityIssueType.INCONSISTENT,'currency',mismatch.sum())
        try:
            start = pd.to_datetime(frame.period_start,errors='raise')
            end = pd.to_datetime(frame.period_end,errors='raise',format='ISO8601')
            invalid = start.isna() | end.isna() | ~(end > start)
            if invalid.any(): add('INVALID_PERIOD',QualityIssueType.INVALID,'period_end',invalid.sum())
        except (ValueError,TypeError): add('INVALID_PERIOD',QualityIssueType.INVALID,'period_end',len(frame))
        negatives = zeros = invalid = 0
        for value in frame.value:
            if type(value) is Decimal:
                finite = value.is_finite()
            elif type(value) in (float,int,np.int8,np.int16,np.int32,np.int64,np.uint8,np.uint16,np.uint32,np.uint64,np.float16,np.float32,np.float64): finite = math.isfinite(value)
            else: finite = False
            if not finite: invalid += 1
            elif value < 0: negatives += 1
            elif value == 0: zeros += 1
        if invalid: add('INVALID_NUMERIC',QualityIssueType.INVALID,'value',invalid)
        if negatives: add('NEGATIVE_VALUE',QualityIssueType.INVALID,'value',negatives,QualitySeverity.WARNING)
        if zeros: add('ZERO_VALUE',QualityIssueType.INVALID,'value',zeros,QualitySeverity.INFO)
        # JSON serialization provides collision-safe structured business identities.
        import json
        for series,period,metric,source in zip(frame.series_id,frame.period_start,frame.metric,frame.source_record_id):
            for kind,identity in [('business',json.dumps([str(series),str(period),metric],separators=(',',':'))),
                                  ('source',json.dumps([str(source)],separators=(',',':')))]:
                affected = record_key(kind,identity)
                if affected:
                    add('BUSINESS_KEY_DUPLICATE' if kind=='business' else 'DUPLICATE_RECORD',QualityIssueType.DUPLICATE,count=affected)
    if not total: add('SCHEMA_FAILURE',QualityIssueType.INVALID,count=0)
    return [QualityIssue(issue_code=code,issue_type=kind,field=field,severity=severity,affected_count=count,
                message=f'{code}: canonical dataset ({count})',details={'metric':mapping.policy_metric})
            for (code,kind,field,severity),count in counts.items()]
