
from src.preprocessing.tree_preprocessor import (
    TreePreprocessor
)

from src.preprocessing.transformer_preprocessor import (
    TransformerPreprocessor
)

# --------------------------------------------------
# PREPROCESSOR REGISTRY
# --------------------------------------------------

PREPROCESSOR_REGISTRY = {
    "xgboost": TreePreprocessor,
    "lightgbm": TreePreprocessor,
    "catboost": TreePreprocessor,
    "transformer": TransformerPreprocessor,
}


# --------------------------------------------------
# FACTORY
# --------------------------------------------------

def get_preprocessor(model_type: str):

    preprocessor_class = PREPROCESSOR_REGISTRY.get(
        model_type
    )

    if preprocessor_class is None:

        raise ValueError(
            f"Unsupported preprocessor: {model_type}"
        )

    return preprocessor_class()
