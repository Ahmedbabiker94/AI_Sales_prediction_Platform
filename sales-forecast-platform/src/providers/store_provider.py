from src.services.store_metadata_service import (
    StoreMetadataService
)


class StoreProvider:

    def __init__(self):

        self.metadata_service = (
            StoreMetadataService()
        )

    def get_store_features(
        self,
        store: int
    ):

        return (
            self.metadata_service
            .get_metadata(store)
        )