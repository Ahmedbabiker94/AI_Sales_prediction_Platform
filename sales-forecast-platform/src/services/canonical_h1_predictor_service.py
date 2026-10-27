"""R7-04A contract admission only. No execution implementation or legacy fallback.

The original R7-03 service retains every original owner/repository/provider.
Neither it nor this service issues runtime execution authority. There is no
accepted execution verifier, payload loader or executable-input binder to call.
"""
import math
from threading import Lock
from time import monotonic
from src.data.contracts.canonical_h1_predictor import CanonicalH1PredictionRequest,CanonicalH1PredictionOutcome
from src.data.contracts.runtime_model_use import RuntimeModelUseDecision
from src.services.runtime_model_use_service import RuntimeModelUseService


class CanonicalH1PredictorError(ValueError): pass


def _reject():
    error=CanonicalH1PredictorError('canonical H1 predictor composition unavailable')
    try: raise error from None
    finally: error.__cause__=None;error.__context__=None


def _original(inspector):
    return (type(inspector) is RuntimeModelUseService and inspector._self is inspector
            and inspector._root.service is inspector)


class CanonicalH1PredictorService:
    """Protected host composition; never configure collaborators from request data."""
    def __init__(self,inspector,*,elapsed_clock=monotonic):
        try:
            if not _original(inspector) or not callable(elapsed_clock): raise ValueError()
            self._inspector=inspector;self._clock=elapsed_clock;self._self=self;self._lock=Lock()
            return
        except Exception: pass
        _reject()

    def predict(self,request,*,inspection_capability=None,execution_authority=None,cancelled=lambda:False):
        deny=lambda reason:CanonicalH1PredictionOutcome(status='DENIED',reason=reason)
        if self._self is not self: return deny('INVALID_REQUEST_OR_DEPENDENCY')
        if not self._lock.acquire(blocking=False): return deny('BUSY')
        try:
            if type(request) is not CanonicalH1PredictionRequest or not callable(cancelled): raise ValueError()
            req=CanonicalH1PredictionRequest.model_validate_json(request.canonical_json)
            if len(req.canonical_bytes)>req.limits.max_request_bytes or not _original(self._inspector): raise ValueError()
            # No accepted verifier exists. Never inspect/deserialise a supplied
            # object, including an inspection receipt or M9 approval, as a token.
            if execution_authority is not None: return deny('EXECUTION_AUTHORITY_UNSUPPORTED')
            previous=None
            def tick():
                nonlocal previous
                now=self._clock()
                if type(now) not in (int,float) or not math.isfinite(now) or previous is not None and now<previous:
                    raise ValueError()
                previous=now;return now
            start=tick()
            was_interrupted=False
            def interrupted():
                nonlocal was_interrupted
                stopped=cancelled() is not False or tick()-start>=req.limits.max_elapsed_seconds
                was_interrupted=was_interrupted or stopped
                return stopped
            # All host clock/cancellation callbacks run INSIDE the accepted
            # inspection, before its repaired final live capability validation.
            # Do not add a post-inspection callback that can invalidate authority.
            decision=self._inspector.decide(req.inspection,capability=inspection_capability,cancelled=interrupted)
            if type(decision) is not RuntimeModelUseDecision: raise ValueError()
            decision=RuntimeModelUseDecision.model_validate_json(decision.canonical_json)
            # A lower capture boundary can sanitize an interruption into generic
            # denial. Preserve our observed signal without another callback.
            if was_interrupted or decision.reason=='CANCELLED_OR_EXPIRED': return deny('CANCELLED_OR_EXPIRED')
            if decision.status=='DENIED':
                return deny('INSPECTION_DENIED')
            expected=(req.inspection.request_id,req.inspection.candidate_id,req.inspection.package_id,
                      req.inspection.snapshot_id,req.inspection.family)
            if (decision.request_id,decision.candidate_id,decision.package_id,decision.snapshot_id,decision.family)!=expected:
                raise ValueError()
            # UNAVAILABLE is readiness evidence, NEVER execution permission.
            result=CanonicalH1PredictionOutcome(status='UNAVAILABLE',reason='EXECUTION_DEPENDENCIES_UNAVAILABLE',
                request_id=req.request_id,scope_id=req.scope_id,purpose_id=req.purpose_id,
                inspection_decision_id=decision.decision_id,candidate_id=decision.candidate_id,
                package_id=decision.package_id,snapshot_id=decision.snapshot_id,family=decision.family)
            if len(result.canonical_bytes)>req.limits.max_output_bytes: raise ValueError()
            return result
        except Exception: pass
        finally: self._lock.release()
        return deny('INVALID_REQUEST_OR_DEPENDENCY')
