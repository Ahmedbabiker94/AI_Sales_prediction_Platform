from src.preprocessing.tree_preprocessor import (
    TreePreprocessor
)


def get_preprocessor(model_type: str):

    if model_type == "xgboost":
        return TreePreprocessor()

    raise ValueError(
        f"Unsupported preprocessor: {model_type}"
    )
