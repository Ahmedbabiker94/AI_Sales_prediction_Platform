"""Fresh-process import/collection guards; never execute real legacy training."""
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from unittest.mock import Mock

import pytest


PROJECT = Path(__file__).resolve().parents[2]

GUARDS = r'''
import importlib.abc
import os
import sys

# Reject even importing operational or executable training dependencies. This
# catches client/engine construction as well as eager fitting and sync imports.
blocked = (
    "xgboost", "mlflow", "sqlalchemy", "psycopg2", "apscheduler",
    "src.database", "src.deployment", "src.ml", "src.monitoring",
    "src.preprocessing", "src.training.trainer", "src.training.evaluator",
    "src.training.cross_validation",
)
class NoOperationalImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + ".") for p in blocked):
            raise AssertionError("operational dependency imported: " + fullname)
sys.meta_path.insert(0, NoOperationalImports())

def audit(event, args):
    if event in ("socket.connect", "socket.bind", "subprocess.Popen", "os.system"):
        raise AssertionError("external operation: " + event)
    if event == "open":
        mode, flags = args[1], args[2]
        if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
        ):
            raise AssertionError("file write during import/collection")
sys.addaudithook(audit)
'''


@pytest.mark.parametrize('collect', [False, True])
def test_fresh_process_import_and_collection_are_inert(collect):
    script = GUARDS + '''
import src.training.train_pipeline
import src.training.test_training_pipeline
assert not any(name == p or name.startswith(p + ".")
               for name in sys.modules for p in blocked)
'''
    if collect:
        # Disable pytest's own file-backed capture/logging in this subprocess;
        # the audit guard continues to reject all writes by collected modules.
        script += '''
import pytest
code = pytest.main(["--collect-only", "-q", "-s", "-p", "no:cacheprovider", "-p", "no:logging",
                    "src/training/test_training_pipeline.py"])
assert code == pytest.ExitCode.NO_TESTS_COLLECTED, code
'''
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
               PYTHONPATH=str(PROJECT))
    result = subprocess.run([sys.executable, '-c', script], cwd=PROJECT, env=env,
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_compatibility_command_requires_an_explicit_path(monkeypatch):
    from src.training import test_training_pipeline as command
    fake = ModuleType('src.training.train_pipeline')
    fake.run_training_pipeline = Mock()
    monkeypatch.setitem(sys.modules, fake.__name__, fake)
    with pytest.raises(SystemExit) as exc:
        command.main([])
    assert exc.value.code == 2
    fake.run_training_pipeline.assert_not_called()


def test_compatibility_command_invokes_training_only_when_called(monkeypatch):
    from src.training import test_training_pipeline as command
    fake = ModuleType('src.training.train_pipeline')
    fake.run_training_pipeline = Mock(return_value={'model': 'fake'})
    monkeypatch.setitem(sys.modules, fake.__name__, fake)
    assert command.main(['synthetic-unused.csv']) == {'model': 'fake'}
    fake.run_training_pipeline.assert_called_once_with('synthetic-unused.csv', model_type='xgboost')


@pytest.mark.parametrize('family', ['lightgbm', 'transformer', 'lstm', 'gru', 'unknown'])
def test_compatibility_command_rejects_unsupported_families(monkeypatch, family):
    from src.training import test_training_pipeline as command
    fake = ModuleType('src.training.train_pipeline')
    fake.run_training_pipeline = Mock()
    monkeypatch.setitem(sys.modules, fake.__name__, fake)
    with pytest.raises(SystemExit) as exc:
        command.main(['synthetic-unused.csv', '--model-type', family])
    assert exc.value.code == 2
    fake.run_training_pipeline.assert_not_called()


@pytest.mark.parametrize('overwrite', [False, True])
def test_compatibility_command_explicit_report_options(monkeypatch, tmp_path, overwrite):
    from src.training import test_training_pipeline as command
    from src.monitoring.outlier_analysis import OutlierReportOptions
    fake = ModuleType('src.training.train_pipeline')
    fake.run_training_pipeline = Mock(return_value={'model': 'fake'})
    monkeypatch.setitem(sys.modules, fake.__name__, fake)
    path = str(tmp_path / 'report.csv')
    argv = ['synthetic-unused.csv', '--outlier-report', path]
    if overwrite:
        argv.append('--overwrite-outlier-report')
    assert command.main(argv) == {'model': 'fake'}
    fake.run_training_pipeline.assert_called_once_with(
        'synthetic-unused.csv', model_type='xgboost',
        outlier_report=OutlierReportOptions(path, overwrite=overwrite))
    assert list(tmp_path.iterdir()) == []


def test_compatibility_command_rejects_overwrite_without_report(monkeypatch):
    from src.training import test_training_pipeline as command
    fake = ModuleType('src.training.train_pipeline')
    fake.run_training_pipeline = Mock()
    monkeypatch.setitem(sys.modules, fake.__name__, fake)
    with pytest.raises(SystemExit) as exc:
        command.main(['synthetic-unused.csv', '--overwrite-outlier-report'])
    assert exc.value.code == 2
    fake.run_training_pipeline.assert_not_called()
