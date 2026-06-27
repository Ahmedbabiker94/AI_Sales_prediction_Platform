from src.repositories.store_metadata_repository import (
    StoreMetadataRepository
)


class StoreMetadataService:

    def __init__(self):

        self.repo = (
            StoreMetadataRepository()
        )

    def get_metadata(
        self,
        store: int
    ):

        metadata = (
            self.repo.get_store_metadata(
                store
            )
        )

        if metadata is None:

            raise ValueError(
                f"Store {store} not found"
            )

        return metadata