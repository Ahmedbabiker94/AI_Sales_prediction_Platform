"""Real legacy orchestration, fake estimator only; no native fit or operations.

Combined-partition/category leakage remains deferred to M3. Legacy wrapper
serialization remains deferred to M6. These tests do not certify either issue.
"""
import importlib
import importlib.abc
import importlib.util
import os
from pathlib import Path
import sys
from types import ModuleType
from unittest.mock import Mock

import numpy as np
import pytest

from src.monitoring.outlier_analysis import OutlierReportOptions


@pytest.fixture
def execution(monkeypatch, tmp_path):
    state = {'active': False, 'external': [], 'fits': [], 'fail_fit': None,
             'predictions': 0, 'fail_predict': None}

    def forbidden(*args, **kwargs):
        state['external'].append('external operation')
        raise AssertionError('external operation forbidden')

    class Engine:
        connect = forbidden
        begin = forbidden
    db = ModuleType('src.database.db')
    db.engine = Engine()
    monkeypatch.setitem(sys.modules, db.__name__, db)

    blocked = ('mlflow', 'src.ml.model_registry', 'src.ml.mlflow_logger',
               'src.training.registry', 'src.deployment', 'apscheduler')
    class NoOperations(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if any(fullname == p or fullname.startswith(p + '.') for p in blocked):
                forbidden()
    finder = NoOperations()
    monkeypatch.setattr(sys, 'meta_path', [finder, *sys.meta_path])
    for name in list(sys.modules):
        if any(name == p or name.startswith(p + '.') for p in blocked):
            stub = ModuleType(name)
            stub.__getattr__ = lambda name: forbidden()
            monkeypatch.setitem(sys.modules, name, stub)

    class Estimator:
        def __init__(self, **kwargs):
            pass
        def fit(self, X, y):
            state['fits'].append((len(X), tuple(X.columns)))
            if state['fail_fit'] == len(state['fits']):
                raise RuntimeError('fake estimator failure')
            self.value = float(np.mean(y))
            return self
        def predict(self, X):
            state['predictions'] += 1
            if state['fail_predict'] == state['predictions']:
                raise RuntimeError('fake prediction failure')
            return np.full(len(X), self.value)
    xgb = ModuleType('xgboost')
    xgb.XGBRegressor = Estimator
    monkeypatch.setitem(sys.modules, 'xgboost', xgb)
    wrapper_name = 'src.ml.models.xgboost_model'
    wrapper_existed = wrapper_name in sys.modules
    wrapper = importlib.import_module(wrapper_name)
    monkeypatch.setattr(wrapper, 'XGBRegressor', Estimator)

    spec = importlib.util.spec_from_file_location('m1_reference', 'tests/fixtures/reference_training.py')
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    frame = reference.reference_frame()
    frame['unrelated_private_column'] = 'NEVER_EXPORT_THIS'
    data_path = tmp_path / 'synthetic.csv'
    frame.to_csv(data_path, index=False)

    pipeline = importlib.import_module('src.training.train_pipeline')
    # Wrap actual implementations, preserving their behavior and call graph.
    spies = {}
    for module_name, name in (
        ('src.training.train_pipeline', 'validate_dataframe'),
        ('src.data.splitter', 'train_test_split'),
        ('src.preprocessing.tree_preprocessor', 'prepare_features'),
        ('src.training.cross_validation', 'run_time_series_cv'),
        ('src.training.trainer', 'train_model'),
        ('src.training.evaluator', 'evaluate_model'),
    ):
        module = importlib.import_module(module_name)
        spies[name] = Mock(wraps=getattr(module, name))
        monkeypatch.setattr(module, name, spies[name])

    # Audit events omit dir_fd for open(). Resolve the actual directory-relative
    # destination in a wrapper, then permit that exact open event only.
    opening = []
    real_open = os.open
    def checked_open(path, flags, *args, dir_fd=None, **kwargs):
        writing = flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
        if state['active'] and writing:
            base = Path(os.readlink(f'/proc/self/fd/{dir_fd}')) if dir_fd is not None else Path.cwd()
            resolved = (base / path).absolute()
            if not resolved.is_relative_to(tmp_path):
                forbidden()
        opening.append(path)
        try:
            return real_open(path, flags, *args, dir_fd=dir_fd, **kwargs)
        finally:
            opening.pop()
    monkeypatch.setattr(os, 'open', checked_open)

    def audit(event, args):
        if not state['active']:
            return
        if event in ('socket.connect', 'socket.bind', 'subprocess.Popen', 'os.system'):
            forbidden()
        if event == 'open':
            path, mode, flags = args
            writing = ((isinstance(mode, str) and any(c in mode for c in 'wax+')) or
                       isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            if writing and not isinstance(path, int):
                if opening and path == opening[-1]:
                    return
                if not Path(path).absolute().is_relative_to(tmp_path):
                    forbidden()
    sys.addaudithook(audit)
    monkeypatch.setattr(sys, 'dont_write_bytecode', True)

    def run(**kwargs):
        state['active'] = True
        try:
            return pipeline.run_training_pipeline(str(data_path), **kwargs)
        finally:
            state['active'] = False
    try:
        yield run, state, spies, tmp_path
    finally:
        state['active'] = False
        if not wrapper_existed:
            sys.modules.pop(wrapper_name, None)


def assert_training(result, state, spies):
    assert result['model_type'] == 'xgboost'
    assert len(state['fits']) == 6  # five real CV iterations, one final fake fit
    assert state['predictions'] == 6
    assert all(len(columns) == 32 for _, columns in state['fits'])
    assert state['external'] == []
    for spy in spies.values():
        spy.assert_called_once()
    assert set(result['metrics']) == {'mae', 'rmse', 'r2'}


def test_actual_orchestration_has_no_default_reporting(execution, monkeypatch):
    run, state, spies, root = execution
    report = importlib.import_module('src.monitoring.outlier_analysis')
    export = Mock(side_effect=AssertionError('default reporting forbidden'))
    monkeypatch.setattr(report, 'export_outlier_report', export)
    result = run()
    assert_training(result, state, spies)
    assert result['outlier_results'] == {'status': 'DISABLED'}
    export.assert_not_called()
    assert sorted(p.name for p in root.iterdir()) == ['synthetic.csv']


def test_actual_orchestration_opt_in_report(execution):
    run, state, spies, root = execution
    path = root / 'report.csv'
    result = run(outlier_report=OutlierReportOptions(str(path)))
    assert_training(result, state, spies)
    assert result['outlier_results']['status'] == 'WRITTEN'
    payload = path.read_bytes()
    assert len(payload) == result['outlier_results']['bytes'] <= 65536
    assert b'NEVER_EXPORT_THIS' not in payload
    assert b'unrelated_private_column' not in payload


@pytest.mark.parametrize('failure', ['byte_limit', 'exists', 'write', 'cleanup', 'invalid_options'])
def test_report_failures_preserve_actual_training_result(execution, monkeypatch, failure, capsys, caplog):
    run, state, spies, root = execution
    report = importlib.import_module('src.monitoring.outlier_analysis')
    path = root / 'report.csv'
    options = OutlierReportOptions(str(path))
    if failure == 'byte_limit':
        options = OutlierReportOptions(str(path), max_bytes=1)
    elif failure == 'exists':
        path.write_bytes(b'original')
    elif failure == 'write':
        monkeypatch.setattr(report.os, 'fsync', Mock(side_effect=OSError('SECRET_WRITE')))
    elif failure == 'cleanup':
        real_unlink = report.os.unlink
        calls = []
        def unlink(*a, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise OSError('SECRET_CLEANUP')
            return real_unlink(*a, **kw)
        monkeypatch.setattr(report.os, 'unlink', unlink)
    else:
        options = object()
    result = run(outlier_report=options)
    assert_training(result, state, spies)
    assert result['outlier_results'] == {'status': 'ERROR', 'error': 'outlier report export failed'}
    captured = capsys.readouterr()
    assert 'SECRET_' not in captured.out + captured.err + caplog.text + repr(result['outlier_results'])
    assert not list(root.glob('.outlier-*.tmp'))
    if failure == 'exists':
        assert path.read_bytes() == b'original'
    elif failure != 'cleanup':
        assert not path.exists()
    else:
        # Cleanup failed after atomic create-only publication: complete file,
        # error status, and finally retries removal of the private temporary link.
        assert path.read_bytes().startswith(b'Store,Dept,Date,actual,')


@pytest.mark.parametrize('fit_call', [1, 6])
def test_cv_or_final_fit_failure_has_no_reporting_or_production_work(execution, monkeypatch, fit_call):
    run, state, spies, root = execution
    state['fail_fit'] = fit_call
    report = importlib.import_module('src.monitoring.outlier_analysis')
    export = Mock(side_effect=AssertionError('report after failed training'))
    monkeypatch.setattr(report, 'export_outlier_report', export)
    with pytest.raises(RuntimeError, match='fake estimator failure'):
        run(outlier_report=OutlierReportOptions(str(root / 'report.csv')))
    export.assert_not_called()
    assert state['external'] == []
    assert sorted(p.name for p in root.iterdir()) == ['synthetic.csv']


@pytest.mark.parametrize('phase', ['prediction', 'evaluation'])
def test_prediction_or_evaluation_failure_never_exports(execution, monkeypatch, phase):
    run, state, spies, root = execution
    report = importlib.import_module('src.monitoring.outlier_analysis')
    export = Mock(side_effect=AssertionError('report after failed evaluation'))
    monkeypatch.setattr(report, 'export_outlier_report', export)
    if phase == 'prediction':
        state['fail_predict'] = 6
    else:
        spies['evaluate_model'].side_effect = RuntimeError('fake evaluation failure')
    with pytest.raises(RuntimeError):
        run(outlier_report=OutlierReportOptions(str(root / 'report.csv')))
    export.assert_not_called()
    assert len(state['fits']) == 6
    assert state['external'] == []
    assert sorted(p.name for p in root.iterdir()) == ['synthetic.csv']
