from src.preprocessing.base_preprocessor import BasePreprocessor


class TransformerPreprocessor(BasePreprocessor):

    def fit_transform(self, df):
        raise NotImplementedError(
            "Transformer preprocessing not implemented yet."
        )

    def transform(self, df):
        raise NotImplementedError(
            "Transformer preprocessing not implemented yet."
        )
