"""Contract-only fixtures and mocked storage. No PostgreSQL/native proof here."""
import ast
import copy
from dataclasses import replace
from pathlib import Path
import pickle
from unittest.mock import Mock
from uuid import UUID
import pytest
from src.data.contracts.canonical_forecast_read import ExactForecastRead, CanonicalReadOutcome
from src.data.contracts.forecast_evidence_storage import ForecastStorageScope, ForecastEvidenceRow
from src.database.offline_forecast_postgres import OfflineForecastPostgres
from src.database.forecast_read_connection import ForecastReadConnectionFactory
from src.providers.forecast_read_provider import ForecastReadProvider
from src.repositories.forecast_read_repository import ForecastReadRepository
from src.repositories.postgres_forecast_evidence_repository import _PostgresForecastEvidenceRepository
from src.services.canonical_forecast_read_service import (
    build_canonical_forecast_reader, ForecastReadDenied, HostReadPrincipal, ExactReadGrant,
)


@pytest.fixture
def context(monkeypatch):
    instance = UUID(int=1)
    reader = OfflineForecastPostgres('r7_05b3_'+instance.hex, instance, 'reader', 'synthetic-test-password', True)
    scope = ForecastStorageScope(organization_id=UUID(int=2), dataset_id=UUID(int=3),
                                series_ids=(UUID(int=4), UUID(int=5)))
    selection = ExactForecastRead(scope=scope, idempotency_id='a'*64, expected_content_sha256='b'*64)
    now = dict(wall=100., elapsed=0.)
    op = object()
    authority, service = build_canonical_forecast_reader(reader, scope, operator=op,
        wall_clock=lambda:now['wall'], elapsed_clock=lambda:now['elapsed'])
    principal = authority.establish_principal(op, subject_reference='synthetic:alice', scope=scope)
    grant = authority.authorize(op, principal, selection)
    lookup = Mock(return_value=None)
    monkeypatch.setattr(ForecastReadProvider, 'lookup', lookup)
    return dict(reader=reader, scope=scope, selection=selection, now=now, op=op,
                authority=authority, service=service, principal=principal, grant=grant, lookup=lookup)


def changed(selection, **kw):
    return ExactForecastRead.model_validate_json(selection.model_copy(update=kw).canonical_json)


@pytest.mark.parametrize('handle', [None, {}, 'a'*64, ExactReadGrant(), HostReadPrincipal(), object()])
def test_foreign_or_detached_handle_no_connection(context, handle):
    s=context
    assert s['service'].read(handle,s['selection']).status=='DENIED'
    s['lookup'].assert_not_called()


@pytest.mark.parametrize('field', ['organization_id','dataset_id','series_ids','idempotency_id','expected_content_sha256'])
def test_scope_and_content_substitution_denied_before_port(context, field):
    s=context; request=s['selection']
    if field in ('organization_id','dataset_id','series_ids'):
        values=request.scope.model_dump()
        values[field]=(UUID(int=4),) if field=='series_ids' else UUID(int=99)
        request=changed(request,scope=ForecastStorageScope(**values))
    else:request=changed(request,**{field:'f'*64})
    assert s['service'].read(s['grant'],request).status=='DENIED'
    s['lookup'].assert_not_called()


@pytest.mark.parametrize('fault', ['wall','elapsed','principal_revoked','grant_revoked','cancelled','cancel_raises','backward','nan'])
def test_expiry_revocation_and_callbacks_before_read(context,fault):
    s=context
    if fault=='wall':s['now']['wall']=130.
    if fault=='elapsed':s['now']['elapsed']=30.
    if fault=='backward':s['now']['wall']=99.
    if fault=='nan':s['now']['wall']=float('nan')
    if fault=='principal_revoked':s['authority'].revoke(s['op'],s['principal'])
    if fault=='grant_revoked':s['authority'].revoke(s['op'],s['grant'])
    def callback():
        if fault=='cancel_raises':raise RuntimeError('secret must not leak')
        return fault=='cancelled'
    out=s['service'].read(s['grant'],s['selection'],cancelled=callback)
    assert out.canonical_json==CanonicalReadOutcome(status='DENIED').canonical_json
    s['lookup'].assert_not_called()


@pytest.mark.parametrize('edge', ['cancel','wall','elapsed'])
@pytest.mark.parametrize('target', ['principal','grant'])
def test_final_callback_revocation_returns_no_not_found_or_content(context,edge,target):
    s=context;state=s['service']._state;calls=0
    def callback(value):
        nonlocal calls
        calls+=1
        if calls==2:s['authority'].revoke(s['op'],s[target])
        return value
    stop=lambda:callback(False) if edge=='cancel' else False
    if edge=='wall':state.wall=lambda:callback(100.)
    if edge=='elapsed':state.elapsed=lambda:callback(0.)
    assert s['service'].read(s['grant'],s['selection'],cancelled=stop).status=='DENIED'
    s['lookup'].assert_called_once()


@pytest.mark.parametrize('which',['principal','grant'])
@pytest.mark.parametrize('operation',[copy.copy,copy.deepcopy,pickle.dumps])
def test_original_handles_cannot_serialize_or_copy(context,which,operation):
    with pytest.raises(TypeError):operation(context[which])


