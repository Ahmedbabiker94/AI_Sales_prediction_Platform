from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[2]

PRODUCTION_ROOT = (
    ROOT
    / "models"
    / "production"
)

CURRENT_MANIFEST_PATH = (
    PRODUCTION_ROOT
    / "current.json"
)


def _load_current_manifest():

    if not CURRENT_MANIFEST_PATH.exists():

        raise FileNotFoundError(
            f"Production manifest not found: "
            f"{CURRENT_MANIFEST_PATH}"
        )

    with open(
        CURRENT_MANIFEST_PATH,
        "r",
        encoding="utf-8"
    ) as file:

        return json.load(file)


def get_production_model_type():

    manifest = _load_current_manifest()

    model_type = manifest.get(
        "model_type"
    )

    if not model_type:

        raise ValueError(
            "Production model_type "
            "not found in current manifest."
        )

    return model_type


def get_production_model_artifact_path():

    manifest = _load_current_manifest()

    artifact_path = manifest.get(
        "artifact_path"
    )

    if not artifact_path:

        raise ValueError(
            "Production artifact_path "
            "not found in current manifest."
        )

    path = (
        ROOT
        / Path(artifact_path)
    ).resolve()

    if not path.exists():

        raise FileNotFoundError(
            f"Production artifact directory "
            f"not found: {path}"
        )

    model_type = get_production_model_type()

    if model_type == "xgboost":

        model_path = (
            path
            / "model.ubj"
        )

    else:

        raise ValueError(
            f"Unsupported production model type: "
            f"{model_type}"
        )

    if not model_path.exists():

        raise FileNotFoundError(
            f"Production model artifact not found: "
            f"{model_path}"
        )

    return model_path