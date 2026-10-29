"""Real disposable PostgreSQL reads. Writer/admin are synthetic TEST SETUP only."""
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
import struct
from uuid import uuid4
import pytest
from conftest import admin, target, make_batch, repository, DATABASE, INSTANCE
from src.data.contracts.canonical_forecast_read import ExactForecastRead
from src.data.contracts.forecast_evidence_storage import ForecastStorageScope
from src.database import forecast_read_connection as connections
from src.database.forecast_read_connection import ForecastReadConnectionFactory
from src.database.offline_forecast_postgres import OfflineForecastPostgres, MIGRATION, driver
from src.repositories.forecast_read_repository import ForecastReadRepository
from src.services.canonical_forecast_read_service import build_canonical_forecast_reader, ExactReadGrant


def setup(batch=None,*,insert=True):
    batch=make_batch() if batch is None else batch
    if insert:repository(batch)._append_initial(batch)
    control={'wall':100.,'elapsed':0.};op=object()
    authority,service=build_canonical_forecast_reader(target('reader'),batch.scope,operator=op,
        wall_clock=lambda:control['wall'],elapsed_clock=lambda:control['elapsed'])
    principal=authority.establish_principal(op,subject_reference='synthetic:pg-reader',scope=batch.scope)
    selection=ExactForecastRead(scope=batch.scope,idempotency_id=batch.idempotency_id,expected_content_sha256=batch.content_sha256)
    grant=authority.authorize(op,principal,selection)
    return SimpleNamespace(**locals())


@pytest.fixture
def tracked(monkeypatch):
    real=driver();opened=[]
    def connect(**kw):
        c=real.connect(**kw);opened.append(c);return c
    monkeypatch.setattr(connections,'driver',lambda:SimpleNamespace(connect=connect))
    return opened


def test_authorized_original_order_bytes_and_read_only_transaction(tracked):
    s=setup(make_batch(values=[498.6732482910156,649.3267211914062]*2))
    result=s.service.read(s.grant,s.selection)
    assert result.status=='VERIFIED_RECORD_SET'
    stored=result.records.stored
    assert stored.batch.canonical_json==s.batch.canonical_json
    assert [r.float32_le for r in stored.batch.rows]==['2d56f943','e9542244']*2
    assert result.records.display_values==(498.6732482910156,649.3267211914062)*2
    assert all(struct.pack('<f',v).hex()==r.float32_le for v,r in zip(result.records.display_values,stored.batch.rows))
    assert all(c.closed for c in tracked) and len(tracked)==1
    with admin() as c,c.cursor() as q:
        q.execute('SELECT encode(float32_le,\'hex\') FROM canonical_forecast.evidence WHERE organization_id=%s ORDER BY ordinal',(str(s.batch.scope.organization_id),))
        assert [r[0] for r in q.fetchall()]==['2d56f943','e9542244']*2


@pytest.mark.parametrize('statement',[
    'INSERT INTO canonical_forecast.schema_migrations VALUES (1,repeat(\'a\',64),now())',
    'UPDATE canonical_forecast.evidence SET revision=1 WHERE false',
    'DELETE FROM canonical_forecast.evidence WHERE false',
    'CREATE SCHEMA reader_forbidden', 'CREATE TEMP TABLE reader_forbidden(x integer)',
])
@pytest.mark.parametrize('read_only',[True,False])
def test_postgres_role_permissions_independent_of_read_only_transaction(statement,read_only):
    batch=make_batch();factory=ForecastReadConnectionFactory(target('reader'))
    with factory._open(batch.scope) as c:
        with c.cursor() as q:
            q.execute('SHOW transaction_read_only');assert q.fetchone()==('on',)
        if not read_only:
            c.rollback();c.set_session(readonly=False)
        with pytest.raises(driver().Error) as error:
            with c.cursor() as q:q.execute(statement)
        assert error.value.pgcode in ('25006','42501')
        if not read_only:assert error.value.pgcode=='42501'


def test_reader_cannot_execute_explicit_migration():
    with ForecastReadConnectionFactory(target('reader'))._open(make_batch().scope) as c:
        with pytest.raises(driver().Error):
            with c.cursor() as q:q.execute(MIGRATION.read_text())


@pytest.mark.parametrize('field',['organization_id','dataset_id','series_ids'])
def test_rls_is_real_postgres_filter_not_user_authentication(field):
    s=setup();values=s.batch.scope.model_dump()
    values[field]=(s.batch.scope.series_ids[0],) if field=='series_ids' else uuid4()
    scope=ForecastStorageScope(**values)
    with ForecastReadConnectionFactory(target('reader'))._open(scope) as c,c.cursor() as q:
        q.execute('SELECT count(*) FROM canonical_forecast.batches WHERE idempotency_id=%s',(s.batch.idempotency_id,))
        assert q.fetchone()==(0,)
    # Custom GUCs are HOST scope selection, NOT authentication of a caller.


