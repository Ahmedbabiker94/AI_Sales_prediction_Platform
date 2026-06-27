from abc import ABC, abstractmethod


class SalesDataProvider(ABC):

    @abstractmethod
    def get_history(
        self,
        store: int,
        dept: int
    ):
        pass
    @abstractmethod
    def get_actual_sales(
        self,
        store,
        dept,
        date
    ):
        pass