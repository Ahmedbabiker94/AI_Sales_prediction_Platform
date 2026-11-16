"""F-04 offline negative tests of disposable PostgreSQL runner safety.

No real Docker, database, network or operational model execution.
Passing these tests does not approve the accepted runner or rebaseline.
"""
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'postgres'))
    return importlib.import_module('run_postgres')


@pytest.mark.parametrize('kind,metadata', [
    ('container', {'Config': {'Labels': {'r7_05b3_run': 'foreign-run'}}}),
    ('volume', {'Labels': {'r7_05b3_run': 'foreign-run'}}),
    ('container', {'Config': {'Labels': {}}}),
    ('volume', {'Labels': {}}),
])
def test_foreign_or_unlabelled_resource_never_deleted(runner, monkeypatch, kind, metadata):
    run_id = uuid4()
    seen = []

    def fake_command(args, **kwargs):
        seen.append(args)
        assert args == ['docker', kind, 'inspect', 'synthetic-existing']
        return SimpleNamespace(returncode=0, stdout=json.dumps([metadata]), stderr='')

    monkeypatch.setattr(runner, 'command', fake_command)
    assert runner.remove_owned(kind, 'synthetic-existing', run_id) is False
    assert seen == [['docker', kind, 'inspect', 'synthetic-existing']]


@pytest.mark.parametrize('kind', ['container', 'volume'])
def test_unknown_resource_inspect_failure_is_not_absence(runner, monkeypatch, kind):
    monkeypatch.setattr(runner, 'command', lambda *args, **kwargs:
                        SimpleNamespace(returncode=1, stdout='', stderr='daemon unavailable'))
    assert runner.remove_owned(kind, 'synthetic-owned', uuid4()) is False


@pytest.mark.parametrize('kind,stderr', [
    ('container', 'Error: No such container: synthetic-missing'),
    ('volume', 'Error: No such volume: synthetic-missing'),
])
def test_explicit_missing_message_is_distinct_from_unknown_failure(
        runner, monkeypatch, kind, stderr):
    monkeypatch.setattr(runner, 'command', lambda *args, **kwargs:
                        SimpleNamespace(returncode=1, stdout='', stderr=stderr))
    # A prior ambiguous mutation must STILL force CLEANUP_UNCONFIRMED.
    assert runner.remove_owned(kind, 'synthetic-missing', uuid4()) is True


@pytest.mark.parametrize('failure', [TimeoutError, KeyboardInterrupt])
def test_unacknowledged_docker_mutation_records_unresolved_before_spawn(
        runner, monkeypatch, failure):
    run_id = uuid4()
    record = {'run_hex': run_id.hex, 'creation_attempts': []}
    token = runner._LIFECYCLE.set(record)
    name = 'r7-05b3-' + run_id.hex + '-synthetic'

    def fail_spawn(args, **kwargs):
        assert len(record['creation_attempts']) == 1
        assert record['creation_attempts'][0]['state'] == 'PENDING'
        raise failure('synthetic CLI failure')

    monkeypatch.setattr(runner.subprocess, 'Popen', fail_spawn)
    try:
        with pytest.raises(failure):
            runner.command(['docker', 'run', '--name', name, 'sha256:synthetic'])
        attempt, = record['creation_attempts']
        assert attempt['kind'] == 'container'
        assert attempt['name'] == name
        assert attempt['state'] == 'DAEMON_UNRESOLVED'
    finally:
        runner._LIFECYCLE.reset(token)


def test_foreign_creation_denied_before_process_execution(runner, monkeypatch):
    run_id = uuid4()
    record = {'run_hex': run_id.hex, 'creation_attempts': []}
    token = runner._LIFECYCLE.set(record)

    def forbidden(*args, **kwargs):
        pytest.fail('foreign resource creation must not spawn Docker')

    monkeypatch.setattr(runner.subprocess, 'Popen', forbidden)
    try:
        with pytest.raises(ValueError, match='foreign creation rejected'):
            runner.command(['docker', 'run', '--name', 'foreign-resource', 'sha256:synthetic'])
        assert record['creation_attempts'] == []
    finally:
        runner._LIFECYCLE.reset(token)


@pytest.mark.parametrize('metadata', [
    {'Id': 'sha256:' + 'b'*64, 'Os': 'linux', 'Architecture': 'amd64'},
    {'Id': 'sha256:' + 'a'*64, 'Os': 'linux', 'Architecture': 'arm64'},
    {'Id': 'sha256:' + 'a'*64, 'Os': 'windows', 'Architecture': 'amd64'},
])
def test_legacy_image_identity_mismatch_is_rejected(runner, monkeypatch, metadata):
    expected = 'sha256:' + 'a'*64
    seen = []

    def fake_command(args, **kwargs):
        seen.append(args)
        return SimpleNamespace(returncode=0, stdout=json.dumps([metadata]), stderr='')

    monkeypatch.setattr(runner, 'command', fake_command)
    with pytest.raises(ValueError, match='exact test image unavailable'):
        runner.inspect_image(expected)
    assert seen == [['docker', 'image', 'inspect', expected]]
