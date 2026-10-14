from uuid import UUID

import pandas as pd
import pytest

from src.data.contracts.features import FeatureConfiguration
from src.data.contracts.forecast import ForecastConfig
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline


def feature_config(**changes):
    calculations = [
        dict(output="previous", operation="lag", inputs=["value"], parameters={"offset":1}),
        dict(output="previous_four", operation="lag", inputs=["value"], parameters={"offset":4}),
        dict(output="mean_four", operation="rolling_mean", inputs=["value"], parameters={"window":4,"offset":1,"min_periods":4}),
        dict(output="std_four", operation="rolling_std", inputs=["value"], parameters={"window":4,"offset":1,"min_periods":4,"ddof":1}),
        dict(output="trend", operation="difference", inputs=["previous","previous_four"], parameters={}),
        dict(output="month", operation="calendar", inputs=["period_start"], parameters={"component":"month"}),
    ]
    outputs = ("product_class", "previous", "previous_four", "mean_four", "std_four", "trend", "month")
    values = dict(configuration_id="causal-reference",version="2.0",target_metric="reported",
        canonical_inputs=("value","period_start","period_end","product_class"), calculations=calculations,
        history_order="oldest_first",lag_offsets=(1,2,4,52),rolling_windows=(4,12),
        missing_value_behavior={name:"reject" for name in outputs},category_encoding={"product_class":"ordinal"},
        ordered_outputs=outputs,output_dtypes={name:("int8" if name=="product_class" else "int32" if name=="month" else "float64") for name in outputs},
        execution=dict(measurement=dict(metric="reported",unit="amount",currency="USD"),currency_required=True,
            metric_meaning="signed reported sales",sign_semantics="preserve reported sign",dimension_fields=("product_class",),
            categories=[dict(field="product_class",unknown="reject",missing="reject")],exogenous=[],numeric_conversion="lossless_float64",
            historical_value_availability="available_at",
            temporal=dict(frequency="weekly",calendar="gregorian",timezone="UTC",period_anchor="start",week_start=0,
                lag_basis="period",gap_policy="reject",minimum_history=4,cold_start="reject",recursive_strategy="provided_predictions")))
    values.update(changes)
    return FeatureConfiguration(**values)


def observations(organizations=(1,), series=(10,11), *, frequency="weekly", timezone="UTC", start="2020-01-06", count=8):
    step=1 if frequency=="daily" else 7
    rows=[]
    for org in organizations:
        for index,sid in enumerate(series):
            for n in range(count):
                at=pd.Timestamp(start,tz=timezone)+pd.DateOffset(days=step*n)
                rows.append(dict(organization_id=UUID(int=org),series_id=UUID(int=sid),metric="reported",unit="amount",currency="USD",frequency=frequency,
                    period_start=at,period_end=at+pd.DateOffset(days=step),available_at=at+pd.DateOffset(days=step),value=float([10,-20,0,40,50,60,70,80][n%8]+index*100+1000*(org-1)),product_class="B" if index==0 else "C"))
    return pd.DataFrame(rows)


def requests(frame):
    return frame.drop_duplicates(["organization_id","series_id"])[["organization_id","series_id","metric","unit","currency","frequency","product_class"]].reset_index(drop=True)


@pytest.fixture
def setup():
    config=feature_config();history=observations();origin=history.period_end.max()
    engine=CanonicalFeaturePipeline(config)
    state=engine.fit_state(history,training_cutoff=origin,partition_reference="synthetic-training-partition")
    return config,history,requests(history),origin,engine,state


@pytest.fixture
def forecast():
    return ForecastConfig(forecast_frequency="weekly",forecast_horizon=1)