@pytest.mark.parametrize('fault',['tenant','dataset','partial','foreign','revoked','principal_revoked','expired','cancelled'])
def test_denial_opens_zero_real_connections(tracked,fault):
    s=setup();selection=s.selection;grant=s.grant
    if fault in ('tenant','dataset','partial'):
        values=s.batch.scope.model_dump();field={'tenant':'organization_id','dataset':'dataset_id','partial':'series_ids'}[fault]
        values[field]=(values[field][0],) if fault=='partial' else uuid4()
        selection=ExactForecastRead(scope=ForecastStorageScope(**values),idempotency_id=s.batch.idempotency_id,expected_content_sha256=s.batch.content_sha256)
    if fault=='foreign':grant=ExactReadGrant()
    if fault=='revoked':s.authority.revoke(s.op,grant)
    if fault=='principal_revoked':s.authority.revoke(s.op,s.principal)
    if fault=='expired':s.control['wall']=130.
    assert s.service.read(grant,selection,cancelled=lambda:fault=='cancelled').status=='DENIED'
    assert tracked==[]


def test_missing_and_unknown_identity_are_read_only_not_retry_permission(tracked):
    s=setup(insert=False)
    assert s.service.read(s.grant,s.selection).status=='NOT_FOUND'
    assert len(tracked)==1 and tracked[0].closed
    with admin() as c,c.cursor() as q:
        q.execute('SELECT count(*) FROM canonical_forecast.batches WHERE organization_id=%s',(str(s.batch.scope.organization_id),))
        assert q.fetchone()==(0,)


def test_host_approved_digest_mismatch_unavailable(tracked):
    s=setup();selection=s.selection.model_copy(update={'expected_content_sha256':'f'*64})
    grant=s.authority.authorize(s.op,s.principal,selection)
    assert s.service.read(grant,selection).status=='UNAVAILABLE'
    assert all(c.closed for c in tracked)


@pytest.mark.parametrize('table',['evidence','current_records'])
def test_admin_corruption_missing_row_or_pointer_rejected(table,tracked):
    s=setup()
    with admin() as c,c.cursor() as q:
        # TEST ONLY corrupt a unique fixture; session-local trigger bypass is
        # admin infrastructure, never reader credentials or application code.
        q.execute('SET LOCAL session_replication_role=replica')
        q.execute('DELETE FROM canonical_forecast.'+table+' WHERE organization_id=%s',(str(s.batch.scope.organization_id),))
        c.commit()
    assert s.service.read(s.grant,s.selection).status=='UNAVAILABLE'
    assert all(c.closed for c in tracked)


def test_alternating_tenants_fresh_connections_and_no_pool_state(tracked):
    first=setup();second=setup()
    for s in [first,second,first,second]:
        result=s.service.read(s.grant,s.selection)
        assert result.records.stored.batch.scope==s.batch.scope
    assert len(tracked)==4 and all(c.closed for c in tracked)


@pytest.mark.parametrize('edge',['guard','query'])
@pytest.mark.parametrize('error',[KeyboardInterrupt,SystemExit,RuntimeError])
def test_connections_close_during_setup_and_lookup_interruption(monkeypatch,tracked,edge,error):
    s=setup()
    def fail(*_):raise error('injected')
    if edge=='guard':monkeypatch.setattr(OfflineForecastPostgres,'_guard',fail)
    else:monkeypatch.setattr(ForecastReadRepository,'_find',fail)
    if error is RuntimeError:assert s.service.read(s.grant,s.selection).status=='UNAVAILABLE'
    else:
        with pytest.raises(error):s.service.read(s.grant,s.selection)
    assert len(tracked)==1 and tracked[0].closed


def test_no_schema_initialization_in_unmigrated_disposable_database():
    instance=uuid4();database='r7_05b3_'+instance.hex
    # A separately named disposable database; no production/legacy database.
    with admin() as c:
        c.set_session(autocommit=True)
        with c.cursor() as q:
            q.execute('CREATE DATABASE '+database)
            q.execute("COMMENT ON DATABASE "+database+" IS 'r7_05b3_disposable:"+str(instance)+"'")
            q.execute('REVOKE ALL ON DATABASE '+database+' FROM PUBLIC')
            q.execute('GRANT CONNECT ON DATABASE '+database+' TO r7_forecast_reader')
    try:
        reader=OfflineForecastPostgres(database,instance,'reader',target('reader').password,True)
        batch=make_batch();op=object();a,s=build_canonical_forecast_reader(reader,batch.scope,operator=op)
        p=a.establish_principal(op,subject_reference='synthetic:empty',scope=batch.scope)
        request=ExactForecastRead(scope=batch.scope,idempotency_id=batch.idempotency_id,expected_content_sha256=batch.content_sha256)
        grant=a.authorize(op,p,request)
        assert s.read(grant,request).status=='UNAVAILABLE'
        with ForecastReadConnectionFactory(reader)._open(batch.scope) as c,c.cursor() as q:
            q.execute("SELECT to_regnamespace('canonical_forecast')");assert q.fetchone()==(None,)
    finally:
        with admin() as c:
            c.set_session(autocommit=True)
            with c.cursor() as q:q.execute('DROP DATABASE '+database)


