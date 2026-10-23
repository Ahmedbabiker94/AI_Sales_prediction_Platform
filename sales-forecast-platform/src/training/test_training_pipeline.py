"""Compatibility command, safe to import or collect; never an automatic test.

Invoke explicitly with ``python -m src.training.test_training_pipeline PATH``.
No dataset is selected implicitly, and no registration/promotion is performed.
"""


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Explicit legacy training only")
    parser.add_argument("data_path", help="CSV to train on (explicit invocation only)")
    parser.add_argument("--model-type", choices=("xgboost",), default="xgboost")
    args = parser.parse_args(argv)

    from src.training.train_pipeline import run_training_pipeline

    return run_training_pipeline(args.data_path, model_type=args.model_type)


if __name__ == "__main__":
    main()
