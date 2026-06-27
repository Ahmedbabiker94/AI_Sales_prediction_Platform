from abc import ABC, abstractmethod


class ForecastStorageProvider(ABC):

    @abstractmethod
    def save_forecast(
        self,
        store,
        dept,
        forecast_date,
        predicted_sales,
        model_version
    ):
        pass