@pytest.mark.parametrize('family',['xgboost','lightgbm','lstm','gru','transformer'])
def test_storage_reads_are_family_independent_not_model_readiness(family):
    s=setup(make_batch(family=family))
    out=s.service.read(s.grant,s.selection)
    assert out.status=='VERIFIED_RECORD_SET'
    assert all(r.key.family==family for r in out.records.stored.batch.rows)


@pytest.mark.parametrize('bits',['00000080','01000000','ffff7f7f'])
def test_display_widening_preserves_signed_zero_subnormal_and_max_finite(bits):
    s=setup(make_batch(values=[struct.unpack('<f',bytes.fromhex(bits))[0]]))
    out=s.service.read(s.grant,s.selection)
    assert out.status=='VERIFIED_RECORD_SET'
    assert [r.float32_le for r in out.records.stored.batch.rows]==[bits]*4
    assert [struct.pack('<f',v).hex() for v in out.records.display_values]==[bits]*4


@pytest.mark.parametrize('field',['row_order','bits','candidate','family','origin','cutoff','snapshot','publication'])
def test_coherently_recomputed_unauthorized_content_rejected(field):
    original=make_batch();rows=[]
    for row in original.rows:
        if field=='bits':row=row.model_copy(update={'float32_le':'00000000'})
        if field in ('candidate','family','origin'):
            changes={'candidate_id':'f'*64} if field=='candidate' else {'family':'gru'}
            if field=='origin':changes={k:getattr(row.key,k)+timedelta(days=1) for k in ('forecast_origin','target_period','target_period_end')}
            row=row.model_copy(update={'key':row.key.model_copy(update=changes)})
        if field in ('cutoff','snapshot','publication'):
            changes={'capture_cutoff':row.lineage.capture_cutoff-timedelta(seconds=1)} if field=='cutoff' else {('snapshot_id' if field=='snapshot' else 'publication_id'):'f'*64}
            row=row.model_copy(update={'lineage':row.lineage.model_copy(update=changes)})
        rows.append(row)
    if field=='row_order':rows.reverse()
    changed=type(original).model_validate_json(original.model_copy(update={'rows':tuple(rows)}).canonical_json)
    s=setup(changed)
    request=ExactForecastRead(scope=original.scope,idempotency_id=original.idempotency_id,expected_content_sha256=original.content_sha256)
    grant=s.authority.authorize(s.op,s.principal,request)
    assert s.service.read(grant,request).status=='UNAVAILABLE'


@pytest.mark.parametrize('bits',['0000807f','000080ff','0100807f','0000c07f','bad','0000000000'])
def test_malformed_or_nonfinite_storage_encoding_rejected_without_read(bits,tracked):
    row=make_batch().rows[0]
    with pytest.raises(ValueError):type(row).model_validate_json(row.model_copy(update={'float32_le':bits}).canonical_json)
    assert tracked==[]


def test_unsupported_physical_dtype_is_not_coerced(tracked):
    row=make_batch().rows[0]
    with pytest.raises(ValueError):type(row).model_validate_json(row.model_copy(update={'physical_dtype':'float64'}).canonical_json)
    assert tracked==[]


def test_actual_read_issues_select_show_only_and_keeps_rows_unchanged(monkeypatch):
    s=setup();real=driver();statements=[];opened=[]
    class Cursor(real.extensions.cursor):
        def execute(self,query,*args,**kwargs):
            statements.append(query)
            return super().execute(query,*args,**kwargs)
    class Connection(real.extensions.connection):
        def cursor(self,*args,**kwargs):
            kwargs['cursor_factory']=Cursor
            return super().cursor(*args,**kwargs)
    def connect(**kw):
        c=real.connect(**kw,connection_factory=Connection);opened.append(c);return c
    monkeypatch.setattr(connections,'driver',lambda:SimpleNamespace(connect=connect))
    result=s.service.read(s.grant,s.selection)
    assert result.status=='VERIFIED_RECORD_SET' and all(c.closed for c in opened)
    assert statements and all(q.split()[0] in ('SELECT','SHOW') for q in statements)
    assert repository(s.batch)._lookup(s.batch.idempotency_id,expected=s.batch)==result.records.stored


@pytest.mark.parametrize('edge',['cancel','wall','elapsed'])
def test_actual_read_then_final_revocation_discards_verified_content(tracked,edge):
    s=setup();state=s.service._state;calls=0
    def callback(value):
        nonlocal calls
        calls+=1
        if calls==2:s.authority.revoke(s.op,s.grant)
        return value
    if edge=='wall':state.wall=lambda:callback(100.)
    if edge=='elapsed':state.elapsed=lambda:callback(0.)
    result=s.service.read(s.grant,s.selection,cancelled=(lambda:callback(False)) if edge=='cancel' else lambda:False)
    assert result.status=='DENIED' and result.records is None
    assert len(tracked)==1 and tracked[0].closed
