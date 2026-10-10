"""Deterministic reference input only; no production dataset configuration."""
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd


def reference_frame():
    rows = []
    for store, dept, category in [(1, 1, 'C'), (1, 2, 'A'), (2, 1, 'B')]:
        for step in range(60):
            rows.append(dict(
                Store=store, Dept=dept, Type=category, Size=1000+store,
                Date=pd.Timestamp('2020-01-03')+pd.Timedelta(weeks=step),
                Weekly_Sales=float(-12.5 if step == 5 else 0 if step == 6 else store*100+dept*10+step),
                Temperature=np.nan if step % 9 == 0 else 20.0+step/10,
                Fuel_Price=3.5, CPI=200.0, Unemployment=7.0,
                IsHoliday=step % 3 == 0,
                **{f'MarkDown{i}': np.nan if step % 7 == 0 else float(i*step) for i in range(1,6)},
            ))
    frame = pd.DataFrame(rows)
    frame.index = pd.Index(np.arange(len(frame))*3+11, name='source_row')
    return frame.sample(frac=1, random_state=17)


def active_features(monkeypatch):
    # This unused training import otherwise brings operational DB dependencies.
    stub = ModuleType('src.features.history_feature_builder')
    stub.HistoryFeatureBuilder = object
    monkeypatch.setitem(sys.modules, stub.__name__, stub)
    spec = importlib.util.spec_from_file_location('r01_reference_features', Path('src/features/feature_pipeline.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.prepare_features