@pytest.mark.parametrize('operation',['principal','authorize','revoke'])
def test_wrong_host_operator(context,operation):
    s=context
    with pytest.raises(ForecastReadDenied):
        if operation=='principal':s['authority'].establish_principal(object(),subject_reference='mallory',scope=s['scope'])
        if operation=='authorize':s['authority'].authorize(object(),s['principal'],s['selection'])
        if operation=='revoke':s['authority'].revoke(object(),s['grant'])
    s['lookup'].assert_not_called()


@pytest.mark.parametrize('lifetime',[0,61,True,1.5])
def test_bounded_lifetime(context,lifetime):
    s=context
    with pytest.raises(ForecastReadDenied):s['authority'].authorize(s['op'],s['principal'],s['selection'],lifetime_seconds=lifetime)


def test_foreign_issuer_and_foreign_principal(context):
    s=context;op=object()
    a,v=build_canonical_forecast_reader(s['reader'],s['scope'],operator=op)
    with pytest.raises(ForecastReadDenied):a.authorize(op,s['principal'],s['selection'])
    assert v.read(s['grant'],s['selection']).status=='DENIED'
    s['lookup'].assert_not_called()


def test_repeated_read_missing_never_confers_retry_authority(context):
    s=context
    for _ in range(3):
        result=s['service'].read(s['grant'],s['selection'])
        assert result.status=='NOT_FOUND' and result.records is None
        assert 'write' not in type(result).model_fields
    assert s['lookup'].call_count==3


@pytest.mark.parametrize('error',[ValueError,RuntimeError])
def test_storage_failure_sanitized_without_fallback(context,error):
    s=context;s['lookup'].side_effect=error('sensitive detail')
    assert s['service'].read(s['grant'],s['selection']).canonical_json==CanonicalReadOutcome(status='UNAVAILABLE').canonical_json
    s['lookup'].assert_called_once()


@pytest.mark.parametrize('error',[KeyboardInterrupt,SystemExit])
def test_host_interruption_propagates(context,error):
    s=context;s['lookup'].side_effect=error()
    with pytest.raises(error):s['service'].read(s['grant'],s['selection'])


@pytest.mark.parametrize('access',['writer','migrator'])
def test_writer_credentials_rejected_in_reader_constructor(context,access):
    with pytest.raises(ValueError):ForecastReadConnectionFactory(replace(context['reader'],access=access))


def test_read_adapter_reuses_exact_verifier_without_inheriting_writes(context):
    s=context;r=ForecastReadRepository(ForecastReadConnectionFactory(s['reader']),s['scope'])
    assert not isinstance(r,_PostgresForecastEvidenceRepository)
    assert r.lookup.__func__ is _PostgresForecastEvidenceRepository._lookup
    assert r._find.__func__ is _PostgresForecastEvidenceRepository._find
    for obj in [r,r.reader,ForecastReadProvider(r),s['service']]:
        for method in ['writer','commit','_append_initial','migrate','apply_forecast_evidence_migration']:
            assert not hasattr(obj,method)
    assert 'synthetic-test-password' not in repr(r)


def test_no_sql_or_operational_bindings_in_service():
    path=Path('src/services/canonical_forecast_read_service.py');source=path.read_text();tree=ast.parse(source)
    assert not any(isinstance(n,ast.Attribute) and n.attr in {'execute','connect','commit','_append_initial'} for n in ast.walk(tree))
    for term in ['sqlite','mlflow','xgboost','current.json','forecast_repository','src.database.db','init_db']:
        assert term not in source


@pytest.mark.parametrize('status',['DENIED','NOT_FOUND','UNAVAILABLE','VERIFIED_RECORD_SET'])
def test_outcome_contract(status):
    if status=='VERIFIED_RECORD_SET':
        with pytest.raises(ValueError):CanonicalReadOutcome(status=status)
    else:assert CanonicalReadOutcome(status=status).records is None


def test_changed_private_state_is_outside_trusted_host_boundary(context):
    # Detect casual substitution; coherent arbitrary Python memory/code mutation
    # is explicitly NOT an adversarial guarantee of this process-local design.
    s=context;s['service']._state.provider=object()
    assert s['service'].read(s['grant'],s['selection']).status=='DENIED'
    s['lookup'].assert_not_called()


def test_callback_cannot_redirect_lookup_by_mutating_public_selection(context):
    s=context
    def mutation():
        object.__setattr__(s['selection'],'idempotency_id','f'*64)
        return False
    assert s['service'].read(s['grant'],s['selection'],cancelled=mutation).status=='DENIED'
    s['lookup'].assert_not_called()


def test_host_cannot_issue_a_partial_scope_principal_on_full_scope_reader(context):
    s=context;scope=ForecastStorageScope(organization_id=s['scope'].organization_id,
        dataset_id=s['scope'].dataset_id,series_ids=(s['scope'].series_ids[0],))
    with pytest.raises(ForecastReadDenied):
        s['authority'].establish_principal(s['op'],subject_reference='synthetic:partial',scope=scope)
    s['lookup'].assert_not_called()
