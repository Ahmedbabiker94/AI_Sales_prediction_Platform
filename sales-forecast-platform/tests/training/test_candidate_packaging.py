"""M7 tests retain real canonical providers/preparation; all model fits are stubs."""
import copy
import hashlib
import json
import os
from pathlib import Path
import runpy
import struct
import traceback
import warnings
from unittest.mock import Mock
import pytest

from src.data.contracts.candidate_package import CandidatePackageManifest, payload_capability, PreprocessingReference
from src.data.contracts.model_evaluation import EvaluationDeclarationError
from src.repositories.candidate_package_repository import CandidatePackageRepository, PackageStoreError, MAGIC
from src.providers.candidate_package_provider import CandidatePackageProvider
from src.services.candidate_packaging_service import CandidatePackagingService
from src.services.canonical_adapter_training_service import CandidateReference

FAMILIES = ['xgboost', 'lightgbm', 'lstm', 'gru', 'transformer']


@pytest.fixture
def setup(tmp_path):
    build = runpy.run_path('tests/training/test_canonical_adapter_training.py')['setup'].__wrapped__(tmp_path)
    opened = []
    def create(family='xgboost'):
        b = build()
        result = b['run'](family)
        assert result.status == 'SUCCESS'
        root = tmp_path / ('storage-'+str(len(opened)))
        root.mkdir(mode=0o700)
        repository = CandidatePackageRepository(str(root)); opened.append(repository)
        provider = CandidatePackageProvider(repository)
        allowed = {'yes': True}
        service = CandidatePackagingService(b['trainer'], provider, authorize_packaging=lambda m: allowed['yes'])
        b.update(result=result, repository=repository, provider=provider, packaging=service, allowed=allowed,
                 directory=root/'candidate-packages-v1')
        return b
    yield create
    for repo in opened:
        repo.close()


def publish(b):
    record = b['packaging'].package(b['result'].candidate, test_only=True)
    assert record.status == 'FINALIZED'
    manifest = b['packaging'].verify(record.package_id, candidate=b['result'].candidate)
    return record, manifest


def file(b, record):
    return b['directory']/(record.package_id+'.candidate')


@pytest.mark.parametrize('family', FAMILIES)
def test_original_owner_all_families_package_verify_reload_and_lookup(setup, family):
    b = setup(family); record, manifest = publish(b)
    assert manifest.training == b['result'].metadata
    assert manifest.preprocessing.state_id == b['result'].metadata.fitted_state_id
    assert manifest.payload_capability.native_loading == 'UNAVAILABLE'
    assert manifest.payload_capability.family == family
    assert manifest.deployability == 'TEST_ONLY_NON_DEPLOYABLE'
    assert b['provider'].lookup(record.package_id) == record
    assert b['packaging'].package(b['result'].candidate, test_only=True) == record
    assert CandidatePackageManifest.model_validate_json(manifest.canonical_json) == manifest
    assert manifest.package_id == hashlib.sha256(manifest.canonical_bytes).hexdigest()
    fresh = CandidatePackageProvider(b['repository'])
    assert fresh.lookup(record.package_id) == record  # byte integrity only; not owner restoration
    assert not file(b, record).stat().st_mode & 0o222
    assert not list(b['directory'].glob('.stage-*'))


@pytest.mark.parametrize('family', FAMILIES)
def test_default_native_publication_unavailable_without_io(setup, family):
    b = setup(family)
    out = b['packaging'].package(b['result'].candidate)
    assert out.status == 'UNAVAILABLE' and out.package_id is None
    assert b['provider'].status(out.candidate_id) == out
    assert not list(b['directory'].iterdir())


@pytest.mark.parametrize('family', FAMILIES)
def test_real_backend_publication_never_calls_native_serializer(setup, family):
    b = setup(family); ref = b['result'].candidate; trainer = b['trainer']
    r = trainer._candidates[id(ref)]
    model = Mock(); model.save_model.side_effect = AssertionError('no native serialization')
    # Trusted test fixture simulates retained native metadata; it does not fit a model.
    metadata = r[2].model_dump(); metadata['backend'] = 'native_framework'
    from src.data.contracts.training_adapter import TrainingCandidateMetadata
    metadata = TrainingCandidateMetadata.model_validate(metadata)
    trainer._candidates[id(ref)] = (trainer, ref, metadata, model, r[4])
    out = b['packaging'].package(ref, test_only=True)
    assert out.status == 'UNAVAILABLE' and not list(b['directory'].iterdir())
    assert not model.mock_calls


