"""Small process-local factory boundary, not authentication or model-use authority.

Only trusted startup code supplies factories. Empty/default composition denies
operations; there is no fallback to legacy constructors or TEST_ONLY models.
"""
from threading import RLock
from types import MappingProxyType


NAMES = frozenset({
    'forecast', 'recursive_forecast', 'dashboard', 'report', 'monitoring',
    'job_health', 'forecast_task', 'task_backend', 'scheduler', 'scheduler_lock',
    'forecast_job', 'accuracy_job', 'report_job', 'production_model_sync_job',
    'job_status', 'job_execution', 'job_tracker', 'job_metrics',
    'accuracy_service', 'report_generator', 'pdf_writer', 'production_sync',
})


class RuntimeUnavailable(RuntimeError):
    """No usable collaborator was provided; never denotes model authorization."""


def unavailable():
    error = RuntimeUnavailable('Runtime dependency unavailable')
    try:
        raise error from None
    finally:
        error.__cause__ = None
        error.__context__ = None


class Runtime:
    def __init__(self, factories=None):
        if factories is None:
            factories = {}
        if (type(factories) is not dict or not set(factories) <= NAMES
                or any(not callable(factory) for factory in factories.values())):
            unavailable()
        self._factories = MappingProxyType(dict(factories))
        self._instances = {}
        self._building = set()
        self._lock = RLock()

    def get(self, name):
        # Serialize construction only. This is not a lock/lease for operations on
        # returned services, which retain their own authorization/concurrency rules.
        with self._lock:
            if type(name) is not str or name not in self._factories or name in self._building:
                unavailable()
            if name in self._instances:
                return self._instances[name]
            self._building.add(name)
            try:
                value = self._factories[name]()
                if value is None:
                    raise ValueError()
            except Exception:
                pass
            else:
                self._instances[name] = value
                return value
            finally:
                self._building.remove(name)
        unavailable()

    def is_resolved(self, name):
        with self._lock:
            return name in self._instances


def require_runtime(runtime):
    if type(runtime) is not Runtime:
        unavailable()
    return runtime
