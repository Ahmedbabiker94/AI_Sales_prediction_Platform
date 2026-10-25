"""M6 same-process operator composition; candidate-only, no registry or I/O.

Services prepare their own inputs through original canonical providers. Public
metadata/receipts do not authenticate buffers or confer D2 ownership/model use.
"""
from dataclasses import dataclass
from datetime import timezone
from threading import Lock
from time import monotonic
import numpy as np

from src.data.contracts.backtesting import identity
from src.data.contracts.model_evaluation import _reject_declaration
from src.data.contracts.temporal_preparation import TemporalPreparationPlan
from src.data.contracts.features import FeatureConfiguration
from src.data.contracts.training_adapter import TrainingSpecification,TrainingCandidateMetadata,TrainingReport,TrainingStatus,capability
from src.preprocessing.canonical_tabular import TabularOutput
from src.preprocessing.canonical_recurrent import RecurrentOutput,RecurrentPreparationRequest,_number
from src.preprocessing.canonical_transformer import TransformerOutput,TransformerPreparationRequest
from src.services.canonical_tabular_preparation_service import CanonicalTabularPreparationService
from src.services.canonical_recurrent_preparation_service import CanonicalRecurrentPreparationService
from src.services.canonical_transformer_preparation_service import CanonicalTransformerPreparationService
from src.training.canonical_adapters import TrainingBatch,FamilyTrainingAdapter,NativeBackend,FrameworkUnavailable,parameter_bound


class CandidateReference:
    """Opaque process-local reference. Copies and metadata confer no ownership."""
    __slots__=()


@dataclass(frozen=True)
class TrainingOutcome:
    status: TrainingStatus
    metadata: TrainingCandidateMetadata | None=None
    candidate: CandidateReference | None=None
    # Fixed status codes only; never framework/input exception details.
    @property
    def public_report(self):return TrainingReport(status=self.status,metadata=self.metadata)


@dataclass(frozen=True)
class TrainingApprovalContext:
    specification: TrainingSpecification
    plan_digest: str
    preparation_id: str
    fitted_state_id: str


class _Interrupted(Exception):pass
class _Denied(Exception):pass
class _Limit(Exception):pass


