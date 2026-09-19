from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]

PRODUCTION_MODEL_DIR = (
    ROOT
    / "models"
    / "production_xgboost"
)

MLMODEL_PATH = (
    PRODUCTION_MODEL_DIR
    / "MLmodel"
)


def _load_model_metadata():

    if not MLMODEL_PATH.exists():

        raise FileNotFoundError(
            f"Production MLmodel not found: "
            f"{MLMODEL_PATH}"
        )

    with open(
        MLMODEL_PATH,
        "r",
        encoding="utf-8"
    ) as file:

        return yaml.safe_load(file)


def get_production_model_type():

    metadata = _load_model_metadata()

    flavors = metadata.get(
        "flavors",
        {}
    )

    if "xgboost" in flavors:

        return "xgboost"

    raise ValueError(
        "Unsupported production model type. "
        f"Available flavors: {list(flavors.keys())}"
    )
def get_production_model_artifact_path():

    metadata = _load_model_metadata()

    flavors = metadata.get(
        "flavors",
        {}
    )

    model_type = get_production_model_type()

    flavor_metadata = flavors.get(
        model_type
    )

    if flavor_metadata is None:

        raise ValueError(
            f"No metadata found for "
            f"production model type: {model_type}"
        )

    model_filename = flavor_metadata.get(
        "data"
    )

    if not model_filename:

        raise ValueError(
            f"Model artifact filename not found "
            f"for model type: {model_type}"
        )

    artifact_path = (
        PRODUCTION_MODEL_DIR
        / model_filename
    )

    if not artifact_path.exists():

        raise FileNotFoundError(
            f"Production model artifact not found: "
            f"{artifact_path}"
        )

    return artifact_path