"""No-Docker safety tests for B5-4-1 opt-in PostgreSQL host stage.

These verify lifecycle registration and failure propagation only. They DO NOT
establish that a real PostgreSQL database or container was ever started.
"""
import importlib
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.fixture
def stage(monkeypatch, tmp_path):
    postgres_tests = Path(__file__).resolve().parents[1] / 'postgres'
    monkeypatch.syspath_prepend(str(postgres_tests))
    runner = importlib.import_module('run_b5_4_1_postgres')
    original = importlib.import_module('run_postgres')
    run_id = uuid4()
    record = {'run_hex': run_id.hex, 'creation_attempts': [],
              'b5_4_1_evidence_dir': str(tmp_path), 'operations': []}
    token = original._LIFECYCLE.set(record)
    try:
        yield SimpleNamespace(runner=runner, original=original, record=record,
                              run_id=run_id, directory=tmp_path)
    finally:
        original._LIFECYCLE.reset(token)


def invoke(s):
    return s.runner.reporting_stage(
        server='r7-05b3-' + s.run_id.hex + '-server',
        socket='r7-05b3-' + s.run_id.hex + '-socket',
        client='sha256:synthetic-test-only', envfile=s.directory/'test.env',
        run_id=s.run_id, database='r7_05b3_' + s.run_id.hex,
        record=s.record,
    )


def test_extra_container_registered_before_any_execution(stage, monkeypatch):
    s = stage
    expected = 'r7-05b3-' + s.run_id.hex + '-b5-4-1'
    observed = []

    def fake_command(argv, **kwargs):
        observed.append((argv, kwargs))
        assert s.record['extra_containers'] == [expected]
        assert argv[:4] == ['docker', 'run', '--name', expected]
        assert '--network=none' in argv and '--read-only' in argv
        assert '--cap-drop=ALL' in argv
        assert '--security-opt=no-new-privileges' in argv
        assert '-p' not in argv and '--publish' not in argv
        assert argv[-len(s.runner.TESTS):] == list(s.runner.TESTS)
        return SimpleNamespace(returncode=0, stdout='4 passed', stderr='')

    monkeypatch.setattr(s.runner, 'command', fake_command)
    invoke(s)
    assert len(observed) == 1
    assert s.record['operations'][0]['exit_code'] == 0
    assert (s.directory/'b5-4-1.stdout').read_text() == '4 passed'


def test_failed_pg_test_still_has_owned_container_for_cleanup(stage, monkeypatch):
    s = stage
    expected = 'r7-05b3-' + s.run_id.hex + '-b5-4-1'

    def failed(argv, **kwargs):
        assert expected in s.record['extra_containers']
        return SimpleNamespace(returncode=1, stdout='failed', stderr='error')

    monkeypatch.setattr(s.runner, 'command', failed)
    with pytest.raises(RuntimeError, match='PostgreSQL tests failed'):
        invoke(s)
    assert s.record['extra_containers'] == [expected]
    assert s.record['operations'][0]['exit_code'] == 1
    assert (s.directory/'b5-4-1.stderr').read_text() == 'error'


@pytest.mark.parametrize('interrupt', [KeyboardInterrupt, SystemExit, TimeoutError])
def test_interrupted_docker_call_retains_cleanup_identity(stage, monkeypatch, interrupt):
    s = stage
    expected = 'r7-05b3-' + s.run_id.hex + '-b5-4-1'

    def fail(argv, **kwargs):
        assert s.record['extra_containers'] == [expected]
        raise interrupt('simulated failure')

    monkeypatch.setattr(s.runner, 'command', fail)
    with pytest.raises(interrupt):
        invoke(s)
    assert s.record['extra_containers'] == [expected]
    assert not s.record['operations']


def test_runner_cannot_start_without_original_lifecycle(stage, monkeypatch):
    s = stage
    token = s.original._LIFECYCLE.set(None)
    try:
        def forbidden(*args, **kwargs):
            pytest.fail('Docker command must never be called without lifecycle')

        monkeypatch.setattr(s.runner, 'command', forbidden)
        with pytest.raises(RuntimeError, match='lifecycle required'):
            invoke(s)
        assert not s.record.get('extra_containers')
    finally:
        s.original._LIFECYCLE.reset(token)
