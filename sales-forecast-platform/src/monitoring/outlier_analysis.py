import os
import pandas as pd
import numpy as np


def analyze_prediction_outliers(
    y_true,
    y_pred,
    original_df,
    top_n=50,
    save_dir="artifacts/outliers"
):
    """
    Analyze largest prediction errors.

    Parameters
    ----------
    y_true : actual target values
    y_pred : predicted values
    original_df : matching dataframe rows
    top_n : number of worst predictions
    save_dir : where to save report
    """

    os.makedirs(save_dir, exist_ok=True)

    results_df = original_df.copy().reset_index(drop=True)

    results_df["actual"] = y_true.values
    results_df["predicted"] = y_pred
    results_df["residual"] = (
        results_df["actual"]
        - results_df["predicted"]
    )

    results_df["absolute_error"] = (
        results_df["residual"].abs()
    )

    results_df["percentage_error"] = np.where(
        results_df["actual"] != 0,
        (
            results_df["absolute_error"]
            / results_df["actual"].abs()
        ) * 100,
        0
    )

    # Worst predictions
    worst_cases = (
        results_df
        .sort_values(
            "absolute_error",
            ascending=False
        )
        .head(top_n)
    )

    output_path = (
        f"{save_dir}/worst_predictions.csv"
    )

    worst_cases.to_csv(
        output_path,
        index=False
    )

    print("\n===== OUTLIER ANALYSIS =====")
    print(
        worst_cases[
            [
                "Store",
                "Dept",
                "Date",
                "actual",
                "predicted",
                "absolute_error"
            ]
        ].head(10)
    )

    print(
        f"\nSaved outlier report to:"
        f" {output_path}"
    )

    return {
        "worst_cases": worst_cases,
        "report_path": output_path
    }
