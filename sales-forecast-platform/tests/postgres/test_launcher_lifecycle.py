"""Host-only lifecycle and evidence tests. No model or database operations."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock

spec = importlib.util.spec_from_file_location('launcher', Path(__file__).with_name('run_postgres.py'))
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LifecycleTests(unittest.TestCase):
    def test_evidence_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'proof'
            launcher.write_evidence(path, 'original')
            with self.assertRaises(FileExistsError): launcher.write_evidence(path, 'replacement')
            self.assertEqual(path.read_text(), 'original')

    def test_evidence_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'original').write_text('preserved')
            (root/'link').symlink_to(root/'original')
            with self.assertRaises(ValueError): launcher.write_evidence(root/'link', 'replacement')
            self.assertEqual((root/'original').read_text(), 'preserved')

    def test_timeout_registered_before_spawn_and_remains_unresolved(self):
        record = dict(run_hex='a'*32, creation_attempts=[])
        process = Mock(pid=12345)
        process.communicate.side_effect = [subprocess.TimeoutExpired('docker', .75), ('', '')]
        process.poll.return_value = None
        def spawn(*args, **kwargs):
            self.assertEqual(record['creation_attempts'][0]['state'], 'PENDING')
            self.assertTrue(kwargs['start_new_session'])
            return process
        token = launcher._LIFECYCLE.set(record)
        try:
            with patch.object(launcher.subprocess, 'Popen', side_effect=spawn), patch.object(launcher.os, 'killpg') as kill:
                with self.assertRaises(subprocess.TimeoutExpired):
                    launcher.command(['docker', 'run', '--name', 'r7-05b3-'+'a'*32+'-worker'], timeout=.75)
                kill.assert_called_once_with(12345, launcher.signal.SIGKILL)
            self.assertEqual(record['creation_attempts'][0]['state'], 'DAEMON_UNRESOLVED')
            self.assertEqual(process.communicate.call_count, 2)
        finally: launcher._LIFECYCLE.reset(token)

    def test_missing_docker_is_not_success(self):
        record = dict(run_hex='a'*32, creation_attempts=[])
        token = launcher._LIFECYCLE.set(record)
        try:
            with patch.object(launcher.subprocess, 'Popen', side_effect=FileNotFoundError):
                with self.assertRaises(FileNotFoundError):
                    launcher.command(['docker', 'volume', 'create', 'r7-05b3-'+'a'*32+'-data'])
            self.assertEqual(record['creation_attempts'][0]['state'], 'DAEMON_UNRESOLVED')
        finally: launcher._LIFECYCLE.reset(token)

    def test_foreign_creation_rejected_before_spawn(self):
        record = dict(run_hex='a'*32, creation_attempts=[])
        token = launcher._LIFECYCLE.set(record)
        try:
            with patch.object(launcher.subprocess, 'Popen') as spawn:
                with self.assertRaises(ValueError): launcher.command(['docker', 'run', '--name', 'foreign'])
                spawn.assert_not_called()
        finally: launcher._LIFECYCLE.reset(token)

    def test_nonzero_docker_response_retains_uncertainty(self):
        record = dict(run_hex='a'*32, creation_attempts=[])
        token = launcher._LIFECYCLE.set(record)
        process = Mock(pid=12345, returncode=125)
        process.communicate.return_value = ('', 'daemon connection failed')
        try:
            with patch.object(launcher.subprocess, 'Popen', return_value=process):
                launcher.command(['docker', 'run', '--name', 'r7-05b3-'+'a'*32+'-worker'], check=False)
            self.assertEqual(record['creation_attempts'][0]['state'], 'DAEMON_UNRESOLVED')
        finally: launcher._LIFECYCLE.reset(token)

    def test_existing_output_directory_rejected_before_commands(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(launcher, 'command') as command:
            with self.assertRaises(FileExistsError):
                launcher.run(allow=True, wheelhouse=Path(directory), evidence=Path(directory))
            command.assert_not_called()

    def test_symlink_output_directory_rejected_before_commands(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(launcher, 'command') as command:
            root = Path(directory); (root/'link').symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                launcher.run(allow=True, wheelhouse=root, evidence=root/'link'/'new')
            command.assert_not_called()

    def test_simulated_late_container_and_volume_never_report_success(self):
        # Deterministic FAKE daemon, not evidence of actual PostgreSQL or Docker.
        import json
        from types import SimpleNamespace
        ticks = [0.]; calls = []; resources = set(); late = [False]
        def clock():
            ticks[0] += .5
            return ticks[0]
        def command(args, **kwargs):
            state = launcher._LIFECYCLE.get()
            if args[:3] == ['docker', 'volume', 'inspect']:
                return SimpleNamespace(stdout='', stderr='no such volume', returncode=1)
            if args[:3] == ['docker', 'volume', 'create']:
                return SimpleNamespace(stdout=args[-1]+'\n', returncode=0)
            if args[:2] == ['docker', 'run']:
                name = args[args.index('--name')+1]
                state['creation_attempts'].append(dict(kind='container', name=name, state='DAEMON_UNRESOLVED'))
                raise subprocess.TimeoutExpired('fake daemon create', .75)
            return SimpleNamespace(stdout='', returncode=0)
        def remove(kind, name, run_id):
            calls.append((kind, name))
            if len(calls) == 3:
                resources.add(('container', name)); resources.add(('volume', 'r7-05b3-'+run_id.hex+'-data'))
                late[0] = True
            resources.discard((kind, name))
            return True
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory)/'new'
            with patch.object(launcher, 'inspect_image'), patch.object(launcher, 'build_client', return_value='fake'), \
                 patch.object(launcher, 'command', side_effect=command), patch.object(launcher, 'remove_owned', side_effect=remove), \
                 patch.object(launcher.time, 'monotonic', side_effect=clock), patch.object(launcher.time, 'sleep'):
                with self.assertRaises(launcher.CleanupUnconfirmed):
                    launcher.run(allow=True, wheelhouse=Path(directory), evidence=evidence)
            result = json.loads((evidence/'run-manifest.json').read_text())
            self.assertTrue(late[0]); self.assertFalse(resources)
            self.assertEqual(result['cleanup_status'], 'CLEANUP_UNCONFIRMED')
            self.assertFalse(result['cleanup_passed'])
