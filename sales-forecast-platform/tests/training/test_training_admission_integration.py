"""Exercise the real entry point with all downstream side effects replaced."""
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from src.data.processing.training_admission import TrainingAdmissionError


@pytest.fixture
def pipeline(monkeypatch):
    dependencies = {
        'xgboost': ['XGBRegressor'],
        'src.monitoring.outlier_analysis': ['export_outlier_report'],
        'src.data.splitter': ['train_test_split'],
        'src.training.trainer': ['train_model'],
        'src.training.evaluator': ['evaluate_model'],
        'src.training.cross_validation': ['run_time_series_cv'],
        'src.preprocessing.preprocessing_factory': ['get_preprocessor'],
        'src.ml.model_registry': ['register_model', 'promote_model_to_production'],
        'src.ml.mlflow_logger': ['log_model_to_mlflow'],
        'src.deployment.production_artifact_sync': ['ProductionArtifactSync'],
    }
    mocks = {}
    for name, attrs in dependencies.items():
        module = ModuleType(name)
        for attr in attrs:
            mocks[attr] = Mock(name=attr)
            setattr(module, attr, mocks[attr])
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location('dq01_training_entry', Path('src/training/train_pipeline.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module, mocks


@pytest.mark.parametrize('bad', ['missing_column','date','numeric','identifier','duplicate','empty','aux_infinite'])
def test_rejection_stops_every_downstream_operation(pipeline, monkeypatch, bad):
    module, mocks = pipeline
    df = pd.read_csv('data/walmart_cleaned.csv', nrows=3)
    if bad == 'missing_column': df = df.drop(columns='Dept')
    elif bad == 'date': df.loc[0, 'Date'] = 'bad'
    elif bad == 'numeric': df.loc[0, 'Weekly_Sales'] = np.inf
    elif bad == 'identifier': df.loc[0, 'Store'] = 0
    elif bad == 'duplicate': df.loc[1, ['Store','Dept','Date']] = df.loc[0, ['Store','Dept','Date']]
    elif bad == 'empty': df = df.iloc[:0]
    else: df.loc[0, 'Temperature'] = np.inf
    original = df.copy(deep=True)
    monkeypatch.setattr(module.pd, 'read_csv', Mock(return_value=df))
    with pytest.raises(TrainingAdmissionError) as exc:
        module.run_training_pipeline('unused.csv')
    assert not exc.value.result.metadata['training_admitted']
    assert exc.value.issues
    for mock in mocks.values(): mock.assert_not_called()
    pd.testing.assert_frame_equal(df, original)


def test_allowed_targets_reach_preprocessor_unchanged(pipeline, monkeypatch, caplog):
    from src.data.processing import walmart_training_admission as boundary

    detector = Mock(wraps=boundary.detect_walmart_quality)
    evaluator = Mock(wraps=boundary.evaluate_training_admission)
    monkeypatch.setattr(boundary, 'detect_walmart_quality', detector)
    monkeypatch.setattr(boundary, 'evaluate_training_admission', evaluator)
    module, mocks = pipeline
    df = pd.read_csv('data/walmart_cleaned.csv', nrows=3)
    df['Weekly_Sales'] = [-10.5, 0.0, 42.0]
    original = df.copy(deep=True)
    monkeypatch.setattr(module.pd, 'read_csv', Mock(return_value=df))
    with pytest.raises(ValueError,match='canonical tabular service'):
        module.run_training_pipeline('unused.csv')
    assert 'negative Weekly_Sales' in caplog.text
    detector.assert_called_once()
    evaluator.assert_called_once()
    # Quality admission alone cannot establish a temporal/frozen-state scope.
    for mock in mocks.values(): mock.assert_not_called()
    pd.testing.assert_frame_equal(df, original)


@pytest.mark.parametrize('holiday_values', [[True, False, True], [0, 1, 0], [0.0, 1.0, 0.0]])
def test_actual_feature_schema_does_not_require_returns(monkeypatch, holiday_values):
    # The feature module imports a DB-dependent history builder that its
    # training function does not use. Replace only that unused dependency.
    builder_module = ModuleType('src.features.history_feature_builder')
    builder_module.HistoryFeatureBuilder = Mock()
    monkeypatch.setitem(sys.modules, builder_module.__name__, builder_module)
    spec = importlib.util.spec_from_file_location('dq01_actual_features', Path('src/features/feature_pipeline.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    df = pd.read_csv('data/walmart_cleaned.csv', nrows=3)
    df['IsHoliday'] = holiday_values
    df['Weekly_Sales'] = [-10.5, 0.0, 42.0]
    before = df.copy(deep=True)
    X, y = module.prepare_features(df)
    pd.testing.assert_series_equal(y.sort_index(), before['Weekly_Sales'])
    assert 'Returns' not in X
    assert X.columns.tolist() == [
        'Store', 'Dept', 'Type', 'Size', 'Temperature', 'Fuel_Price', 'CPI',
        'Unemployment', 'total_markdown', 'has_promotion', 'MarkDown1',
        'MarkDown2', 'MarkDown3', 'MarkDown4', 'MarkDown5', 'IsHoliday',
        'is_super_bowl', 'is_labor_day', 'is_thanksgiving', 'is_christmas',
        'year', 'month', 'week_of_year', 'quarter', 'lag_1', 'lag_2', 'lag_4',
        'lag_52', 'rolling_mean_4', 'rolling_mean_12', 'rolling_std_4', 'sales_trend',
    ]
    pd.testing.assert_series_equal(X['IsHoliday'].sort_index(), before['IsHoliday'])
    pd.testing.assert_frame_equal(df, before)


@pytest.mark.parametrize('value', [None, float('inf'), -float('inf'), 2, -1, 0.5, 'True', '0', 'invalid'])
def test_invalid_holiday_rejected_before_training_operations(pipeline, monkeypatch, value):
    module, mocks = pipeline
    df = pd.read_csv('data/walmart_cleaned.csv', nrows=3)
    df['IsHoliday'] = [value]*3
    before = df.copy(deep=True)
    monkeypatch.setattr(module.pd, 'read_csv', Mock(return_value=df))
    with pytest.raises(TrainingAdmissionError) as exc:
        module.run_training_pipeline('unused.csv')
    assert any(i.field == 'IsHoliday' for i in exc.value.issues)
    assert 'field=IsHoliday' in str(exc.value)
    for mock in mocks.values(): mock.assert_not_called()
    pd.testing.assert_frame_equal(df, before)


@pytest.mark.parametrize('family', [
    'lightgbm', 'transformer', 'lstm', 'gru', 'catboost', 'unknown',
    'XGBoost', '', None, True, 1, ['xgboost'],
])
def test_unsupported_family_rejects_before_read_or_downstream_work(pipeline, monkeypatch, family):
    module, mocks = pipeline
    read = Mock(side_effect=AssertionError('data must not be read'))
    monkeypatch.setattr(module.pd, 'read_csv', read)
    with pytest.raises(ValueError, match='unsupported legacy training model family'):
        module.run_training_pipeline('unused.csv', model_type=family)
    read.assert_not_called()
    for mock in mocks.values():
        mock.assert_not_called()


@pytest.mark.parametrize('explicit_family', [False, True])
def test_raw_training_cannot_bypass_canonical_temporal_admission(
    pipeline, monkeypatch, explicit_family,
):
    module, mocks = pipeline
    # Synthetic inputs and fake collaborators only: no real fit, report write,
    # registry client, MLflow server or production pointer is touched.
    df = pd.DataFrame({
        'Store': [1, 1, 1], 'Dept': [1, 1, 1],
        'Date': pd.date_range('2025-01-01', periods=3, freq='7D'),
        'Weekly_Sales': [1.0, 2.0, 3.0],
    })
    monkeypatch.setattr(module.pd, 'read_csv', Mock(return_value=df))
    monkeypatch.setattr(module, 'validate_dataframe', Mock(return_value=df))
    kwargs = {'model_type': 'xgboost'} if explicit_family else {}
    with pytest.raises(ValueError,match='canonical tabular service'):
        module.run_training_pipeline('synthetic-unused.csv', **kwargs)
    # Genuine orchestration with canonical providers and estimator stubs is in
    # test_training_entrypoint_integration; raw self-authored frames fail closed.
    for mock in mocks.values(): mock.assert_not_called()
