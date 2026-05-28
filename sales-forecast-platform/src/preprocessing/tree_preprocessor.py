from src.features.feature_pipeline import prepare_features
from src.preprocessing.base_preprocessor import BasePreprocessor


class TreePreprocessor(BasePreprocessor):

    def fit_transform(self, df):
        return prepare_features(df)

    def transform(self, df):
        return prepare_features(df)