@pytest.mark.parametrize('forgery', ['copy_reference', 'new_reference', 'copy_trainer', 'foreign', 'copy_service', 'metadata'])
def test_forged_or_foreign_authority_denied(setup, forgery):
    b = setup(); service = b['packaging']; ref = b['result'].candidate
    if forgery == 'copy_reference': ref = copy.copy(ref)
    elif forgery == 'new_reference': ref = CandidateReference()
    elif forgery == 'copy_trainer':
        service = CandidatePackagingService(copy.copy(b['trainer']), b['provider'], authorize_packaging=lambda m: True)
    elif forgery == 'foreign': ref = setup()['result'].candidate
    elif forgery == 'copy_service': service = copy.copy(service)
    else: ref = b['result'].metadata
    with pytest.raises(PackageStoreError): service.package(ref, test_only=True)
    assert not list(b['directory'].iterdir())


@pytest.mark.parametrize('fault', ['denied', 'callback_raises', 'callback_discards', 'reentrant'])
def test_fresh_packaging_approval_fail_closed(setup, fault):
    b = setup(); ref = b['result'].candidate
    def approval(m):
        if fault == 'denied': return False
        if fault == 'callback_raises': raise RuntimeError('PRIVATE_TEST_SECRET')
        if fault == 'callback_discards': b['trainer'].discard(ref); return True
        b['packaging'].package(ref, test_only=True)
        return True
    b['packaging']._authorize = approval
    with pytest.raises(PackageStoreError): b['packaging'].package(ref, test_only=True)
    assert not list(b['directory'].iterdir())


