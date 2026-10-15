"""Canonical quality + trusted consistency assessment + one atomic publication.

External verifiers are authenticated application integration boundaries, not
implemented source snapshot/reconciliation engines. No live source is queried.
Only explicit supported publication purposes are implemented; unadmitted data remains private.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
from time import monotonic

from src.data.contracts import SourceMapping
from src.data.contracts.dataset import (
    StagingBudgets, DatasetAccessContext, ConsistencyPolicy, VerifiedConsistencyEvidence,
    ScopedWarningAuthorization, operational_json, access_context_from_dict,
    consistency_evidence_from_dict, warning_authorization_from_dict, _opaque,
)
from src.data.processing.canonical_dataset_quality import detect_canonical_dataset_quality
from src.data.processing.quality import QualityIssue, QualityIssueType, QualitySeverity, QualityPolicy, QualityAction, QualityGateResult
from src.data.processing.sales_quality import build_general_sales_quality_policy
from src.data.processing.training_admission import (
    WarningAuthorization, evaluate_training_admission, enforce_training_admission, TrainingAdmissionError,
)
from src.providers import dataset_store_provider as _protocol
from src.providers.dataset_store_provider import StagingError


def trusted_callback(callback, message, *args):
    try:
        result=callback(*args)
    except Exception:
        raise StagingError(message) from None
    if result is not True:raise StagingError(message)


def verified_consistency(callback, context, evidence):
    """An exact False is a completed verification denial, not an outage.

    Only called after scope, integrity and applicable policy validation. An
    exception or malformed callback response establishes no consistency result.
    """
    try:
        result=callback(context,evidence)
    except Exception:
        raise StagingError('consistency verification failed') from None
    if type(result) is not bool:
        raise StagingError('invalid consistency verification response')
    return result


def validate_admission_binding(serialized, candidate, purpose, now):
    """Reader checks binding and independently re-evaluates stored quality evidence.

    Storage hashes are integrity, not authentication. Publication was issued by
    the trusted finalization lifecycle; read authorization remains mandatory.
    """
    try:
        from src.data.adapters.json_adapter import _unique_object, _invalid_constant
        data=json.loads(serialized,object_pairs_hook=_unique_object,parse_constant=_invalid_constant)
        if data.get('quality_detector_version')!='1.0':raise ValueError()
        context=access_context_from_dict(data['scope'])
        manifest=candidate.manifest
        if (context.reference!=candidate.reference or context.purpose!=purpose
                or data['manifest_digest']!=manifest.version_id or context.source_id!=manifest.source_id
                or context.ingestion_batch_id!=manifest.ingestion_batch_id or context.mapping_id!=manifest.mapping_id
                or context.mapping_version!=manifest.mapping_version or context.mapping_digest!=manifest.mapping_digest):
            raise ValueError()
        mapping=SourceMapping.model_validate(data['mapping'])
        if hashlib.sha256(operational_json(mapping.model_dump(mode='json')).encode()).hexdigest()!=manifest.mapping_digest:
            raise ValueError()
        if context.metric!=mapping.target_metric or context.business_scope!=mapping.business_scope:raise ValueError()
        if context.source_scope!=json.loads(manifest.envelopes[0])['source_dataset']:raise ValueError()
        quality=QualityPolicy.model_validate(data['quality_policy'])
        policy_data=dict(data['consistency_policy']);policy_data['scope']=access_context_from_dict(policy_data['scope'])
        policy=ConsistencyPolicy(**policy_data)
        if policy.scope!=context or (policy.quality_policy_id,policy.quality_policy_version)!=(quality.policy_id,quality.version):raise ValueError()
        evidence=consistency_evidence_from_dict(data['consistency_evidence'])
        if evidence.scope!=context or evidence.policy_version!=policy.version or not evidence.verified_at<=now<evidence.expires_at:raise ValueError()
        if evidence.method=='snapshot_transaction_v1' and (manifest.consistency.kind!='transaction_scoped' or any(json.loads(v)['source_format']!='database' for v in manifest.envelopes)):
            raise ValueError()
        level='B' if evidence.method=='reconciliation_authoritative_v1' else 'A'
        if data['consistency_level']!=level or (level=='B' and not policy.allow_reconciliation):raise ValueError()
        authorizations=[warning_authorization_from_dict(v) for v in data['warning_authorizations']]
        approved=[]
        for auth in authorizations:
            if auth.scope!=context or not auth.issued_at<=now<auth.expires_at:raise ValueError()
            approved.append(WarningAuthorization(mapping.policy_context,auth.issue_code,auth.field,mapping.policy_metric,QualityAction.KEEP))
        issues=[QualityIssue.model_validate(v) for v in data['issues']]
        recomputed=evaluate_training_admission(issues,quality,execution_context=mapping.policy_context,warning_authorizations=approved)
        saved=QualityGateResult.model_validate(data['quality_result'])
        if recomputed.model_dump(mode='json')!=saved.model_dump(mode='json'):raise ValueError()
        enforce_training_admission(issues,recomputed)
        _opaque(data['decision_context'])
        return data,mapping
    except Exception:
        raise StagingError('dataset admission evidence or binding failed') from None


def verify_dataset_quality_evidence(batches, mapping, record_key, admission):
    """Recheck actual immutable read snapshot against published quality evidence."""
    issues=detect_canonical_dataset_quality(batches,mapping,record_key)
    if [v.model_dump(mode='json') for v in issues]!=admission['issues']:
        raise StagingError('published quality evidence differs from dataset content')


class DatasetFinalizationService:
    def __init__(self, store, *, authorize, verify_consistency, verify_policy,
                 decision_context_reference, verify_warning=None,
                 clock=lambda:datetime.now(timezone.utc), elapsed_clock=monotonic):
        _opaque(decision_context_reference)
        self.store=store;self.authorize=authorize;self.verify_consistency=verify_consistency
        self.verify_policy=verify_policy;self.verify_warning=verify_warning
        self.decision_context=decision_context_reference;self.clock=clock;self.elapsed_clock=elapsed_clock
        self.__publication=None;self.__rejection=None;self.__active=False

    def _claim_publication(self, reference, digest):
        if self.__publication!=(reference,digest):raise StagingError('finalization lifecycle has not admitted this version')
        self.__publication=None

    def _claim_rejection(self, reference):
        if self.__rejection!=reference:raise StagingError('rejection lifecycle scope mismatch')
        self.__rejection=None

    def finalize(self, reference, mapping, *, connection_reference, consistency_policy,
                 consistency_evidence=None, warning_authorizations=(), quality_policy=None,
                 purpose='production_training', budgets=None):
        if self.__active:raise StagingError('finalization lifecycle already active')
        self.__active=True;candidate=None;trusted_admission_failure=None
        budgets=budgets or StagingBudgets()
        def now():
            try:value=self.clock()
            except Exception:raise StagingError('finalization clock failed') from None
            if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:raise StagingError('invalid finalization time')
            return value
        try:
            def elapsed():
                try:value=self.elapsed_clock()
                except Exception:raise StagingError('finalization elapsed clock failed') from None
                if type(value) not in (int,float) or not math.isfinite(value):raise StagingError('invalid elapsed clock')
                return value
            start=elapsed()
            def checkpoint():
                if elapsed()-start>=budgets.max_elapsed_seconds:raise StagingError('finalization elapsed budget exceeded')
            # Authorization precedes revealing private capture metadata.
            trusted_callback(self.authorize,'dataset finalization is not authorized',reference,purpose,'inspect')
            with self.store._publication_session(budgets):
                try:
                    candidate=self.store._candidate(reference,budgets)
                    cfg=SourceMapping.model_validate(mapping.model_dump())
                    if hashlib.sha256(operational_json(cfg.model_dump(mode='json')).encode()).hexdigest()!=candidate.manifest.mapping_digest:
                        raise StagingError('finalization mapping mismatch')
                    candidate.mapping_json=cfg.model_dump_json()
                    context=DatasetAccessContext(reference,candidate.manifest.source_id,candidate.manifest.ingestion_batch_id,
                        cfg.mapping_id,cfg.version,candidate.manifest.mapping_digest,cfg.target_metric,cfg.business_scope,
                        json.loads(candidate.manifest.envelopes[0])['source_dataset'],connection_reference,purpose)
                    quality=QualityPolicy.model_validate((quality_policy or build_general_sales_quality_policy()).model_dump())
                    if type(consistency_policy) is not ConsistencyPolicy or consistency_policy.scope!=context:
                        raise StagingError('consistency policy scope mismatch')
                    if (consistency_policy.quality_policy_id,consistency_policy.quality_policy_version)!=(quality.policy_id,quality.version):
                        raise StagingError('quality and consistency policy version mismatch')
                    trusted_callback(self.verify_policy,'consistency policy is not approved',context,consistency_policy,quality.model_copy(deep=True))
                    with self.store._quality_key_index(budgets) as record_key:
                        issues=detect_canonical_dataset_quality(self.store._verified_batches(candidate,checkpoint),cfg,record_key)
                    current=now();level='C'
                    if consistency_evidence is not None:
                        if type(consistency_evidence) is not VerifiedConsistencyEvidence or consistency_evidence.scope!=context:
                            raise StagingError('consistency evidence scope mismatch')
                        if consistency_evidence.policy_version!=consistency_policy.version or not consistency_evidence.verified_at<=current<consistency_evidence.expires_at:
                            raise StagingError('consistency evidence expired or incompatible')
                        created=[datetime.fromisoformat(json.loads(v)['raw_asset']['created_at']) for v in candidate.manifest.envelopes]
                        if any(not consistency_evidence.window_start<=t<=consistency_evidence.window_end for t in created):
                            raise StagingError('consistency evidence does not cover extraction timestamps')
                        if consistency_evidence.method=='snapshot_transaction_v1':
                            if candidate.manifest.consistency.kind!='transaction_scoped' or any(json.loads(v)['source_format']!='database' for v in candidate.manifest.envelopes):
                                raise StagingError('transaction snapshot evidence is unsupported for this capture')
                        if verified_consistency(self.verify_consistency,context,consistency_evidence):
                            level='B' if consistency_evidence.method=='reconciliation_authoritative_v1' else 'A'
                    if level=='C' or (level=='B' and not consistency_policy.allow_reconciliation):
                        issues.append(QualityIssue(issue_code='CONSISTENCY_NOT_ALLOWED',issue_type=QualityIssueType.INCONSISTENT,
                            severity=QualitySeverity.ERROR,affected_count=candidate.manifest.rows,
                            message='Consistency is not eligible for production training',details={'metric':cfg.policy_metric}))
                    declarations=tuple(warning_authorizations)
                    if len(declarations)>32:raise StagingError('warning authorization budget exceeded')
                    approved=[]
                    for auth in declarations:
                        if type(auth) is not ScopedWarningAuthorization or auth.scope!=context or not auth.issued_at<=current<auth.expires_at:
                            raise StagingError('warning authorization scope or validity mismatch')
                        if self.verify_warning is None:raise StagingError('warning authorization is not verified')
                        trusted_callback(self.verify_warning,'warning authorization is not verified',context,auth)
                        approved.append(WarningAuthorization(cfg.policy_context,auth.issue_code,auth.field,cfg.policy_metric,QualityAction.KEEP))
                    result=evaluate_training_admission(issues,quality,execution_context=cfg.policy_context,warning_authorizations=approved)
                    # An assessment that exceeded its operational deadline is
                    # incomplete even if its evaluator returned a denial.
                    checkpoint()
                    try:
                        enforce_training_admission(issues,result)
                    except TrainingAdmissionError as error:
                        trusted_admission_failure=error
                        raise
                    data={'scope':context,'manifest_digest':candidate.manifest.version_id,'mapping':cfg.model_dump(mode='json'),
                          'quality_policy':quality.model_dump(mode='json'),'consistency_policy':consistency_policy,'quality_detector_version':'1.0',
                          'consistency_level':level,'consistency_evidence':consistency_evidence,
                          'warning_authorizations':declarations,'issues':[v.model_dump(mode='json') for v in issues],
                          'quality_result':result.model_dump(mode='json'),'decision_context':self.decision_context}
                    serialized=operational_json(data)
                    validate_admission_binding(serialized,candidate,purpose,now())
                    trusted_callback(self.authorize,'dataset publication is not authorized',reference,purpose,'publish')
                    checkpoint()
                    existing=self.store._publication(reference,budgets)
                    if candidate.state=='finalized':
                        if existing!=serialized:raise StagingError('idempotent finalization evidence differs')
                        return reference
                    if existing is not None:raise StagingError('unexpected existing publication')
                    self.__publication=(reference,hashlib.sha256(serialized.encode()).hexdigest())
                    def publication_checkpoint():
                        checkpoint()
                        validate_admission_binding(serialized,candidate,purpose,now())
                        trusted_callback(self.authorize,'dataset publication is not authorized',reference,purpose,'publish')
                    return self.store._publish(candidate,serialized,self,publication_checkpoint)
                except TrainingAdmissionError as error:
                    # Only the exact denial produced by completed, scoped
                    # admission enforcement may change the dataset state.
                    # Request, authorization, callback and integrity failures
                    # leave the private capture SEALED for a fresh assessment.
                    if error is trusted_admission_failure and candidate is not None and candidate.state=='sealed':
                        self.__rejection=reference
                        try:self.store._reject(reference,budgets,self)
                        except Exception:pass  # Unavailable storage never grants publication.
                    raise
        except TrainingAdmissionError as error:
            if error is trusted_admission_failure:
                raise
            raise StagingError('dataset finalization failed') from None
        except Exception:
            raise StagingError('dataset finalization failed') from None
        finally:
            self.__publication=None;self.__rejection=None;self.__active=False


_protocol._finalization_owner_type=DatasetFinalizationService
