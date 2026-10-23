"""Compatibility command, safe to import or collect; never an automatic test.

Invoke explicitly with ``python -m src.training.test_training_pipeline PATH``.
No dataset is selected implicitly, and no registration/promotion is performed.
"""


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Explicit legacy training only")
    parser.add_argument("data_path", help="CSV to train on (explicit invocation only)")
    parser.add_argument("--model-type", choices=("xgboost",), default="xgboost")
    parser.add_argument("--outlier-report", help="Opt-in CSV path; parent must already exist")
    parser.add_argument("--overwrite-outlier-report", action="store_true",
                        help="Explicitly permit replacing an existing regular report file")
    args = parser.parse_args(argv)
    if args.overwrite_outlier_report and args.outlier_report is None:
        parser.error("--overwrite-outlier-report requires --outlier-report")

    from src.training.train_pipeline import run_training_pipeline

    options = {}
    if args.outlier_report is not None:
        from src.monitoring.outlier_analysis import OutlierReportOptions
        options['outlier_report'] = OutlierReportOptions(
            args.outlier_report, overwrite=args.overwrite_outlier_report)
    return run_training_pipeline(args.data_path, model_type=args.model_type, **options)


if __name__ == "__main__":
    main()
