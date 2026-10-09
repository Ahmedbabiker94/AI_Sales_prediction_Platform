import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import mlflow
import yaml
from mlflow.tracking import MlflowClient


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

MODEL_NAME = "sales_forecasting_model"

MLFLOW_TRACKING_URI = (
    "http://127.0.0.1:5000"
)

PRODUCTION_URI = (
    f"models:/{MODEL_NAME}@production"
)


class ProductionArtifactSync:

    def __init__(self):

        self.production_root = (
            PRODUCTION_ROOT
        )

        self.production_root.mkdir(
            parents=True,
            exist_ok=True
        )

    def sync(self):

        mlflow.set_tracking_uri(
            MLFLOW_TRACKING_URI
        )

        production_identity = (
            self._get_production_identity()
        )

        current_manifest = (
            self._get_current_manifest()
        )

        if current_manifest:

            same_version = (
                str(
                    current_manifest.get(
                        "model_version"
                    )
                )
                == production_identity[
                    "model_version"
                ]
            )

            same_run = (
                current_manifest.get("run_id")
                == production_identity["run_id"]
            )

            current_model_id = (
                current_manifest.get("model_id")
            )

            production_model_id = (
                production_identity["model_id"]
            )

            same_model_id = (
                not current_model_id
                or not production_model_id
                or current_model_id
                == production_model_id
            )

            if (
                same_version
                and same_run
                and same_model_id
            ):

                return current_manifest

        artifact_root = Path(
            mlflow.artifacts.download_artifacts(
                artifact_uri=PRODUCTION_URI
            )
        )

        mlmodel_path = (
            artifact_root
            / "MLmodel"
        )

        if not mlmodel_path.exists():

            raise FileNotFoundError(
                f"MLmodel not found: {mlmodel_path}"
            )

        with open(
            mlmodel_path,
            "r",
            encoding="utf-8"
        ) as file:

            metadata = yaml.safe_load(
                file
            )

        model_type = (
            self._resolve_model_type(
                metadata
            )
        )

        model_version = (
            self._read_model_version(
                artifact_root
            )
        )

        run_id = metadata.get(
            "run_id"
        )

        model_id = metadata.get(
            "model_id"
        )

        if not model_version:

            raise ValueError(
                "Production model version "
                "not found."
            )

        target_root = (
            self.production_root
            / MODEL_NAME
            / f"version_{model_version}"
        )

        target_parent = (
            target_root.parent
        )

        target_parent.mkdir(
            parents=True,
            exist_ok=True
        )

        existing_mlmodel = (
            target_root
            / "MLmodel"
        )

        if target_root.exists():

            if existing_mlmodel.exists():

                if current_manifest:

                    if (
                        str(
                            current_manifest.get(
                                "model_version"
                            )
                        )
                        == str(model_version)
                        and current_manifest.get(
                            "run_id"
                        )
                        == run_id
                        and current_manifest.get(
                            "model_id"
                        )
                        == model_id
                    ):

                        return current_manifest

                raise RuntimeError(
                    f"Production version {model_version} "
                    "already exists with different metadata."
                )

            shutil.copytree(
                artifact_root,
                target_root,
                dirs_exist_ok=True
            )

        else:

            shutil.copytree(
                artifact_root,
                target_root
            )

        manifest = {

            "model_name": MODEL_NAME,

            "model_version": int(
                model_version
            ),

            "model_type": model_type,

            "run_id": run_id,

            "model_id": model_id,

            "artifact_path": (
                target_root
                .relative_to(ROOT)
                .as_posix()
            ),

            "synced_at": datetime.now(
                timezone.utc
            ).isoformat()

        }

        manifest_tmp = (
            CURRENT_MANIFEST_PATH
            .with_suffix(".tmp")
        )

        with open(
            manifest_tmp,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                manifest,
                file,
                indent=2
            )

        manifest_tmp.replace(
            CURRENT_MANIFEST_PATH
        )

        return manifest

    def _get_production_identity(self):

        client = MlflowClient(
            tracking_uri=MLFLOW_TRACKING_URI
        )

        production_version = (
            client.get_model_version_by_alias(
                MODEL_NAME,
                "production"
            )
        )

        return {

            "model_version": str(
                production_version.version
            ),

            "run_id": (
                production_version.run_id
            ),

            "model_id": getattr(
                production_version,
                "model_id",
                None
            )

        }

    def _get_current_manifest(self):

        if not CURRENT_MANIFEST_PATH.exists():

            return None

        with open(
            CURRENT_MANIFEST_PATH,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)

    @staticmethod
    def _resolve_model_type(
        metadata
    ):

        flavors = metadata.get(
            "flavors",
            {}
        )

        if "xgboost" in flavors:

            return "xgboost"

        if "lightgbm" in flavors:

            return "lightgbm"

        raise ValueError(
            "Unsupported production model "
            f"flavors: {list(flavors.keys())}"
        )

    @staticmethod
    def _read_model_version(
        artifact_root
    ):

        metadata_path = (
            artifact_root
            / "registered_model_meta"
        )

        if not metadata_path.exists():

            raise FileNotFoundError(
                "registered_model_meta not found."
            )

        metadata = yaml.safe_load(
            metadata_path.read_text(
                encoding="utf-8"
            )
        )

        if not isinstance(metadata, dict):

            raise ValueError(
                "Invalid registered_model_meta format."
            )

        return metadata.get(
            "model_version"
        )


if __name__ == "__main__":

    result = (
        ProductionArtifactSync()
        .sync()
    )

    print(
        json.dumps(
            result,
            indent=2
        )
    )
