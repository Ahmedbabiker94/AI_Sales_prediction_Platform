"""C1 offline admitted publications and independent exact source/revision ledger."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
import copy
import runpy
import struct
import traceback
from uuid import uuid4
import warnings

import pytest

from src.data.contracts.model_joint_evaluation import JointH1ScoringBudgets
from src.services.canonical_model_prediction_service import _Owner, ModelPredictionError
from src.services.canonical_baseline_evaluation_service import BaselineAuthorityBinding
from src.services.canonical_joint_model_evaluation_service import JointLabelUse
from src.services.canonical_joint_model_prediction_service import SealedJointH1Session, _BaselineOwner


@pytest.fixture(scope='module')
def genuine(tmp_path_factory):
    ns=runpy.run_path('tests/training/test_joint_model_prediction.py')
    value=ns['genuine'].__wrapped__(tmp_path_factory)
    remember_lineages(value)
    return ns,value


def remember_lineages(genuine):
    # Operator bootstrap evidence: preserve immutable source lineage separately
    # from later C1 captures; exact values/releases still come from raw ledgers.
    root,_=genuine
    with root['evaluation_provider'].open_read(root['evaluation'].reference,purpose='offline_forecast_evaluation') as session:
        root['c1_lineages']={r['source_record_id']:(r['series_id'],r['lineage_id'])
            for frame in session.batches() for r in frame.to_dict('records')}


@pytest.fixture
def make(genuine):
    ns,g=genuine;make_joint=ns['make'].__wrapped__(g);root,_=g
    # Independent ledger from original source, not labels observed by C1.
    raw=root['b']['raws'][2]
    ledger={str(i):row for i,row in raw.iterrows()}
    def create(*,algorithm='last_observation_v1',**changes):
        joint=make_joint(algorithm=algorithm)
        session=joint['executor'].execute(joint['op'].issue())
        sealed=joint['verifier'].verify(session)
        control={'deny':None,'hook':None,'revision':True};trace=[];claims_seen=[]
        def authorize(ctx,stage):
            assert ctx.seal_id==sealed.seal_id and ctx.evaluation_publication==sealed.eligibility.relationship.evaluation
            assert ctx.scopes==tuple(s for s in sealed.coverage.scopes if ctx.forecast_origin is None or s.forecast_origin==ctx.forecast_origin)
            assert ctx.purpose=='offline_forecast_evaluation'
            trace.append(stage)
            if control['hook']:control['hook'](ctx,stage)
            return control['deny']!=stage
        def revision(ctx,claims):
            claims_seen.append(claims)
            if not control['revision']:return False
            meta=root['evaluation'];binding=sealed.eligibility.relationship.evaluation
            for c in claims:
                r=ledger.get(c.source_record_id)
                if r is None:return False
                if ((c.series_id,c.lineage_id)!=root['c1_lineages'].get(c.source_record_id)
                        or c.dataset_reference!=meta.reference or c.manifest_digest!=meta.manifest_digest
                        or c.admission_digest!=meta.admission_digest or c.mapping_digest!=binding.mapping_digest
                        or c.source_id!=meta.source_id or c.organization_id!=meta.reference.organization_id
                        or c.series_id!=next(s.series_id for s in meta.series if s.dimensions['location']==r['branch'])
                        or c.period_start!=r['from'] or c.period_end!=r['to']
                        or c.available_at!=r['released'] or c.evidence_reference!=r['proof']
                        or c.assessment_cutoff!=ctx.assessment_cutoff or c.available_at>c.assessment_cutoff
                        or c.value_identity!=('float64',struct.pack('>d',float(r['reported'])).hex())):return False
            return True
        authority=BaselineAuthorityBinding('independent-c1-ledger','1.0','captured_value_as_of_E_v1',
            (sealed.eligibility.relationship.evaluation,),revision)
        args={**dict(authorize_labels=authorize,label_revision_authority=authority),**changes}
        op,evaluator=joint['verifier'].bootstrap_evaluation(session,**args)
        return dict(joint=joint,session=session,sealed=sealed,op=op,evaluator=evaluator,control=control,
            trace=trace,claims=claims_seen,root=root,args=args)
    return create


def forbid_after_seal(monkeypatch):
    def forbidden(*a,**kw):pytest.fail('post-seal producer operation')
    for name in ('_load_native','_predict_native','_joint_predict'):
        monkeypatch.setattr(_Owner,name,staticmethod(forbidden))
    monkeypatch.setattr(_BaselineOwner,'execute',forbidden)
    from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',forbidden)


@pytest.mark.parametrize('algorithm',['last_observation_v1','seasonal_naive_v1'])
def test_exact_shared_capture_and_non_native_scoring(make,monkeypatch,algorithm):
    b=make(algorithm=algorithm);h=b['op'].issue();forbid_after_seal(monkeypatch)
    provider=b['root']['evaluation_provider'];read=provider.open_read;reads=[]
    @contextmanager
    def counted(*a,**kw):
        reads.append('open')
        with read(*a,**kw) as session:yield session
        reads.append('closed')
    monkeypatch.setattr(provider,'open_read',counted)
    r=b['evaluator'].evaluate(h)
    assert reads==['open','closed'] and len(b['claims'])==2
    assert b['claims'][0]==b['claims'][1]
    assert r.model_scores.producer_result_id==b['sealed'].model_handoff_id
    assert [e.label_binding_digest for e in r.model_scores.events]==[e.label_binding_digest for e in r.baseline_scores.events]
    for i,event in enumerate(r.model_scores.events):
        claim=b['claims'][0][i];actual=Fraction.from_float(struct.unpack('>d',bytes.fromhex(claim.value_identity[1]))[0])
        predicted=Fraction.from_float(b['sealed'].model.events[i].point.value);err=predicted-actual;s=event.statistics
        assert (s.count,s.sae.fraction,s.sse.fraction,s.saa.fraction)==(1,abs(err),err*err,abs(actual))
        assert s.mae.fraction==abs(err) and s.mse==s.rmse_radicand
    for d in r.deltas:
        assert d.mae.fraction==d.candidate.mae.fraction-d.baseline.mae.fraction
        assert d.mse.fraction==d.candidate.mse.fraction-d.baseline.mse.fraction
        assert 'rmse' not in type(d).model_fields and 'winner' not in type(d).model_fields
    encoded=r.model_dump_json()
    assert type(r).model_validate_json(encoded).model_dump_json()==encoded
    assert type(r).model_validate_json(encoded).evaluation_id==r.evaluation_id
    assert b['joint']['verifier'].verify(b['session']).result_id==r.sealed.result_id
    with pytest.raises(ModelPredictionError):b['evaluator'].evaluate(h)
    assert reads==['open','closed']
    pending=[r.model_dump()];forbidden={'manifest','labels','state_json','artifact_base64','matrix_float32_hex','predictions_float32_hex'}
    while pending:
        v=pending.pop()
        if isinstance(v,dict):assert not forbidden.intersection(v);pending.extend(v.values())
        elif isinstance(v,(list,tuple)):pending.extend(v)


@pytest.mark.parametrize('fault',['missing','forged','copied','foreign','detached'])
def test_session_and_capability_identity(make,fault):
    b=make();h=b['op'].issue()
    if fault=='missing':bad=None
    elif fault=='forged':bad=JointLabelUse()
    elif fault=='foreign':bad=make()['op'].issue()
    elif fault=='detached':bad=b['sealed']
    else:
        with pytest.raises(TypeError):copy.copy(h)
        bad=SealedJointH1Session()
    with pytest.raises(ModelPredictionError):b['evaluator'].evaluate(bad)
    assert b['evaluator'].evaluate(h).status.startswith('TERMINAL')
    with pytest.raises(ModelPredictionError):b['joint']['verifier'].bootstrap_evaluation(bad,**b['args'])


@pytest.mark.parametrize('failure',['read','after_read','return','scoped_return','revision','b0','cancel','expiry','joint_scope'])
def test_authority_failures_are_terminal(make,monkeypatch,failure):
    b=make();h=b['op'].issue();reads=[];provider=b['root']['evaluation_provider'];read=provider.open_read
    @contextmanager
    def counted(*a,**kw):
        reads.append(1)
        with read(*a,**kw) as s:yield s
    monkeypatch.setattr(provider,'open_read',counted)
    if failure in ('read','after_read','return','scoped_return'):b['control']['deny']=failure
    elif failure=='revision':b['control']['revision']=False
    elif failure=='b0':b['joint']['b']['bo'].revoke(b['joint']['b']['approval'])
    elif failure=='cancel':b['op'].cancel(h)
    elif failure=='expiry':b['joint']['b']['clock'][0]=60.
    else:b['joint']['control']['joint']=lambda ctx,stage:stage!='scoped_return'
    with pytest.raises(ModelPredictionError):b['evaluator'].evaluate(h)
    with pytest.raises(ModelPredictionError):b['evaluator'].evaluate(h)
    if failure in ('read','b0','cancel','expiry','joint_scope'):assert not reads
    else:assert reads==[1]
    if failure not in ('cancel','expiry','b0','joint_scope'):
        op,e=b['joint']['verifier'].bootstrap_evaluation(b['session'],**b['args'])
        hh=op.issue()
        with pytest.raises(ModelPredictionError):e.evaluate(hh)


@pytest.mark.parametrize('stage',['read','after_read','return','scoped_return'])
@pytest.mark.parametrize('kind',['revoke','cancel','b0','model','deadline'])
def test_revocation_inside_callbacks(make,stage,kind):
    b=make();h=b['op'].issue()
    def hook(ctx,action):
        if action!=stage:return
        if kind=='revoke':b['op'].revoke(h)
        elif kind=='cancel':b['op'].cancel(h)
        elif kind=='b0':b['joint']['b']['bo'].revoke(b['joint']['b']['approval'])
        elif kind=='model':b['joint']['op'].cancel(b['session'])
        else:b['joint']['b']['clock'][0]=60.
    b['control']['hook']=hook
    with pytest.raises(ModelPredictionError):b['evaluator'].evaluate(h)
    with pytest.raises(ModelPredictionError):b['evaluator'].evaluate(h)


def test_competing_consumers_and_reentrance(make):
    b=make();op2,e2=b['joint']['verifier'].bootstrap_evaluation(b['session'],**b['args'])
    h1=b['op'].issue();h2=op2.issue()
    def consume(e,h):
        try:return e.evaluate(h)
        except ModelPredictionError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(consume,b['evaluator'],h1);bb=pool.submit(consume,e2,h2)
        results=[a.result(),bb.result()]
    assert sum(r is not None for r in results)==1


def test_reentrant_consumer_denied(make):
    b=make();h=b['op'].issue()
    def hook(ctx,stage):
        if stage=='read':
            with pytest.raises(ModelPredictionError):b['evaluator'].evaluate(h)
    b['control']['hook']=hook
    assert b['evaluator'].evaluate(h).status.startswith('TERMINAL')


@pytest.mark.parametrize('fault',['missing','duplicate','nonfinite','substituted','late','end','reader','cleanup','lineage'])
def test_label_integrity_and_reader_failures(make,monkeypatch,fault):
    b=make();h=b['op'].issue();provider=b['root']['evaluation_provider'];read=provider.open_read
    @contextmanager
    def corrupt(*a,**kw):
        with read(*a,**kw) as s:
            batches=s.batches
            def stream():
                for frame in batches():
                    frame=frame.copy()
                    if fault=='reader':raise RuntimeError('PRIVATE-C1-READER')
                    if fault=='missing':frame=frame.iloc[:0]
                    elif fault=='duplicate':
                        import pandas as pd
                        frame=pd.concat([frame,frame])
                    elif fault in ('nonfinite','substituted'):frame['value']=float('nan') if fault=='nonfinite' else 999.
                    elif fault=='late':frame['available_at']=b['root']['b']['at'](10)
                    elif fault=='end':frame['period_end']=b['root']['b']['at'](10)
                    elif fault=='lineage':frame['lineage_id']=uuid4()
                    yield frame
            s.batches=stream;yield s
        if fault=='cleanup':raise RuntimeError('PRIVATE-C1-CLEANUP')
    monkeypatch.setattr(provider,'open_read',corrupt)
    with pytest.raises(ModelPredictionError) as err:b['evaluator'].evaluate(h)
    assert 'PRIVATE' not in str(err.value) and err.value.__context__ is None
    with pytest.raises(ModelPredictionError):b['evaluator'].evaluate(h)


@pytest.mark.parametrize('field',['max_label_rows','max_label_bytes','max_points','max_groups','max_representation_bytes','max_work','max_arithmetic_units','max_output_bytes'])
def test_cumulative_budgets(make,field):
    b=make(budgets=JointH1ScoringBudgets(**{field:1}))
    if field=='max_label_rows':
        with pytest.raises(ModelPredictionError):b['op'].issue()
        with pytest.raises(ModelPredictionError):b['op'].issue()
    else:
        h=b['op'].issue()
        with pytest.raises(ModelPredictionError):b['evaluator'].evaluate(h)
        with pytest.raises(ModelPredictionError):b['evaluator'].evaluate(h)


@pytest.mark.parametrize('field,counter',[
    ('max_label_rows','rows'), ('max_label_bytes','decoded'),
    ('max_points',None), ('max_groups',None), ('max_work','work'),
    ('max_arithmetic_units','arithmetic'),
    ('max_representation_bytes','intermediate'), ('max_output_bytes',None),
])
def test_exact_consumption_boundary_and_one_unit_insufficient(make,monkeypatch,field,counter):
    """Measure accepted work, then use fresh sessions for both sides of its cap.

    Point/group caps are fixed at the two-point geometry in the reference run:
    those caps enter scoring-plan JSON, so doing this also keeps byte accounting
    invariant across the three runs. Random identifiers have fixed wire lengths.
    No private owner data is exported; counters are test instrumentation only.
    """
    defaults=dict(max_points=2,max_groups=2)
    measured=make(budgets=JointH1ScoringBudgets(**defaults))
    result=measured['evaluator'].evaluate(measured['op'].issue())
    owner=getattr(measured['evaluator'],'_JointH1Evaluator__owner')
    required=(owner.used[counter] if counter else
              len(result.model_dump_json(warnings='error').encode()) if field=='max_output_bytes' else 2)
    assert required>1
    for allowance,accepted in ((required,True),(required-1,False)):
        options={**defaults,field:allowance}
        b=make(budgets=JointH1ScoringBudgets(**options))
        provider=b['root']['evaluation_provider'];original=provider.open_read;reads=[]
        @contextmanager
        def counted(*a,**kw):
            reads.append(1)
            with original(*a,**kw) as session:yield session
        with monkeypatch.context() as patch:
            patch.setattr(provider,'open_read',counted)
            published=[]
            if accepted:
                h=b['op'].issue();published.append(b['evaluator'].evaluate(h))
                actual=getattr(b['evaluator'],'_JointH1Evaluator__owner')
                assert (actual.used[counter] if counter else
                        len(published[0].model_dump_json(warnings='error').encode()) if field=='max_output_bytes' else 2)==required
                assert reads==[1]
            else:
                # Row admission can reject during issue(), before claiming C0.
                h=None
                with pytest.raises(ModelPredictionError):
                    h=b['op'].issue();published.append(b['evaluator'].evaluate(h))
                assert not published
                if h is not None:
                    with pytest.raises(ModelPredictionError):b['evaluator'].evaluate(h)
                else:
                    with pytest.raises(ModelPredictionError):b['op'].issue()
                if field in ('max_label_rows','max_points','max_groups','max_arithmetic_units'):
                    assert reads==[]  # Declared geometry/arithmetic reserved before truth access.
                else:
                    assert reads==[1]  # Observed bytes/work checked as charged; no output escapes.


@pytest.mark.parametrize('entry',['bootstrap','issue','evaluate','revoke','cancel'])
def test_sanitized_public_ingress(make,entry,capsys,caplog):
    b=make();h=b['op'].issue();secret='C1_'+uuid4().hex
    actions={'bootstrap':lambda:b['joint']['verifier'].bootstrap_evaluation(b['session'],**{secret:secret}),
        'issue':lambda:b['op'].issue(**{secret:secret}),'evaluate':lambda:b['evaluator'].evaluate(h,**{secret:secret}),
        'revoke':lambda:b['op'].revoke(**{secret:secret}),'cancel':lambda:b['op'].cancel(**{secret:secret})}
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        try:raise ValueError(secret)
        except ValueError:
            with pytest.raises(ModelPredictionError) as caught:actions[entry]()
    e=caught.value;out=capsys.readouterr()
    assert e.__cause__ is None and e.__context__ is None
    assert secret not in str(e)+repr(e)+repr(e.args)+''.join(traceback.format_exception(e))+repr(seen)+out.err+caplog.text

@pytest.mark.parametrize('replacement',[None,True,'matching-digest'])
def test_revision_authority_is_scoped_bootstrap_configuration(make,replacement):
    with pytest.raises(ModelPredictionError):make(label_revision_authority=replacement)


def test_no_request_selected_provider_or_scope(make):
    b=make()
    for key,value in [('provider',b['root']['evaluation_provider']),('scopes',b['sealed'].coverage.scopes),('clock',lambda:0.)]:
        with pytest.raises(ModelPredictionError):b['joint']['verifier'].bootstrap_evaluation(b['session'],**{**b['args'],key:value})


def test_final_origin_denial_after_scoring_and_one_shot_failure(make):
    b=make();h=b['op'].issue()
    def hook(ctx,stage):
        if stage=='scoped_return':
            b['joint']['control']['joint']=lambda c,s:False if s=='scoped_return' else True
    b['control']['hook']=hook
    with pytest.raises(ModelPredictionError):b['evaluator'].evaluate(h)
    with pytest.raises(ModelPredictionError):b['evaluator'].evaluate(h)


def test_snapshot_grid_is_not_replaceable_after_label_capture(make,monkeypatch):
    b=make();h=b['op'].issue()
    def hook(ctx,stage):
        if stage=='after_read':
            owner=getattr(b['joint']['verifier'],'_JointH1Verifier__owner')
            original=owner.model_result
            owner.model_result=type(original).model_validate_json(original.model_dump_json())
    b['control']['hook']=hook
    with pytest.raises(ModelPredictionError):b['evaluator'].evaluate(h)


def test_label_reader_closure_on_cancellation(make,monkeypatch):
    b=make();h=b['op'].issue();provider=b['root']['evaluation_provider'];read=provider.open_read;closed=[]
    @contextmanager
    def interrupted(*a,**kw):
        try:
            with read(*a,**kw) as s:
                original=s.batches
                def stream():
                    for frame in original():
                        b['op'].cancel(h);yield frame
                s.batches=stream;yield s
        finally:closed.append(True)
    monkeypatch.setattr(provider,'open_read',interrupted)
    with pytest.raises(ModelPredictionError):b['evaluator'].evaluate(h)
    assert closed==[True]

@pytest.fixture(scope='module')
def multi_genuine(tmp_path_factory):
    ns=runpy.run_path('tests/training/test_joint_model_prediction.py')
    value=ns['multi'].__wrapped__(tmp_path_factory)
    remember_lineages(value)
    return ns,value


@pytest.fixture
def make_multi(multi_genuine):return make.__wrapped__(multi_genuine)


def test_multi_origin_labels_and_deterministic_groups(make_multi):
    b=make_multi();h=b['op'].issue();scopes=[]
    b['control']['hook']=lambda ctx,stage:scopes.append((stage,ctx.forecast_origin,ctx.scopes))
    r=b['evaluator'].evaluate(h)
    periods=r.sealed.eligibility.coverage.periods
    assert len(r.model_scores.events)==4 and len(r.deltas)==4
    for stage in ('read','scoped_return'):
        calls=[(o,ss) for st,o,ss in scopes if st==stage]
        assert [o for o,_ in calls]==[p.forecast_origin for p in periods]
        assert all(len(ss)==2 and all(s.forecast_origin==o for s in ss) for o,ss in calls)


def test_label_scoped_denial_last_origin_overrides_global_permission(make_multi):
    b=make_multi();h=b['op'].issue();last=b['sealed'].eligibility.coverage.periods[-1].forecast_origin
    def hook(ctx,stage):
        if stage=='scoped_return' and ctx.forecast_origin==last:b['control']['deny']='scoped_return'
    b['control']['hook']=hook
    with pytest.raises(ModelPredictionError):b['evaluator'].evaluate(h)
    with pytest.raises(ModelPredictionError):b['evaluator'].evaluate(h)

@pytest.mark.parametrize('field',['source_id','series_ids','mapping_digest'])
def test_malformed_typed_revision_binding_does_not_warn(make,field,capsys,caplog):
    from dataclasses import replace
    from src.data.contracts.temporal_preparation import TemporalDatasetBinding
    b=make();secret='C1BIND_'+uuid4().hex
    authority=b['args']['label_revision_authority'];binding=authority.datasets[0]
    data=dict(binding);data[field]={secret:secret}
    malformed=TemporalDatasetBinding.model_construct(**data)
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        with pytest.raises(ModelPredictionError) as error:
            b['joint']['verifier'].bootstrap_evaluation(b['session'],**{**b['args'],
                'label_revision_authority':replace(authority,datasets=(malformed,))})
    err=error.value;out=capsys.readouterr()
    assert err.__cause__ is None and err.__context__ is None
    assert secret not in str(err)+repr(err)+repr(err.args)+''.join(traceback.format_exception(err))+repr(seen)+out.err+caplog.text

@pytest.mark.parametrize('kind',['relationship','label_use'])
def test_revision_callback_revocation_prevents_scoring(make,monkeypatch,kind):
    from dataclasses import replace
    import src.services.canonical_joint_model_evaluation_service as service
    b=make();original=b['args']['label_revision_authority'];holder={}
    def verify(ctx,claims):
        accepted=original.verify(ctx,claims)
        if kind=='relationship':b['joint']['b']['bo'].revoke(b['joint']['b']['approval'])
        else:holder['op'].revoke(holder['h'])
        return accepted
    op,e=b['joint']['verifier'].bootstrap_evaluation(b['session'],**{**b['args'],
        'label_revision_authority':replace(original,verify=verify)})
    holder.update(op=op,h=op.issue())
    monkeypatch.setattr(service,'score_point',lambda *a:pytest.fail('revoked revision authority reached scoring'))
    with pytest.raises(ModelPredictionError):e.evaluate(holder['h'])
    with pytest.raises(ModelPredictionError):e.evaluate(holder['h'])
