"""Phase 0.5 - Market profile contracts.

MarketProfile describes local technical/data conventions.
Business facts such as the currency of a particular dataset are intentionally
not forced by the market profile; they belong to the source/data metadata.
"""

from pydantic import Field

from src.data.contracts.common import ContractModel


class MarketProfile(ContractModel):
    """Local technical conventions shared by a market."""

    market_code: str = Field(min_length=2, max_length=16)
    country_code: str = Field(min_length=2, max_length=2)
    locale: str = Field(min_length=2, max_length=32)
    timezone: str = Field(min_length=1, max_length=64)
    supported_calendars: list[str] = Field(
        default_factory=lambda: ["gregorian"]
    )
    supported_languages: list[str] = Field(
        default_factory=lambda: ["en"]
    )


class SaudiMarketProfile(MarketProfile):
    """Saudi Arabia technical market profile for data processing.

    Currency is deliberately not fixed here. A Saudi-based company can
    receive or report a dataset in SAR, USD, or another currency. The actual
    currency belongs to the dataset/measurement/source metadata.
    """

    market_code: str = "SA"
    country_code: str = "SA"
    locale: str = "ar-SA"
    timezone: str = "Asia/Riyadh"
    supported_calendars: list[str] = Field(
        default_factory=lambda: ["gregorian", "hijri"]
    )
    supported_languages: list[str] = Field(
        default_factory=lambda: ["ar", "en"]
    )
