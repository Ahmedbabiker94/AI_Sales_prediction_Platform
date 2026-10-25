"""Candidate-only tracking of structural integrity, never model approval.

Records are immutable snapshots. Active STAGED/INVALID/UNAVAILABLE state is
process-local. Finalized manifests are discoverable by exact ID after restart;
that discovery authenticates bytes only, not the original M6 execution owner.
"""
from threading import RLock
from src.data.contracts.candidate_package import PackageTrackingRecord
from src.repositories.candidate_package_repository import CandidatePackageRepository, package_key, reject_store, validate_publication


class CandidatePackageProvider:
    def __init__(self, repository):
        if type(repository) is not CandidatePackageRepository:
            reject_store()
        self._repository = repository
        self._records = {}
        self._lock = RLock()

    def _record(self, candidate_id, package_id, status):
        if candidate_id not in self._records and len(self._records) >= 32:
            reject_store()
        record = PackageTrackingRecord(candidate_id=candidate_id, package_id=package_id, status=status)
        self._records[candidate_id] = record
        return record

    def unavailable(self, candidate_id):
        package_key(candidate_id)
        with self._lock:
            previous = self._records.get(candidate_id)
            if previous is not None:
                return self.lookup(previous.package_id) if previous.status == 'FINALIZED' else previous
            return self._record(candidate_id, None, 'UNAVAILABLE')

    def publish(self, manifest, payload):
        with self._lock:
            manifest, _ = validate_publication(manifest, payload)
            cid, pid = manifest.candidate_id, manifest.package_id
            previous = self._records.get(cid)
            if previous is not None and previous.status != 'UNAVAILABLE':
                # Never retry INVALID/STAGED or silently replace a candidate ID.
                if previous.status != 'FINALIZED' or previous.package_id != pid:
                    reject_store()
                if self._repository.verify(pid) != manifest:
                    reject_store()
                return previous
            self._record(cid, pid, 'STAGED')
            try:
                self._repository.publish(manifest, payload)
                if self._repository.verify(pid) != manifest:
                    raise ValueError()
            except Exception:
                self._record(cid, pid, 'INVALID')
            else:
                return self._record(cid, pid, 'FINALIZED')
        reject_store()

    def lookup(self, package_id):
        package_key(package_id)
        with self._lock:
            try:
                manifest = self._repository.verify(package_id)
                previous = self._records.get(manifest.candidate_id)
                if previous is not None and (previous.package_id != package_id or previous.status != 'FINALIZED'):
                    raise ValueError()
                return self._record(manifest.candidate_id, package_id, 'FINALIZED')
            except Exception:
                for cid, record in tuple(self._records.items()):
                    if record.package_id == package_id:
                        return self._record(cid, package_id, 'INVALID')
        reject_store()

    def status(self, candidate_id):
        package_key(candidate_id)
        with self._lock:
            record = self._records.get(candidate_id)
            if record is None:
                return None
            if record.status == 'FINALIZED':
                return self.lookup(record.package_id)
            return record
