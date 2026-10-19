"""Owned offline fixture training. No caller handoffs, registry, paths or aliases."""
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import platform
import subprocess
from threading import Lock
from time import monotonic

import numpy as np
import pandas as pd

from src.data.contracts.model_candidate import (OfflineXGBoostConfiguration,TrainingInvocation,
    RuntimeIdentity,CandidateManifest,CandidateBundle,assemble_candidate,digest,strict_float32)
from src.data.contracts.model_training import TrainingHandoffScope,TrainingHandoffProvenance
from src.data.contracts.temporal_preparation import TemporalPreparationPlan
from src.data.contracts.features import FeatureConfiguration
from src.services.canonical_training_preparation_service import (CanonicalTrainingPreparationService,
    ValueAttestedAvailabilityVerifier,TrainingOnlyHandoff,TemporalPreparationContext)
from src.providers.canonical_training_data_provider import CanonicalTrainingDataProvider


class CandidateTrainingError(ValueError):
    """Fixed public diagnostics, including callback/native error chains."""


@dataclass(frozen=True)
class CandidateAuthorizationContext:
    invocation_json: str
    preparation: TemporalPreparationContext | None
    provenance_json: str | None
    bundle_id: str | None


def _approved(callback,*args):
    try:ok=callback(*args)
    except Exception:raise CandidateTrainingError('trusted candidate authorization failed') from None
    if ok is not True:raise CandidateTrainingError('candidate operation not authorized')


def _runtime():
    import xgboost
    if xgboost.__version__!='3.2.0':raise CandidateTrainingError('unsupported XGBoost runtime')
    root=Path(__file__).resolve().parents[3]
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True,timeout=5).strip()
    files=tuple(sorted('sales-forecast-platform/'+p for p in (
        'src/data/contracts/model_candidate.py','src/services/canonical_xgboost_training_service.py',
        'src/data/contracts/model_training.py','src/services/canonical_training_preparation_service.py',
        'src/features/canonical_feature_pipeline.py','src/features/feature_schema_compatibility.py',
        'src/data/contracts/features.py','src/providers/canonical_training_data_provider.py')))
    snapshot=[];matches=True
    for name in files:
        data=(root/name).read_bytes();snapshot.append((name,digest(data)))
        result=subprocess.run(['git','cat-file','blob',commit+':'+name],cwd=root,capture_output=True,timeout=5)
        matches=matches and result.returncode==0 and result.stdout==data
    return RuntimeIdentity(python=platform.python_version(),xgboost=xgboost.__version__,numpy=np.__version__,
        pandas=pd.__version__,platform=platform.platform(),source_commit=commit,source_files=tuple(snapshot),source_matches_commit=matches)


class _InvocationEvidence:
    def __init__(self, options, checkpoint):
        self.options=options;self.checkpoint=checkpoint;self.records=[];self.seen=set();self.size=0

    def wrap(self,kind,callback):
        def verified(ctx,claim):
            self.checkpoint();key=(kind,ctx,claim)
            if key not in self.seen:
                size=len(repr(key).encode())
                if len(self.records)>=self.options.max_evidence_records or self.size+size>self.options.max_evidence_bytes:
                    raise CandidateTrainingError('candidate evidence budget exceeded')
                _approved(callback,ctx,claim)
                self.records.append((kind,callback,ctx,claim));self.seen.add(key);self.size+=size
            else:_approved(callback,ctx,claim)
            self.checkpoint();return True
        return verified

    def recheck(self,records):
        for kind,callback,ctx,claims in records:
            _approved(callback,ctx,claims);self.checkpoint()