def _assemble(source,spec,plan):
    cap=capability(spec.family)
    cls={'tabular':TabularOutput,'recurrent':RecurrentOutput,'transformer':TransformerOutput}[cap.representation]
    if type(source.outputs) is not tuple or type(source.targets) is not tuple or len(source.outputs)!=len(source.targets):raise ValueError()
    if not source.outputs:raise ValueError()
    state_id=source.outputs[0].state.state_id
    compatible=source.outputs[0].representation.compatibility_id
    vectors=[];ys=[];missing=[];padding=[];lengths=[];grids=[];encoded=0
    for original,targets in zip(source.outputs,source.targets):
        if type(original) is not cls or original.model_family!=spec.family:raise ValueError()
        r=original.representation
        if (r.binding.plan!=plan or original.state.state_id!=state_id or r.compatibility_id!=compatible):raise ValueError()
        if r.binding.partition!='training':continue  # no held-out numeric access
        out=cls.model_validate_json(original.canonical_json)
        encoded+=len(out.canonical_bytes)
        if encoded>spec.resources.max_bytes:raise _Limit()
        if len(targets)!=len(r.samples):raise ValueError()
        for grid,y in zip(r.samples,targets):
            if len(grid)!=1:raise ValueError()
            p=grid[0];grids.append((str(p.organization_id),str(p.series_id),p.forecast_origin.astimezone(timezone.utc).isoformat()))
            target_dtype='float32' if cap.target_policy=='strict_exact_float32_v1' else spec.input_dtype
            ys.append((_number(y,target_dtype),))
        if cap.representation=='tabular':
            if any(any(row) for row in r.missing_mask):raise ValueError('missing tree inputs unsupported')
            vectors.append((tuple(tuple(_number(v,spec.input_dtype) for v in row) for row in out.values),))
        else:
            if r.tensor_dtype!=spec.input_dtype:raise ValueError()
            values=(out.values,) if cap.representation=='recurrent' else (out.encoder_values,out.static_values,out.known_future_values)
            vectors.append(values);missing.extend(r.masks.missing);padding.extend(r.masks.padding);lengths.extend(r.masks.lengths)
    if not ys or len(ys)>spec.resources.max_rows or len(set(grids))!=len(grids):raise _Limit()
    fold=plan.folds[0];org=str(plan.datasets[0].reference.organization_id)
    allowed={(org,str(s),o.astimezone(timezone.utc).isoformat()) for s in plan.datasets[0].series_ids for o in fold.training_origins}
    if not set(grids)<=allowed:raise ValueError()
    # Each selected origin must cover all original series, not a convenient subset.
    selected={o for _,_,o in grids}
    if len(grids)!=len(selected)*len(plan.datasets[0].series_ids):raise ValueError()
    streams=[]
    for i in range(len(vectors[0])):
        values=tuple(row for chunk in vectors for row in chunk[i])
        array=np.array(values,dtype=spec.input_dtype)
        if not np.isfinite(array).all():raise ValueError()
        array.flags.writeable=False;streams.append(array)
    target_dtype='float32' if cap.target_policy=='strict_exact_float32_v1' else spec.input_dtype
    y=np.array(ys,dtype=target_dtype);y.flags.writeable=False
    if cap.representation=='tabular':meta=(None,None,None);offsets=()
    else:
        meta=(np.array(missing,dtype=bool),np.array(padding,dtype=bool),np.array(lengths,dtype=np.int64))
        for a in meta:a.flags.writeable=False
        offsets=source.outputs[0].representation.window.past_offsets
    arrays=tuple(streams)+(y,)+tuple(a for a in meta if a is not None)
    cells=sum(a.size for a in arrays)
    if offsets:cells+=streams[0].size+streams[0].shape[0]*streams[0].shape[1] # generated mask/offset channels
    byte_size=sum(a.nbytes for a in arrays)+encoded
    work=cells*spec.steps*(spec.hidden_size**2 if offsets else 8)
    batch=TrainingBatch(spec.family,tuple(streams),y,*meta,offsets)
    if (cells>spec.resources.max_cells or byte_size>spec.resources.max_bytes or work>spec.resources.max_work_units
            or parameter_bound(batch,spec)>(spec.resources.max_tree_nodes if cap.representation=='tabular' else spec.resources.max_parameters)):raise _Limit()
    digest=identity([list(grids),[[str(a.dtype),list(a.shape),a.astype(a.dtype.newbyteorder('>'),copy=True).tobytes().hex()] for a in arrays],list(offsets)])
    return batch,state_id,compatible,digest


