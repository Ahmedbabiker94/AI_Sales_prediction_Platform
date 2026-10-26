"""Explicit scheduler startup; imports/registry inspection perform no operations."""
import signal
import sys
from src.jobs import job_registry
from src.runtime.bootstrap import Runtime, require_runtime, unavailable

_runtime = Runtime()
scheduler = None
lock_service = None
_lock_held = False

JOB_DISPLAY_NAMES = {
    'forecast_job': 'ForecastJob.run',
    'accuracy_job': 'ForecastAccuracyJob.run',
    'report_job': 'ReportGenerationJob.run',
    'production_model_sync_job': 'ProductionModelSyncJob.run',
}


def configure_runtime(runtime):
    """Protected startup only. Do not reconfigure a running scheduler."""
    global _runtime, scheduler, lock_service
    if _lock_held:
        unavailable()
    _runtime = require_runtime(runtime)
    job_registry.configure_runtime(_runtime)
    scheduler = None
    lock_service = None


def register_jobs(*, scheduler_instance=None, registry=None):
    active = scheduler_instance if scheduler_instance is not None else _runtime.get('scheduler')
    configured = job_registry.JOB_REGISTRY if registry is None else registry
    for job_name, config in configured.items():
        trigger = config['trigger']
        job_args = dict(func=config['job'].run, trigger=trigger, id=job_name, replace_existing=True)
        if job_name in JOB_DISPLAY_NAMES:
            job_args['name'] = JOB_DISPLAY_NAMES[job_name]
        fields = ('minutes', 'hours') if trigger == 'interval' else ('day', 'day_of_week', 'hour', 'minute')
        for field in fields:
            if config.get(field) is not None:
                job_args[field] = config[field]
        active.add_job(**job_args)
        print(f'Registered {job_name} ({trigger})')


def _release_lock():
    global _lock_held
    if _lock_held:
        lock_service.release()
        _lock_held = False


def shutdown_scheduler(signum, frame):
    try:
        _release_lock()
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=True)
    sys.exit(0)


def start_scheduler(*, install_signal_handlers=True):
    global scheduler, lock_service, _lock_held
    if _lock_held:
        unavailable()
    scheduler = _runtime.get('scheduler')
    lock_service = _runtime.get('scheduler_lock')
    if lock_service.acquire() is not True:
        print('Another scheduler is already running.')
        return
    _lock_held = True
    try:
        register_jobs(scheduler_instance=scheduler)
        if install_signal_handlers:
            signal.signal(signal.SIGINT, shutdown_scheduler)
            signal.signal(signal.SIGTERM, shutdown_scheduler)
        scheduler.start()
    finally:
        _release_lock()


if __name__ == '__main__':
    start_scheduler()
