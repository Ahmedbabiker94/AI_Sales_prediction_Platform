import socket

from src.repositories.scheduler_lock_repository import (
    SchedulerLockRepository
)


class SchedulerLockService:

    def __init__(self):

        self.repo = (
            SchedulerLockRepository()
        )

        self.scheduler_name = (
            "main_scheduler"
        )

        self.hostname = (
            socket.gethostname()
        )

    def acquire(self):

        return self.repo.acquire_lock(

            scheduler_name=self.scheduler_name,

            hostname=self.hostname

        )

    def release(self):

        self.repo.release_lock(

            scheduler_name=self.scheduler_name

        )

    def is_running(self):

        return self.repo.is_running(

            scheduler_name=self.scheduler_name

        )