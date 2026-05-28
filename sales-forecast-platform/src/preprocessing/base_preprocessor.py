from abc import ABC, abstractmethod


class BasePreprocessor(ABC):

    @abstractmethod
    def fit_transform(self, df):
        pass

    @abstractmethod
    def transform(self, df):
        pass
