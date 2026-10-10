"""Frozen baseline captured from prepare_features at commit 956fcb2."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd

spec = importlib.util.spec_from_file_location('reference_input', Path('tests/fixtures/reference_training.py'))
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)


def test_complete_reference_feature_baseline(monkeypatch):
    frame = reference.reference_frame()
    before = frame.copy(deep=True)
    X, y = reference.active_features(monkeypatch)(frame)
    frozen = json.loads(Path('tests/fixtures/reference_training_956fcb2.json').read_text())
    assert frozen['baseline_commit'] == '956fcb28d799b8c1da3e4f0aa48b2295dbb4d440'
    assert X.columns.tolist() == frozen['columns'] and len(X.columns) == 32
    assert [str(t) for t in X.dtypes] == frozen['dtypes']
    assert X.index.tolist() == frozen['index']
    assert X.index.name == frozen['index_name']
    expected = pd.DataFrame(frozen['values'], columns=frozen['columns'], index=X.index)
    for column, dtype in zip(frozen['columns'], frozen['dtypes']):
        expected[column] = expected[column].astype(dtype)
    pd.testing.assert_frame_equal(X, expected, check_exact=True)
    expected_y = pd.Series(frozen['target'], index=X.index, name=frozen['target_name'], dtype=frozen['target_dtype'])
    pd.testing.assert_series_equal(y, expected_y, check_exact=True)
    pd.testing.assert_frame_equal(frame, before)
    pd.testing.assert_series_equal(y, before.loc[X.index, 'Weekly_Sales'])
    assert (y < 0).sum() == 3 and (y == 0).sum() == 3


def test_history_baseline_is_series_local_and_preserves_existing_math(monkeypatch):
    X, y = reference.active_features(monkeypatch)(reference.reference_frame())
    for (_, _), rows in X.groupby(['Store', 'Dept']):
        assert len(rows) == 60
        target = y.loc[rows.index]
        assert rows['lag_52'].iloc[52] == target.iloc[0]
        for offset in [1, 2, 4, 52]:
            assert rows[f'lag_{offset}'].iloc[:offset].eq(0).all()
        assert rows['rolling_mean_4'].iloc[10] == target.iloc[6:10].mean()
        assert rows['rolling_mean_12'].iloc[20] == target.iloc[8:20].mean()
        np.testing.assert_allclose(rows['rolling_std_4'].iloc[10], target.iloc[6:10].std(ddof=1), rtol=1e-14)
    assert X[['is_super_bowl', 'is_labor_day', 'is_thanksgiving', 'is_christmas']].eq(0).all().all()
    assert X.loc[X.Store.eq(1) & X.Dept.eq(1), 'Type'].eq(2).all()
    assert X.loc[X.Store.eq(1) & X.Dept.eq(2), 'Type'].eq(0).all()
    assert X.loc[X.Store.eq(2), 'Type'].eq(1).all()
    assert not X.isna().any().any()
