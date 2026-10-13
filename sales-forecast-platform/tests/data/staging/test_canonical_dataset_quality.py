from decimal import Decimal
from uuid import UUID
import pandas as pd
import pytest
from src.data.processing.canonical_dataset_quality import detect_canonical_dataset_quality
from src.data.processing.quality import QualitySeverity
from test_dataset_finalization import candidate,reference


def frame(mapping):
    from src.data.processing.canonical_mapper import CanonicalMapper
    asset=reference.ref.asset.__wrapped__()
    return CanonicalMapper(mapping).map_batch(reference.ref.batch(reference.ref.raw(),asset)).records


def detect(c,frames):
    with c[1]._quality_key_index(__import__('src.data.contracts.dataset',fromlist=['StagingBudgets']).StagingBudgets()) as keys:
        return detect_canonical_dataset_quality(frames,c[2],keys)


def test_signed_zero_detection_is_exact_and_nonmutating(candidate):
    data=frame(candidate[2]);before=data.copy(deep=True)
    issues=detect(candidate,[data.iloc[:1],data.iloc[1:]])
    assert [(v.issue_code,v.affected_count,v.severity) for v in issues]==[
        ('NEGATIVE_VALUE',1,QualitySeverity.WARNING),('ZERO_VALUE',1,QualitySeverity.INFO)]
    pd.testing.assert_frame_equal(before,data)


def test_disk_backed_cross_batch_duplicate_counts(candidate):
    data=frame(candidate[2]).iloc[:1]
    issues=detect(candidate,[data,data,data])
    counts={v.issue_code:v.affected_count for v in issues}
    assert counts['BUSINESS_KEY_DUPLICATE']==counts['DUPLICATE_RECORD']==3


@pytest.mark.parametrize('field,value,code',[('unit','wrong','SCHEMA_FAILURE'),('currency',None,'MISSING_CURRENCY'),
    ('value',float('inf'),'INVALID_NUMERIC'),('period_end','not-date','INVALID_PERIOD'),('metric','other','SCHEMA_FAILURE')])
def test_invalid_canonical_content(candidate,field,value,code):
    data=frame(candidate[2]);data[field]=data[field].astype(object);data.loc[data.index[0],field]=value
    assert code in [v.issue_code for v in detect(candidate,[data])]


def test_empty_and_missing_columns(candidate):
    assert 'SCHEMA_FAILURE' in [v.issue_code for v in detect(candidate,[])]
    data=frame(candidate[2]).drop(columns='value')
    assert 'MISSING_REQUIRED_FIELD' in [v.issue_code for v in detect(candidate,[data])]


def test_unicode_and_nested_values_fail_closed(candidate):
    data=frame(candidate[2]);data['promotion']=data.promotion.astype(object)
    data.at[data.index[0],'promotion']='\ud800';data.at[data.index[1],'promotion']=[1]
    assert {'INVALID_UNICODE','UNSUPPORTED_FIELD_TYPE'} <= {v.issue_code for v in detect(candidate,[data])}


def test_exact_decimal_signs_do_not_use_float(candidate):
    from decimal import localcontext
    data=frame(candidate[2]);data['value']=pd.Series([Decimal('-123456789012345678901234567890.1234'),Decimal('-0.0000'),Decimal('4.0000')],index=data.index,dtype=object)
    with localcontext() as context:
        context.prec=2
        issues=detect(candidate,[data])
    assert [(v.issue_code,v.affected_count) for v in issues]==[('NEGATIVE_VALUE',1),('ZERO_VALUE',1)]