def test_deterministic_manifest_identity_and_public_graph(setup):
    b = setup(); record, manifest = publish(b)
    restored = CandidatePackageManifest.model_validate_json(manifest.canonical_json)
    assert restored.canonical_bytes == manifest.canonical_bytes
    assert restored.package_id == manifest.package_id
    assert restored.representation_binding_id == manifest.representation_binding_id
    forbidden = {'model', 'targets', 'labels', 'values', 'artifact', 'payload', 'canonical_state', 'candidate', 'trainer'}
    def walk(value):
        if hasattr(type(value), 'model_fields'):
            for name in type(value).model_fields:
                assert name not in forbidden
                walk(getattr(value, name))
        elif isinstance(value, tuple):
            for item in value: walk(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                assert key not in forbidden; walk(item)
        elif __import__('dataclasses').is_dataclass(value):
            for f in __import__('dataclasses').fields(value): walk(getattr(value, f.name))
        else: assert not isinstance(value, (bytes, bytearray))
    walk(manifest); walk(manifest.model_dump())
    assert b'NO_MODEL_PAYLOAD' in file(b, record).read_bytes()
    with pytest.raises(Exception): manifest.deployability = 'DEPLOYABLE'
    with pytest.raises(Exception): manifest.preprocessing.ordered_groups[0].features[0].name = 'substitute'


@pytest.mark.parametrize('fault', ['state', 'schema', 'compatibility', 'origins', 'plan', 'family', 'format', 'classification', 'scaler'])
def test_manifest_substitution_rejected_at_validation_or_owner_binding(setup, fault):
    b = setup(); record, manifest = publish(b); data = manifest.model_dump(mode='json')
    if fault == 'state':
        data['preprocessing']['state_id'] = 'a'*64; data['training']['fitted_state_id'] = 'a'*64
    elif fault == 'schema': data['preprocessing']['canonical_schema_id'] = 'b'*64
    elif fault == 'compatibility': data['training']['representation_compatibility_id'] = 'c'*64
    elif fault == 'origins': data['training_origins'] = list(reversed(data['training_origins']))
    elif fault == 'plan': data['plan']['purpose'] = 'evaluation'
    elif fault == 'family': data['payload_capability']['family'] = 'transformer'
    elif fault == 'format': data['payload_format'] = 'pickle'
    elif fault == 'classification': data['deployability'] = 'DEPLOYABLE'
    else: data['preprocessing']['ordered_groups'][0]['features'][0]['numeric_policy'] = 'permissive_v1'
    if fault in ('state', 'compatibility'):
        from src.data.contracts.training_adapter import TrainingCandidateMetadata
        data['candidate_id'] = TrainingCandidateMetadata.model_validate_json(json.dumps(data['training'])).candidate_id
    try: forged = CandidatePackageManifest.model_validate_json(json.dumps(data))
    except EvaluationDeclarationError: return
    # Coherent metadata is structurally possible, but never accepted as original evidence.
    original = b['repository'].verify
    b['repository'].verify = lambda key: forged
    with pytest.raises(PackageStoreError): b['packaging'].verify(forged.package_id, candidate=b['result'].candidate)
    b['repository'].verify = original


@pytest.mark.parametrize('fault', ['payload', 'manifest', 'missing', 'truncated', 'noncanonical', 'extra'])
def test_tampered_missing_and_corrupt_files_mark_invalid(setup, fault):
    b = setup(); record, manifest = publish(b); p = file(b, record)
    data = p.read_bytes(); p.chmod(0o600)
    if fault == 'missing': p.unlink()
    else:
        if fault == 'payload': data = data[:-1]+bytes([data[-1]^1])
        elif fault == 'manifest': data = data.replace(b'CANDIDATE_ONLY', b'PRODUCTION_OK!')
        elif fault == 'truncated': data = data[:12]
        elif fault == 'extra': data += b'anything'
        else:
            start = len(MAGIC)+4; size = struct.unpack('>I', data[len(MAGIC):start])[0]
            raw = b' '+data[start:start+size]
            data = MAGIC+struct.pack('>I', len(raw))+raw+data[start+size:]
        p.write_bytes(data); p.chmod(0o400)
    with pytest.raises(PackageStoreError): b['packaging'].verify(record.package_id, candidate=b['result'].candidate)
    assert b['provider'].lookup(record.package_id).status == 'INVALID'
    with pytest.raises(PackageStoreError): b['packaging'].package(b['result'].candidate, test_only=True)


@pytest.mark.parametrize('key', ['../secret', '/tmp/secret', '.', 'a'*63, False, {'SECRET_KEY':'SECRET_VALUE'}])
def test_path_keys_rejected_before_io(setup, key):
    b = setup()
    with pytest.raises(PackageStoreError): b['repository'].verify(key)
    assert not list(b['directory'].iterdir())


@pytest.mark.parametrize('location', ['root', 'ancestor', 'namespace', 'package'])
def test_symlinks_reject(setup, tmp_path, location):
    b = setup(); record, manifest = publish(b)
    if location == 'package':
        p = file(b, record); data = p.read_bytes(); p.unlink()
        outside = tmp_path/'outside'; outside.write_bytes(data); outside.chmod(0o400); p.symlink_to(outside)
        with pytest.raises(PackageStoreError): b['repository'].verify(record.package_id)
    else:
        target = tmp_path/'private'; target.mkdir(mode=0o700)
        root = tmp_path/'link'
        if location == 'root': root.symlink_to(target, target_is_directory=True)
        elif location == 'ancestor':
            (target/'child').mkdir(mode=0o700); root.symlink_to(target, target_is_directory=True); root = root/'child'
        else:
            root.mkdir(mode=0o700); (root/'candidate-packages-v1').symlink_to(target, target_is_directory=True)
        with pytest.raises(PackageStoreError): CandidatePackageRepository(str(root))


def test_collision_overwrite_and_staging_state(setup, monkeypatch):
    b = setup(); record, manifest = publish(b)
    raw = file(b, record).read_bytes(); start = len(MAGIC)+4; size = struct.unpack('>I', raw[len(MAGIC):start])[0]
    with pytest.raises(PackageStoreError): b['repository'].publish(manifest, raw[start+size:])
    assert file(b, record).read_bytes() == raw
    assert not list(b['directory'].glob('.stage-*'))
    other = setup(); observed = []; publish_impl = other['repository'].publish
    def capture(m, payload):
        observed.append(other['provider'].status(m.candidate_id).status)
        return publish_impl(m, payload)
    monkeypatch.setattr(other['repository'], 'publish', capture)
    publish(other); assert observed == ['STAGED']


@pytest.mark.parametrize('failure', ['write', 'file_fsync', 'link', 'dir_fsync'])
def test_failure_cleanup_no_partial_package(setup, monkeypatch, failure):
    b = setup()
    if failure == 'link': monkeypatch.setattr(os, 'link', Mock(side_effect=OSError('synthetic private failure')))
    elif failure == 'write': monkeypatch.setattr(os, 'write', Mock(side_effect=OSError('private write failure')))
    else:
        real = os.fsync; count = [0]
        def fail(fd):
            count[0] += 1
            if count[0] == (1 if failure == 'file_fsync' else 2): raise OSError('private fsync failure')
            return real(fd)
        monkeypatch.setattr(os, 'fsync', fail)
    with pytest.raises(PackageStoreError): b['packaging'].package(b['result'].candidate, test_only=True)
    assert not list(b['directory'].iterdir())
    assert b['provider'].status(b['result'].metadata.candidate_id).status == 'INVALID'


def test_concurrent_destination_creation_does_not_overwrite(setup, monkeypatch):
    b = setup(); original = os.link
    def race(source, target, **kwargs):
        # A competitor creates the final destination immediately before link.
        fd = os.open(target, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o400, dir_fd=kwargs['dst_dir_fd'])
        os.write(fd, b'competitor'); os.close(fd)
        return original(source, target, **kwargs)
    monkeypatch.setattr(os, 'link', race)
    with pytest.raises(PackageStoreError): b['packaging'].package(b['result'].candidate, test_only=True)
    files = list(b['directory'].iterdir()); assert len(files) == 1
    assert files[0].read_bytes() == b'competitor'


@pytest.mark.parametrize('entry', ['package', 'verify', 'manifest_json', 'nested_json', 'root'])
def test_secret_diagnostics_detached_and_unrelated_warnings_preserved(setup, capsys, caplog, entry, tmp_path):
    b = setup(); record, manifest = publish(b)
    secret = 'M7KEY_'+os.urandom(6).hex(); value = 'M7VAL_'+os.urandom(6).hex()
    data = manifest.model_dump(mode='json')
    if entry == 'nested_json': data['preprocessing'][secret] = value
    else: data[secret] = value
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        try:
            if entry == 'package': b['packaging'].package({secret: value}, test_only=True)
            elif entry == 'verify': b['packaging'].verify(secret, candidate=b['result'].candidate)
            elif entry in ('manifest_json', 'nested_json'): CandidatePackageManifest.model_validate_json(json.dumps(data))
            else: CandidatePackageRepository(str(tmp_path/secret/value))
        except (PackageStoreError, EvaluationDeclarationError) as exc:
            observed = str(exc)+repr(exc)+repr(exc.args)+''.join(traceback.format_exception(exc))
            assert exc.__cause__ is None and exc.__context__ is None
        else: pytest.fail('malformed input accepted')
        warnings.warn('unrelated legitimate warning', UserWarning)
    stream = capsys.readouterr(); observed += stream.out+stream.err+caplog.text+str([str(w.message) for w in seen])
    assert secret not in observed and value not in observed
    assert [str(w.message) for w in seen] == ['unrelated legitimate warning']


def test_no_model_access_after_fit_or_registry_imports(setup, monkeypatch):
    b = setup(); b['calls'].clear()
    # Never invoke arbitrary model serialization methods, even on a test model.
    ref = b['result'].candidate; record = b['trainer']._candidates[id(ref)]
    model = Mock(); model.save.side_effect = AssertionError('forbidden')
    b['trainer']._candidates[id(ref)] = (*record[:3], model, record[4])
    import builtins
    original = builtins.__import__
    def trap(name, *args, **kwargs):
        if name.startswith(('mlflow', 'torch', 'xgboost', 'lightgbm', 'src.deployment', 'src.ml.model_registry', 'src.training.registry')):
            raise AssertionError('operational or framework import')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', trap)
    from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
    monkeypatch.setattr(CanonicalFeaturePipeline, 'fit_state', Mock(side_effect=AssertionError('preprocessing refit')))
    r, m = publish(b)
    assert not model.mock_calls and not b['calls']
    assert b['packaging'].verify(r.package_id, candidate=ref) == m


@pytest.mark.parametrize('family', FAMILIES)
def test_family_payload_formats_explicit_and_unavailable(family):
    cap = payload_capability(family)
    expected = 'xgboost_ubjson' if family == 'xgboost' else 'lightgbm_text' if family == 'lightgbm' else 'torch_state_dict'
    assert cap.native_format == expected
    assert cap.native_loading == cap.native_publication == 'UNAVAILABLE'
    d = cap.model_dump(mode='json'); d['native_format'] = 'pickle'
    with pytest.raises(EvaluationDeclarationError): type(cap).model_validate_json(json.dumps(d))


def test_resource_bounds_and_closed_repository_before_io(setup):
    b = setup(); r, m = publish(b)
    raw = file(b, r).read_bytes(); start = len(MAGIC)+4; size = struct.unpack('>I', raw[len(MAGIC):start])[0]
    for i in range(63): (b['directory']/('bounded-'+str(i))).touch()
    with pytest.raises(PackageStoreError): b['repository'].publish(m, raw[start+size:])
    assert len(list(b['directory'].iterdir())) == 64
    b['repository'].close()
    with pytest.raises(PackageStoreError): b['repository'].verify(r.package_id)
    with pytest.raises(PackageStoreError): b['repository'].publish(m, raw[start+size:])


def test_tracking_record_limit_and_immutability(setup):
    b = setup()
    for i in range(32): b['provider'].unavailable(hashlib.sha256(str(i).encode()).hexdigest())
    with pytest.raises(PackageStoreError): b['provider'].unavailable('f'*64)
    r = b['provider'].status(hashlib.sha256(b'0').hexdigest())
    with pytest.raises(Exception): r.status = 'FINALIZED'
    assert b['provider'].status('0'*64) is None


def test_oversized_corrupt_regular_file_rejects_before_read(setup):
    b = setup(); r, _ = publish(b); p = file(b, r)
    p.chmod(0o600)
    with p.open('wb') as f: f.truncate(1024*1024+1)
    p.chmod(0o400)
    with pytest.raises(PackageStoreError): b['repository'].verify(r.package_id)


def test_invalid_tracking_never_revives_from_repaired_bytes(setup):
    b = setup(); r, m = publish(b); p = file(b, r); data = p.read_bytes()
    p.unlink()
    assert b['provider'].lookup(r.package_id).status == 'INVALID'
    p.write_bytes(data); p.chmod(0o400)
    assert b['provider'].lookup(r.package_id).status == 'INVALID'
    with pytest.raises(PackageStoreError): b['packaging'].package(b['result'].candidate, test_only=True)


@pytest.mark.parametrize('entry', ['python', 'json', 'strings', 'constructor'])
def test_shared_guarded_ingress_malformed_nested_types(setup, capsys, caplog, entry):
    b = setup(); _, m = publish(b)
    secret = 'KEY_'+os.urandom(5).hex(); val = 'VAL_'+os.urandom(5).hex()
    d = m.model_dump(mode='json'); d['preprocessing']['ordered_groups'][0]['features'][0]['dtype'] = {secret: val}
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        with pytest.raises(EvaluationDeclarationError) as caught:
            if entry == 'json': CandidatePackageManifest.model_validate_json(json.dumps(d))
            elif entry == 'python': CandidatePackageManifest.model_validate(d)
            elif entry == 'strings': CandidatePackageManifest.model_validate_strings(d)
            else: CandidatePackageManifest(**d)
    exc = caught.value
    observed = str(exc)+repr(exc)+repr(exc.args)+''.join(traceback.format_exception(exc))+caplog.text+str(seen)+str(capsys.readouterr())
    assert secret not in observed and val not in observed
    assert exc.__context__ is None and exc.__cause__ is None and not seen


@pytest.mark.parametrize('payload', ['{"version":"1.0","version":"1.0"}', '{"a":{"b":1,"b":2}}', '{"a":1,"\\u0061":2}'])
def test_recursive_duplicate_keys_and_malformed_json_sanitize(payload):
    with pytest.raises(EvaluationDeclarationError) as exc: CandidatePackageManifest.model_validate_json(payload)
    assert payload not in str(exc.value) and exc.value.__context__ is None


def test_manifest_digest_and_payload_size_constraints(setup):
    b = setup(); _, m = publish(b)
    for field, value in [('payload_size', True), ('payload_size', 65537), ('payload_sha256', 'not-a-digest')]:
        d = m.model_dump(mode='json'); d[field] = value
        with pytest.raises(EvaluationDeclarationError): CandidatePackageManifest.model_validate_json(json.dumps(d))


def test_unavailable_probe_does_not_prevent_explicit_test_evidence(setup):
    b = setup()
    assert b['packaging'].package(b['result'].candidate).status == 'UNAVAILABLE'
    r, m = publish(b)
    assert r.status == 'FINALIZED' and m.deployability == 'TEST_ONLY_NON_DEPLOYABLE'


def test_owner_verification_does_not_revive_invalid_tracking(setup):
    b = setup(); r, m = publish(b); p = file(b, r); raw = p.read_bytes(); p.unlink()
    assert b['provider'].lookup(r.package_id).status == 'INVALID'
    p.write_bytes(raw); p.chmod(0o400)
    with pytest.raises(PackageStoreError): b['packaging'].verify(r.package_id, candidate=b['result'].candidate)


@pytest.mark.parametrize('fault', ['discard', 'denied'])
def test_post_package_owner_and_fresh_approval_remain_required(setup, fault):
    b = setup(); r, m = publish(b)
    if fault == 'discard': b['trainer'].discard(b['result'].candidate)
    else: b['allowed']['yes'] = False
    with pytest.raises(PackageStoreError): b['packaging'].verify(r.package_id, candidate=b['result'].candidate)
    # Stored bytes do not grant model-use permission or revive an execution owner.
    assert b['repository'].verify(r.package_id) == m


def test_default_capability_probe_never_returns_stale_finalized_tracking(setup):
    b = setup(); r, _ = publish(b); file(b, r).unlink()
    observed = b['packaging'].package(b['result'].candidate)
    assert observed.status == 'INVALID' and observed.package_id == r.package_id
