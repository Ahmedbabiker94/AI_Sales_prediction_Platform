from abc import ABC, abstractmethod


class ExternalFeaturesProvider(ABC):

    @abstractmethod
    def get_features(
        self,
        store,
        dept
    ):
        pass