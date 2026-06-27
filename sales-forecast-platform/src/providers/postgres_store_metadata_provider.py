from src.providers.store_metadata_provider import (
    StoreMetadataProvider
)

from src.services.store_metadata_service import (
    StoreMetadataService
)


class PostgresStoreMetadataProvider(
    StoreMetadataProvider
):

    def __init__(self):

        self.metadata_service = (
            StoreMetadataService()
        )

    def get_metadata(
        self,
        store: int
    ):

        return (
            self.metadata_service
            .get_metadata(store)
        )