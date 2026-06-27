from collections import defaultdict


class ModelMonitor:

    def __init__(self):

        self.model_usage = defaultdict(int)

    def record_prediction(
        self,
        model_version
    ):

        self.model_usage[
            model_version
        ] += 1

    def get_stats(self):

        return dict(
            self.model_usage
        )


model_monitor = ModelMonitor()