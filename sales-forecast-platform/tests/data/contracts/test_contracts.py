from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.data.contracts import (
    AssetMetadata,
    AssetType,
    DataFormat,
    DocumentDataset,
    DocumentElement,
    DocumentMetadata,
    Frequency,
    ForecastConfig,
    ForecastFrequency,
    MultilingualMetadata,
    Observation,
    QualityDisposition,
    QualityStatus,
    StructuredDataset,
    ValueStatus,
)


def asset_metadata():
    now = datetime.now(timezone.utc)
    return AssetMetadata(
        asset_id=uuid4(),
        organization_id=uuid4(),
        source_id=uuid4(),
        ingestion_batch_id=uuid4(),
        created_at=now,
    )


def test_structured_dataset_accepts_daily_sales_observation():
    series_id = uuid4()
    observation = Observation(
        series_id=series_id,
        period_start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        period_end=datetime(2026, 9, 2, tzinfo=timezone.utc),
        frequency=Frequency.DAILY,
        metric="sales",
        value=1250.50,
        unit="SAR",
        currency="SAR",
        value_status=ValueStatus.ACTUAL,
    )

    dataset = StructuredDataset(
        asset=asset_metadata(),
        name="Retail Sales",
        source_format=DataFormat.CSV,
        input_frequency=Frequency.DAILY,
        canonical_frequency=Frequency.DAILY,
        metric_semantics=["sales"],
        forecast_config=ForecastConfig(
            forecast_frequency=ForecastFrequency.WEEKLY,
            forecast_horizon=8,
        ),
        observations=[observation],
    )

    assert dataset.asset_type == AssetType.STRUCTURED_DATASET
    assert dataset.observations[0].metric == "sales"
    assert dataset.forecast_config.forecast_horizon == 8


def test_observation_rejects_invalid_period():
    with pytest.raises(ValueError, match="period_end"):
        Observation(
            series_id=uuid4(),
            period_start=datetime(2026, 9, 2, tzinfo=timezone.utc),
            period_end=datetime(2026, 9, 1, tzinfo=timezone.utc),
            frequency=Frequency.DAILY,
            metric="sales",
            value=10,
            unit="SAR",
        )


def test_multilingual_metadata_supports_arabic_and_english():
    language = MultilingualMetadata(
        language="ar",
        languages=["ar", "en"],
        script="Arab-Latn",
        language_confidence=0.98,
    )

    assert language.is_multilingual is True
    assert "ar" in language.languages
    assert "en" in language.languages


def test_document_dataset_preserves_original_language_metadata():
    document = DocumentMetadata(
        document_id=uuid4(),
        filename="company_policy.pdf",
        media_type="application/pdf",
        source_format=DataFormat.PDF,
        language=MultilingualMetadata(
            languages=["ar", "en"],
            script="Arab-Latn",
        ),
    )

    element = DocumentElement(
        element_id=uuid4(),
        document_id=document.document_id,
        element_type="paragraph",
        page_number=1,
        text="سياسة الإجازات Annual Leave Policy",
        language=MultilingualMetadata(languages=["ar", "en"]),
        confidence=0.99,
    )

    dataset = DocumentDataset(
        asset=asset_metadata(),
        name="Company Policies",
        source_format=DataFormat.PDF,
        content={"documents": [document], "elements": [element]},
    )

    assert dataset.asset_type == AssetType.DOCUMENT_DATASET
    assert dataset.content.elements[0].language.is_multilingual


def test_contract_rejects_unknown_fields():
    with pytest.raises(ValueError):
        MultilingualMetadata(language="ar", unexpected="value")


def test_forecast_config_supports_daily_weekly_and_monthly_horizons():
    daily = ForecastConfig(
        forecast_frequency=ForecastFrequency.DAILY,
        forecast_horizon=30,
    )
    weekly = ForecastConfig(
        forecast_frequency=ForecastFrequency.WEEKLY,
        forecast_horizon=8,
    )
    monthly = ForecastConfig(
        forecast_frequency=ForecastFrequency.MONTHLY,
        forecast_horizon=12,
    )

    assert daily.forecast_horizon == 30
    assert weekly.forecast_horizon == 8
    assert monthly.forecast_horizon == 12


def test_forecast_horizon_must_be_positive():
    with pytest.raises(ValueError):
        ForecastConfig(
            forecast_frequency=ForecastFrequency.WEEKLY,
            forecast_horizon=0,
        )


def test_quality_report_can_accept_repairable_messy_data():
    from src.data.contracts import DataQualityReport

    report = DataQualityReport(
        status=QualityStatus.WARNING,
        disposition=QualityDisposition.PARTIALLY_REPAIRED,
        quality_score=0.91,
        row_count=1000,
        missing_count=20,
        duplicate_count=5,
        repaired_count=20,
        imputed_count=15,
        dropped_count=2,
        processing_operations=[
            "deduplicate",
            "impute_missing_values",
            "normalize_dates",
        ],
        warnings=["Minor missing values were repaired."],
    )

    assert report.disposition == QualityDisposition.PARTIALLY_REPAIRED
    assert report.repaired_count == 20
    assert report.imputed_count == 15
