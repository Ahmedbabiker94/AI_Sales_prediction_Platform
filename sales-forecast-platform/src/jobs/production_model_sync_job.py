"""Explicit sync job boundary; no production module is imported by inspection."""
from src.runtime.bootstrap import Runtime, require_runtime


class ProductionModelSyncJob:
    def __init__(self, *, runtime=None):
        self._runtime = require_runtime(runtime) if runtime is not None else Runtime()

    def run(self):
        result = self._runtime.get('production_sync').sync()
        print('Production model sync completed:')
        print(result)
        return result
