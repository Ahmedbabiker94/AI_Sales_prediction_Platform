from src.features.history_feature_builder import (
    HistoryFeatureBuilder
)

builder = HistoryFeatureBuilder()

features = builder.build(
    store=1,
    dept=1
)

print(features)