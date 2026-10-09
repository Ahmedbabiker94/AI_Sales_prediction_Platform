class ProductionModelSyncJob:

    def run(self):

        from src.deployment.production_artifact_sync import (
            ProductionArtifactSync
        )

        result = (
            ProductionArtifactSync()
            .sync()
        )

        print(
            "Production model sync completed:"
        )

        print(
            result
        )

        return result