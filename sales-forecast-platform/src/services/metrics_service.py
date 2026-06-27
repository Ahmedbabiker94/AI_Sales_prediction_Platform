from collections import defaultdict


class MetricsService:

    def __init__(self):

        self.counters = defaultdict(int)

        self.timers = defaultdict(list)

    def increment(
        self,
        metric_name
    ):

        self.counters[
            metric_name
        ] += 1

    def record_time(
        self,
        metric_name,
        seconds
    ):

        self.timers[
            metric_name
        ].append(seconds)

    def get_metrics(self):

        result = {}

        for key, value in self.counters.items():

            result[key] = value

        for key, values in self.timers.items():

            if values:

                result[
                    f"{key}_avg"
                ] = sum(values) / len(values)

                result[
                    f"{key}_max"
                ] = max(values)

        return result