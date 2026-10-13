"""Private storage seam; underscore operations are trusted ingestion internals.

No public seal, dataset publication or training-read operation is provided.
Authorization is owned by the integration service, not stored identifiers.
Completion is claimed once from the registered lifecycle owner; no receipt or
reusable key is issued. Services, repositories and injected collaborators are
trusted application code. This is not a sandbox against Python memory mutation.
"""
from abc import ABC, abstractmethod


class StagingError(ValueError):
    """Bounded public failure; never includes raw records or source credentials."""


class DatasetStoreProvider(ABC):
    @abstractmethod
    def _begin(self, *, dataset_id, asset, mapping, budgets, consistency, owner): ...

    @abstractmethod
    def _append(self, attempt, raw, canonical): ...

    @abstractmethod
    def _seal(self, attempt, *, owner, checkpoint): ...

    @abstractmethod
    def _abort(self, attempt): ...

    @abstractmethod
    def _release(self, attempt): ...


# The trusted service registers its concrete lifecycle implementation at import.
# This avoids a repository -> service import and issues no caller-mintable proof.
_lifecycle_owner_type = None


def _validate_lifecycle_owner(owner, store):
    if _lifecycle_owner_type is None or type(owner) is not _lifecycle_owner_type or owner.store is not store:
        raise StagingError('registered ingestion lifecycle owner required')
