from abc import ABC, abstractmethod


class AccuracyStorageProvider(ABC):

    @abstractmethod
    def save_accuracy(
        self,
        store,
        dept,
        forecast_date,
        predicted_sales,
        actual_sales,
        absolute_error,
        percentage_error
    ):
        pass