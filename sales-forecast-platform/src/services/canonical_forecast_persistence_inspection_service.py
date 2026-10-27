"""R7-05A structural-only inspection. No store, writer or data-read dependency.

A protected host pins DECLARED scope, not an execution approval. Accepted runtime
inspection can initialize a SQLite source catalog; it is deliberately NOT called
by this strictly non-writing boundary. Fresh execution verification is unavailable.
"""
import math
from threading import Lock
from time import monotonic
from src.data.contracts.canonical_forecast_persistence import CanonicalH1ForecastDeclaration,ForecastPersistenceInspectionOutcome
from src.data.contracts.canonical_h1_predictor import CanonicalH1PredictionRequest


class ForecastPersistenceInspectionError(ValueError): pass


def _reject():
    error=ForecastPersistenceInspectionError('canonical forecast persistence composition unavailable')
    try: raise error from None
    finally: error.__cause__=None;error.__context__=None


class CanonicalForecastPersistenceInspectionService:
    """Metadata-only. The expected request is host-declared scope, NOT authority."""
    def __init__(self,expected_prediction,*,elapsed_clock=monotonic):
        try:
            if type(expected_prediction) is not CanonicalH1PredictionRequest or not callable(elapsed_clock): raise ValueError()
            p=CanonicalH1PredictionRequest.model_validate_json(expected_prediction.canonical_json)
            self._prediction_json=p.canonical_json;self._clock=elapsed_clock;self._self=self;self._lock=Lock()
            return
        except Exception: pass
        _reject()

    def inspect(self,declaration,*,execution_evidence=None,write_authority=None,cancelled=lambda:False):
        deny=lambda reason:ForecastPersistenceInspectionOutcome(status='DENIED',reason=reason)
        if self._self is not self: return deny('INVALID_DECLARATION_OR_DEPENDENCY')
        if not self._lock.acquire(blocking=False): return deny('BUSY')
        try:
            if type(declaration) is not CanonicalH1ForecastDeclaration or not callable(cancelled): raise ValueError()
            d=CanonicalH1ForecastDeclaration.model_validate_json(declaration.canonical_json);p=d.provenance.prediction
            if p.canonical_json!=self._prediction_json or len(d.canonical_bytes)>p.limits.max_request_bytes: raise ValueError()
            if execution_evidence is not None or write_authority is not None:
                return deny('UNSUPPORTED_EXECUTION_OR_WRITE_AUTHORITY')
            previous=None
            def now():
                nonlocal previous
                value=self._clock()
                if type(value) not in (int,float) or not math.isfinite(value) or previous is not None and value<previous: raise ValueError()
                previous=value;return value
            start=now()
            def check():
                return cancelled() is not False or now()-start>=p.limits.max_elapsed_seconds
            if check():return deny('CANCELLED_OR_EXPIRED')
            # No producer, predictor, catalog read, attestation or writer is
            # invoked. These IDs identify unverified declarations only.
            out=ForecastPersistenceInspectionOutcome(status='UNAVAILABLE',reason='EXECUTION_EVIDENCE_AND_WRITE_AUTHORITY_UNAVAILABLE',
                declaration_id=d.declaration_id,logical_key_id=d.logical_key_id,lineage_id=d.provenance.lineage_id,
                prediction_request_id=p.request_id)
            if len(out.canonical_bytes)>p.limits.max_output_bytes: raise ValueError()
            if check():return deny('CANCELLED_OR_EXPIRED')
            return out
        except Exception: pass
        finally:self._lock.release()
        return deny('INVALID_DECLARATION_OR_DEPENDENCY')
