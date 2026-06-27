import numpy as np


class HistoryFeatureCalculator:

    @staticmethod
    def calculate(sales):

        if len(sales) < 52:

            sales = (
                sales +
                [0.0] * (52 - len(sales))
            )

        return {

            "lag_1": sales[0],

            "lag_2": sales[1],

            "lag_4": sales[3],

            "lag_52": sales[51],

            "rolling_mean_4":
                float(
                    np.mean(
                        sales[:4]
                    )
                ),

            "rolling_mean_12":
                float(
                    np.mean(
                        sales[:12]
                    )
                ),

            "rolling_std_4":
                float(
                    np.std(
                        sales[:4]
                    )
                ),

            "sales_trend":
                float(
                    sales[0] -
                    sales[3]
                )
        }