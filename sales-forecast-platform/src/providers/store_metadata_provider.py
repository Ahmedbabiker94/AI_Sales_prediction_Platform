from abc import ABC, abstractmethod


class StoreMetadataProvider(ABC):

    @abstractmethod
    def get_metadata(
        self,
        store: int
    ):
        pass