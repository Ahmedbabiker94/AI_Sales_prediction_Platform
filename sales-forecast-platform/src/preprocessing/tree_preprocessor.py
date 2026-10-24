"""Legacy raw-frame preprocessing is fail-closed; use canonical tabular service.

The prior implementation independently encoded inference categories and fitted
combined partitions. It cannot establish feature availability from CSV values.
"""
from src.preprocessing.base_preprocessor import BasePreprocessor


class TreePreprocessor(BasePreprocessor):
    def fit_transform(self, df):
        raise ValueError("canonical temporal preparation required; raw tabular fitting is disabled")

    def transform(self, df):
        raise ValueError("original frozen canonical tabular state required")
