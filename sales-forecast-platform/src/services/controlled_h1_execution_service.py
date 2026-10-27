"""Family-neutral one-shot offline H1 orchestration, not an API/runtime fallback.

Host must execute in the disposable isolated native worker. Clocks/cancellation
are cooperative; no in-process claim to preempt native calls. No M9/R7-03 grant
or R7-04A receipt is accepted as this independent execution capability.
"""
import math
from threading import Lock
from time import monotonic
from src.data.contracts.controlled_h1_execution import ControlledH1ExecutionRequest, ControlledH1ExecutionResult, ExecutionOutcome
from src.services.controlled_execution_authority import ControlledExecutionAuthority
from src.execution.controlled_h1_adapters import execution_adapter, NativeDependencyUnavailable


class ControlledH1ExecutionService:
    def __init__(self, authority, *, elapsed_clock=monotonic):
        if type(authority) is not ControlledExecutionAuthority or authority._self is not authority or not callable(elapsed_clock):
            raise ValueError('original execution authority required')
        self._authority=authority; self._clock=elapsed_clock; self._self=self; self._lock=Lock()

    def execute(self, reference, native, inputs, request, *, capability=None, cancelled=lambda:False):
        deny=lambda:ExecutionOutcome(status='DENIED',reason='INVALID_UNAUTHORIZED_OR_INTERRUPTED')
        if self._self is not self or not self._lock.acquire(blocking=False): return deny()
        try:
            if type(request) is not ControlledH1ExecutionRequest or not callable(cancelled): raise ValueError()
            req=ControlledH1ExecutionRequest.model_validate_json(request.canonical_json)
            metadata=self._authority._provider.metadata(reference)
            family=metadata.specification.family
            if family!=req.family or metadata.candidate_id!=req.candidate_id: raise ValueError()
            adapter=execution_adapter(family)
            if adapter is None:return ExecutionOutcome(status='UNAVAILABLE',reason='ADAPTER_UNAVAILABLE')
            previous=None;start=None; wall_previous=None; reservation=None
            def check(reserve=False):
                nonlocal previous,start,wall_previous,reservation
                # Every potentially reentrant host callback precedes live grant
                # lookup, including both clocks. No callback inside grant lock.
                stop=cancelled(); elapsed=self._clock(); now=self._authority._clock()
                if (stop is not False or type(elapsed) not in (int,float) or type(now) not in (int,float)
                        or not math.isfinite(elapsed) or not math.isfinite(now)
                        or previous is not None and elapsed<previous or wall_previous is not None and now<wall_previous): raise ValueError()
                if start is None:start=elapsed
                if elapsed-start>=req.limits.max_elapsed_seconds: raise ValueError()
                previous=elapsed; wall_previous=now
                evidence,grant,reservation=self._authority._validate(capability,reference,native,inputs,req,now,reservation,reserve)
                return evidence,grant
            evidence,grant=check(reserve=True)
            matrix=adapter.validate(evidence,req)
            check() # live owner/grant immediately before native parser
            loaded=adapter.load(evidence)
            check() # live owner/grant immediately before prediction
            values,digest=adapter.predict(loaded,matrix,evidence)
            result=ControlledH1ExecutionResult(request=req,grant=grant,adapter=adapter.adapter_id,predictions=values,prediction_sha256=digest)
            check() # last callbacks, then live owner/grant validation
            return ExecutionOutcome(status='EXECUTED',reason='CONTROLLED_VALIDATION_ONLY',result=result)
        except NativeDependencyUnavailable:
            return ExecutionOutcome(status='UNAVAILABLE',reason='NATIVE_DEPENDENCY_UNAVAILABLE')
        except Exception:
            return deny()
        finally:self._lock.release()
