"""Inspectable job identities; neither registration nor inspection builds jobs."""
from src.runtime.bootstrap import Runtime, require_runtime


INTERVALS = {
    'forecast_job': 60,
    'accuracy_job': 10,
    'report_job': 120,
    'production_model_sync_job': 5,
}


class _DeferredJob:
    def __init__(self, name, runtime):
        self._name = name
        self._runtime = runtime

    def run(self):
        return self._runtime.get(self._name).run()


def build_job_registry(runtime=None):
    active = require_runtime(runtime) if runtime is not None else Runtime()
    return {name: {'job': _DeferredJob(name, active), 'trigger': 'interval', 'minutes': minutes}
            for name, minutes in INTERVALS.items()}


JOB_REGISTRY = build_job_registry()


def configure_runtime(runtime):
    """Host startup only; existing references to the registry retain its identity."""
    configured = build_job_registry(require_runtime(runtime))
    JOB_REGISTRY.clear()
    JOB_REGISTRY.update(configured)