class CanonicalXGBoostTrainingService:
    """Trusted bootstrap owns one plan/configuration and at most one issuance.

    Constructor collaborators are trusted Python, not an in-process sandbox.
    No request can provide a handoff, artifact path, callback or model parameters.
    An issued object reference gates native loading; digests only check integrity.
    """
    def __init__(self,provider,*,plan,configuration,training_scope,invocation,training,
                 authorize_preparation,verify_availability,verify_training_labels,authorize_training,
                 authorize_external_forecast=None,elapsed_clock=monotonic):
        try:
            if (type(provider) is not CanonicalTrainingDataProvider or type(plan) is not TemporalPreparationPlan
                    or type(configuration) is not FeatureConfiguration or type(training_scope) is not TrainingHandoffScope
                    or type(invocation) is not TrainingInvocation or type(training) is not OfflineXGBoostConfiguration
                    or type(verify_availability) is not ValueAttestedAvailabilityVerifier):raise ValueError()
            p=TemporalPreparationPlan.model_validate(plan.model_dump(warnings='error'))
            cfg=FeatureConfiguration.model_validate(configuration.model_dump(warnings='error'))
            scope=TrainingHandoffScope.model_validate(training_scope.model_dump(warnings='error'))
            inv=TrainingInvocation.model_validate(invocation.model_dump(warnings='error'))
            options=OfflineXGBoostConfiguration.model_validate(training.model_dump(warnings='error'))
            if (len(p.folds)!=1 or len(p.datasets)!=1 or p.forecast_horizon!=1 or p.capability!='supervised_h1'
                    or p.purpose!='production_training' or p.datasets!=scope.datasets or p.configuration_digest!=scope.configuration_digest
                    or not all(callable(f) for f in (authorize_preparation,verify_training_labels,authorize_training,elapsed_clock))):raise ValueError()
            external=any(r.availability=='external_forecast' for r in cfg.execution.exogenous)
            if external and (not callable(authorize_external_forecast) or inv.external_authority is None):raise ValueError()
            self.__bootstrap=(provider,p.model_dump_json(),cfg.model_dump_json(),scope.model_dump_json(),inv.model_dump_json(),
                options.model_dump_json(),authorize_preparation,verify_availability._availability,verify_availability._values,
                verify_training_labels,authorize_training,authorize_external_forecast,elapsed_clock)
            self.__issued=None;self.__busy=Lock();self.__used=False
        except Exception:raise CandidateTrainingError('invalid owned offline training composition') from None

    def __checkpoint(self,cancelled,clock,options):
        start=clock();previous=start
        def checkpoint():
            nonlocal previous
            now=clock();stop=cancelled()
            if (type(now) not in (float,int) or not math.isfinite(now) or type(start) not in (float,int)
                    or not math.isfinite(start) or now<previous or type(stop) is not bool or stop
                    or now-start>=options.max_elapsed_seconds):raise CandidateTrainingError('candidate interrupted or budget exhausted')
            previous=now
        checkpoint();return checkpoint

    def train(self,*,cancelled=lambda:False):
        if not self.__busy.acquire(blocking=False):raise CandidateTrainingError('candidate operation already running')
        try:
            if self.__used:raise CandidateTrainingError('training invocation is single use')
            self.__used=True
            (provider,pj,cj,sj,ij,tj,prep,release,values,labels,authorize,external,clock)=self.__bootstrap
            options=OfflineXGBoostConfiguration.model_validate_json(tj);checkpoint=self.__checkpoint(cancelled,clock,options)
            invocation=TrainingInvocation.model_validate_json(ij);plan=TemporalPreparationPlan.model_validate_json(pj)
            cfg=FeatureConfiguration.model_validate_json(cj);scope=TrainingHandoffScope.model_validate_json(sj)
            _approved(authorize,CandidateAuthorizationContext(ij,None,None,None),'begin');checkpoint()
            rows=len(plan.folds[0].training_origins)*len(plan.datasets[0].series_ids);cols=len(cfg.ordered_outputs)
            if not 2<=rows<=options.max_rows or not 1<=cols<=options.max_columns or rows*cols>options.max_cells:
                raise CandidateTrainingError('candidate matrix budget exceeded')
            evidence=_InvocationEvidence(options,checkpoint)
            owner=CanonicalTrainingPreparationService(provider,authorize_preparation=prep,
                verify_availability=ValueAttestedAvailabilityVerifier(verify_availability=evidence.wrap('release',release),
                    verify_captured_values=evidence.wrap('values',values)),training_scope=scope,
                verify_training_labels=evidence.wrap('labels',labels),authorize_external_forecast=evidence.wrap('external',external) if external else None,
                elapsed_clock=clock)
            def preparation_cancel():checkpoint();return False
            # Invoke the accepted implementation ourselves. Never accept a caller's
            # PREPARED declaration or use a digest as a permission to fit.
            handoff=CanonicalTrainingPreparationService.prepare_training(owner,plan,cfg,cancelled=preparation_cancel)
            if type(handoff) is not TrainingOnlyHandoff:raise CandidateTrainingError('owned handoff required')
            provenance=TrainingHandoffProvenance.model_validate_json(handoff.provenance.model_dump_json())
            if provenance.plan!=plan or provenance.scope_digest!=scope.digest:raise CandidateTrainingError('handoff binding mismatch')
            native=provenance.states[0]
            self.__validate_owned_handoff(handoff,evidence)
            for g in handoff.origins:
                if (g.partition!='training' or g.fold_id!=plan.folds[0].fold_id
                        or g.preparation.state_json!=native.state_json or g.preparation.schema_json!=native.representation_json):raise ValueError()
            frame=handoff.matrix;target=handoff.labels
            if len(frame)!=rows or not frame.index.equals(target.index) or tuple(frame.columns)!=cfg.ordered_outputs:raise ValueError()
            x=strict_float32(frame.to_numpy());y=strict_float32(target.to_numpy())
            runtime=_runtime();checkpoint()
            _approved(authorize,CandidateAuthorizationContext(ij,None,provenance.model_dump_json(),None),'fit');checkpoint()
            # Complete manifest binding first; the artifact digest is then computed
            # from actual native bytes, without a circular embedded model identity.
            fields=dict(invocation=invocation,training=options,runtime=runtime,scope=scope,
                parameters_json=__import__('src.data.contracts.dataset',fromlist=['operational_json']).operational_json(options.parameters),
                provenance_json=provenance.model_dump_json(),feature_names=tuple(frame.columns),
                feature_dtypes=tuple(str(d) for d in frame.dtypes),row_index=tuple(
                    tuple(v.isoformat() if type(v) is pd.Timestamp else str(v) for v in row) for row in frame.index),
                matrix_float32_hex=x.astype('<f4').tobytes().hex(),labels_float32_hex=y.astype('<f4').tobytes().hex(),
                predictions_float32_hex=np.zeros(len(x),dtype='<f4').tobytes().hex(),state_digest=digest(native.state_json.encode()))
            pre=CandidateManifest(**fields,artifact_digest='0'*64,artifact_size=1)
            checkpoint()
            model=self._fit_native(x,y,tuple(frame.columns),options,checkpoint)
            predictions=self._predict_native(model,x,tuple(frame.columns));checkpoint()
            fields['predictions_float32_hex']=predictions.astype('<f4').tobytes().hex()
            model.set_attr(**pre.native_binding)
            artifact=bytes(model.save_raw(raw_format='ubj'))
            if len(artifact)>options.max_artifact_bytes:raise CandidateTrainingError('native artifact budget exceeded')
            bundle=assemble_candidate(CandidateManifest(**fields,artifact_digest=digest(artifact),artifact_size=len(artifact)),artifact)
            self.__roundtrip(bundle,runtime,checkpoint)
            self.__final(handoff,evidence,authorize,prep,provider,ij,bundle,checkpoint)
            self.__issued=(bundle,handoff,evidence,runtime)
            return bundle
        except Exception:raise CandidateTrainingError('owned offline candidate training failed') from None
        finally:self.__busy.release()

    @staticmethod
    def __validate_owned_handoff(handoff,evidence):
        # A returned declaration alone is insufficient: this invocation must have
        # actually evaluated the original scopes with the fixed authorities.
        labels=[r for r in evidence.records if r[0]=='labels']
        if len(labels)!=len(handoff.origins):raise ValueError('missing owned label assessment')
        from src.services.canonical_training_preparation_service import historical_value_identity
        from src.data.contracts.dataset import operational_json
        for group,(_,callback,ctx,claims) in zip(handoff.origins,labels):
            prepared=group.preparation
            if (ctx.metadata!=prepared.context.metadata or ctx.forecast_origin!=prepared.context.forecast_origin
                    or ctx.fold_id!=group.fold_id or ctx.series_ids!=prepared.context.series_ids
                    or len(claims)!=len(prepared.original_labels)):
                raise ValueError('owned assessment scope substitution')
            expected=tuple(c for c in handoff.provenance.labels if c.forecast_origin==ctx.forecast_origin)
            if claims!=expected:raise ValueError('owned assessment claim substitution')
            for claim,value in zip(claims,prepared.original_labels):
                if claim.original_value_identity_json!=operational_json(historical_value_identity(value)):
                    raise ValueError('owned label value substitution')
            for kind in ('release','values'):
                if not any(r[0]==kind and r[2].forecast_origin==ctx.forecast_origin
                           and r[2].target_period_end==ctx.target_period_end for r in evidence.records):
                    raise ValueError('missing owned historical assessment')

    @staticmethod
    def _fit_native(x,y,names,options,checkpoint):
        import xgboost as xgb
        class Deadline(xgb.callback.TrainingCallback):
            def before_iteration(self,model,epoch,evals_log):checkpoint();return False
            def after_iteration(self,model,epoch,evals_log):checkpoint();return False
        checkpoint()
        data=xgb.DMatrix(x,label=y,feature_names=list(names),nthread=1)
        return xgb.train(options.parameters,data,num_boost_round=options.estimators,callbacks=[Deadline()],evals=[])

    @staticmethod
    def _predict_native(model,x,names):
        import xgboost as xgb
        result=model.predict(xgb.DMatrix(x,feature_names=list(names),nthread=1),validate_features=True)
        if result.dtype!=np.float32 or result.shape!=(len(x),) or not np.isfinite(result).all() or np.any(np.abs(result)>100_000_000):raise ValueError('invalid native predictions')
        return result

    def __roundtrip(self,bundle,runtime,checkpoint):
        import xgboost as xgb
        manifest,artifact=bundle.validate()
        if manifest.runtime!=runtime or _runtime()!=runtime:raise ValueError('runtime/source changed')
        model=xgb.Booster(params={'nthread':1});model.load_model(bytearray(artifact));checkpoint()
        if (model.num_features()!=len(manifest.feature_names) or tuple(model.feature_names or ())!=manifest.feature_names
                or model.num_boosted_rounds()!=manifest.training.estimators
                or model.attributes()!=manifest.native_binding):raise ValueError('native model representation mismatch')
        x=strict_float32(manifest.matrix.to_numpy());pred=self._predict_native(model,x,manifest.feature_names)
        if pred.astype('<f4').tobytes().hex()!=manifest.predictions_float32_hex:raise ValueError('native prediction roundtrip mismatch')
        checkpoint();return tuple(float(v) for v in pred)

    @staticmethod
    def __final(handoff,evidence,authorize,prep,provider,invocation_json,bundle,checkpoint):
        # Broad/calibration/external/read checks precede complete assembly and
        # validation. Terminal phases retain original immutable scoped claims.
        broad=[r for r in evidence.records if r[0]=='external' or r[2].target_period_end is None]
        scoped=[r for r in evidence.records if r not in broad]
        evidence.recheck(broad)
        pj=handoff.provenance.model_dump_json()
        _approved(authorize,CandidateAuthorizationContext(invocation_json,None,pj,bundle.bundle_id),'return')
        for metadata in {g.preparation.context.metadata for g in handoff.origins}:provider.require_current(metadata)
        bundle.validate();checkpoint()
        for kind,callback,ctx,claims in scoped:
            _approved(callback,ctx,claims)
            if kind=='labels':_approved(prep,ctx,'return_training_labels')
            elif kind=='values':_approved(prep,ctx,'return')
            _approved(authorize,CandidateAuthorizationContext(invocation_json,ctx,pj,bundle.bundle_id),'scoped_return')
        checkpoint()

    def verify_candidate(self,bundle,*,cancelled=lambda:False):
        """Native reload only for this owner's exact issued object, never a file/alias."""
        if not self.__busy.acquire(blocking=False):raise CandidateTrainingError('candidate operation already running')
        try:
            if self.__issued is None or bundle is not self.__issued[0]:raise ValueError('unowned candidate')
            _,handoff,evidence,runtime=self.__issued
            provider,pj,cj,sj,ij,tj,prep,release,values,labels,authorize,external,clock=self.__bootstrap
            checkpoint=self.__checkpoint(cancelled,clock,OfflineXGBoostConfiguration.model_validate_json(tj))
            evidence.checkpoint=checkpoint
            _approved(authorize,CandidateAuthorizationContext(ij,None,handoff.provenance.model_dump_json(),bundle.bundle_id),'verify');checkpoint()
            result=self.__roundtrip(bundle,runtime,checkpoint)
            self.__final(handoff,evidence,authorize,prep,provider,ij,bundle,checkpoint)
            return result
        except Exception:raise CandidateTrainingError('owned candidate verification failed') from None
        finally:self.__busy.release()
