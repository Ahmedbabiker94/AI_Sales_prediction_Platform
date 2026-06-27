import pandas as pd

from src.services.feature_enrichment_service import (
    FeatureEnrichmentService
)

df = pd.DataFrame([
    {
        "Store": 1,
        "Dept": 1
    }
])

service = (
    FeatureEnrichmentService()
)

result = service.enrich(df)

print(result.T)