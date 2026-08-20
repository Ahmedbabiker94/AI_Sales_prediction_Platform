from apscheduler.schedulers.blocking import BlockingScheduler
import signal
import sys
from src.jobs.job_registry import JOB_REGISTRY
from src.services.scheduler_lock_service import (
    SchedulerLockService
)

scheduler = BlockingScheduler()
lock_service = SchedulerLockService()


def register_jobs():

    for job_name, config in JOB_REGISTRY.items():

        trigger = config["trigger"]

        job_args = {

            "func": config["job"].run,

            "trigger": trigger,

            "id": job_name,

            "replace_existing": True

        }

        if trigger == "interval":

            if config.get("minutes") is not None:

                job_args["minutes"] = config["minutes"]

            if config.get("hours") is not None:

                job_args["hours"] = config["hours"]

        elif trigger == "cron":

            if config.get("day") is not None:

                job_args["day"] = config["day"]

            if config.get("day_of_week") is not None:

                job_args["day_of_week"] = config["day_of_week"]

            if config.get("hour") is not None:

                job_args["hour"] = config["hour"]

            if config.get("minute") is not None:

                job_args["minute"] = config["minute"]

        scheduler.add_job(**job_args)

        print(f"Registered {job_name} ({trigger})")

def shutdown_scheduler(signum, frame):

    print("\nStopping Scheduler...")

    try:

        lock_service.release()

        print("Scheduler lock released.")

    except Exception as e:

        print(
            f"Failed to release scheduler lock: {e}"
        )

    scheduler.shutdown(wait=True)

    print("Scheduler stopped successfully.")

    sys.exit(0)

def start_scheduler():

    acquired = lock_service.acquire()

    if not acquired:

        print(
            "Another scheduler is already running."
        )

        return

    print(
        "Scheduler lock acquired."
    )

    register_jobs()

    signal.signal(
        signal.SIGINT,
        shutdown_scheduler
    )

    signal.signal(
        signal.SIGTERM,
        shutdown_scheduler
    )

    print(
        "Scheduler started..."
    )

    scheduler.start()
    
if __name__ == "__main__":

    start_scheduler()