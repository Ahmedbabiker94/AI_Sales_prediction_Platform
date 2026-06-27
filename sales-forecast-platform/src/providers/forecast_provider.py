from abc import ABC
from abc import abstractmethod


class ForecastProvider(ABC):

    @abstractmethod
    def get_forecast(
        self,
        store,
        dept,
        forecast_date
    ):
        pass
    @abstractmethod
    def get_forecasts_without_accuracy(self):
        pass