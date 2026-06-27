from src.providers.store_provider import (
    StoreProvider
)


class StoreFeatureProvider:

    def __init__(self):

        self.store_provider = (
            StoreProvider()
        )

    def get_features(
        self,
        store: int
    ):

        return (
            self.store_provider
            .get_store_features(store)
        )