class CanonicalAdapterTrainingService:
    def __init__(self,tabular,recurrent,transformer,*,authorize_training,backend_factory=NativeBackend):
        # Trusted host bootstrap only. Do not inject these through request data.
        if (type(tabular) is not CanonicalTabularPreparationService or type(recurrent) is not CanonicalRecurrentPreparationService
                or type(transformer) is not CanonicalTransformerPreparationService
                or recurrent._tabular is not tabular or transformer._tabular is not tabular
                or not callable(authorize_training) or not callable(backend_factory)):_reject_declaration()
        self._services=(tabular,recurrent,transformer)
        self._authorize=authorize_training;self._backend_factory=backend_factory
        self._lock=Lock();self._candidates={}

    def owns(self,reference):
        record=self._candidates.get(id(reference))
        return record is not None and record[0] is self and record[1] is reference

    def discard(self,reference):
        if not self.owns(reference):return False
        del self._candidates[id(reference)];return True

    def train(self,specification,*,plan=None,feature_configuration=None,preparation_request=None,cancelled=lambda:False):
        # Unknown family rejects before touching plans, providers or dependencies.
        family=specification.get('family') if type(specification) is dict else getattr(specification,'family',None) if type(specification) is TrainingSpecification else None
        if type(family) is not str or family not in ('xgboost','lightgbm','lstm','gru','transformer'):return TrainingOutcome('UNSUPPORTED_FAMILY')
        if not self._lock.acquire(blocking=False):return TrainingOutcome('BUSY')
        try:
            raw=specification.canonical_json if type(specification) is TrainingSpecification else None
            spec=TrainingSpecification.model_validate_json(raw) if raw is not None else TrainingSpecification.model_validate(specification)
            started=monotonic();context=None;preparation_interrupted=False
            def checkpoint():
                if cancelled() is not False or monotonic()-started>=spec.resources.max_elapsed_seconds:raise _Interrupted()
                if context is not None:
                    if self._authorize(context) is not True:raise _Denied()
                    # Approval may itself cancel work or consume the deadline.
                    if cancelled() is not False or monotonic()-started>=spec.resources.max_elapsed_seconds:raise _Interrupted()
            def preparation_cancelled():
                nonlocal preparation_interrupted
                preparation_interrupted=cancelled() is not False or monotonic()-started>=spec.resources.max_elapsed_seconds
                return preparation_interrupted
            checkpoint()
            if len(self._candidates)>=8:raise _Limit()
            if type(plan) is not TemporalPreparationPlan or type(feature_configuration) is not FeatureConfiguration:raise ValueError()
            plan=TemporalPreparationPlan.model_validate(plan.model_dump(warnings='error'))
            feature_configuration=FeatureConfiguration.model_validate_json(feature_configuration.model_dump_json(warnings='error'))
            if len(plan.datasets)!=1 or len(plan.folds)!=1 or plan.forecast_horizon!=1:raise ValueError()
            cap=capability(family)
            if cap.representation=='tabular':
                if preparation_request is not None:raise ValueError()
                source=self._services[0].prepare(plan,feature_configuration,model_family=family,dtype=spec.input_dtype,cancelled=preparation_cancelled)
            else:
                cls=TransformerPreparationRequest if family=='transformer' else RecurrentPreparationRequest
                if type(preparation_request) is not cls:raise ValueError()
                req=cls.model_validate_json(preparation_request.canonical_json)
                if (req.model_family!=family or req.tensor_dtype!=spec.input_dtype or req.fold_id!=plan.folds[0].fold_id
                        or not set(req.origins)&set(plan.folds[0].training_origins)):raise ValueError()
                service=self._services[2] if family=='transformer' else self._services[1]
                source=service.prepare(plan,feature_configuration,req,cancelled=preparation_cancelled)
            checkpoint()
            batch,state_id,compatible,input_digest=_assemble(source,spec,plan)
            context=TrainingApprovalContext(spec,plan.digest,source.preparation_id,state_id)
            checkpoint()
            model,kind,version=FamilyTrainingAdapter(family).fit(batch,spec,self._backend_factory,checkpoint)
            metadata=TrainingCandidateMetadata(specification=spec,capability=cap,plan_digest=plan.digest,preparation_id=source.preparation_id,
                fitted_state_id=state_id,representation_compatibility_id=compatible,training_rows=len(batch.targets),
                input_geometry=tuple(tuple(a.shape) for a in batch.streams),input_digest=input_digest,
                backend=kind,backend_version=version,target_shape=tuple(batch.targets.shape),target_dtype=str(batch.targets.dtype))
            checkpoint();reference=CandidateReference()
            # Only after every final check; no partial model reference escapes.
            self._candidates[id(reference)]=(self,reference,metadata,model,source.outputs[0].state)
            return TrainingOutcome('SUCCESS',metadata,reference)
        except FrameworkUnavailable:return TrainingOutcome('FRAMEWORK_UNAVAILABLE')
        except _Interrupted:return TrainingOutcome('CANCELLED_OR_EXPIRED')
        except _Denied:return TrainingOutcome('DENIED')
        except _Limit:return TrainingOutcome('RESOURCE_LIMIT')
        except Exception:
            return TrainingOutcome('CANCELLED_OR_EXPIRED' if locals().get('preparation_interrupted',False) else 'FAILED')
        finally:self._lock.release()
