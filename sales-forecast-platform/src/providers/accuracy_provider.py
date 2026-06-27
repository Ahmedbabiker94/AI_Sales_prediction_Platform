from abc import ABC
from abc import abstractmethod


class AccuracyProvider(ABC):

    @abstractmethod
    def save_accuracy(
        self,
        store: int,
        dept: int,
        forecast_date,
        predicted_sales: float,
        actual_sales: float,
        absolute_error: float,
        percentage_error: float
    ):
        pass