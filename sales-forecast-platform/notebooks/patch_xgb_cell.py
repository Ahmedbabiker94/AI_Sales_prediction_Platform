"""
patch_xgb_cell.py
-----------------
Patches the XGBoost_final cell in the notebook so that:
  1. model logging uses mlflow.sklearn.log_model (works with XGBRegressor)
  2. artifact_path is correctly set to "model"
  3. debug prints + try/except are added around log_model
  4. the cell is self-contained and idempotent

Run from the project root:
    python notebooks/patch_xgb_cell.py
"""

import json, re, pathlib, sys

NB_PATH = pathlib.Path(__file__).parent / "AutoML_to_predict_future_sales.ipynb"

# ── The replacement cell source ──────────────────────────────────────────────
NEW_SOURCE = [
    "#train Final XGBoost + Log to MLflow\n",
    "\n",
    "xgb_best = xgb_study.best_params\n",
    "xgb_best.update({\n",
    "    \"random_state\" : SEED,\n",
    "    \"tree_method\"  : \"hist\",\n",
    "    \"device\"       : \"cuda\" if torch.cuda.is_available() else \"cpu\",\n",
    "    \"verbosity\"    : 0,\n",
    "})\n",
    "\n",
    "with mlflow.start_run(run_name=\"XGBoost_final\"):\n",
    "\n",
    "    # ── Training ────────────────────────────────────────────────────────\n",
    "    xgb_model = xgb.XGBRegressor(**xgb_best, early_stopping_rounds=50)\n",
    "    xgb_model.fit(\n",
    "        X_train, y_train,\n",
    "        eval_set=[(X_test, y_test)],\n",
    "        verbose=False\n",
    "    )\n",
    "\n",
    "    xgb_preds   = xgb_model.predict(X_test)\n",
    "    xgb_metrics = compute_metrics(y_test.values, xgb_preds, AVG_SALES)\n",
    "    print_metrics(\"XGBoost\", xgb_metrics)\n",
    "\n",
    "    # ── MLflow params / metrics ─────────────────────────────────────────\n",
    "    mlflow.log_param(\"model_type\", \"xgboost\")\n",
    "    mlflow.log_params(xgb_best)\n",
    "    mlflow.log_metrics(xgb_metrics)\n",
    "\n",
    "    # ── DEBUG: confirm the run is still active ───────────────────────────\n",
    "    _active = mlflow.active_run()\n",
    "    print(f\"[DEBUG] active_run before log_model : {_active}\")\n",
    "    print(f\"[DEBUG] run_id                      : {_active.info.run_id if _active else 'NO ACTIVE RUN'}\")\n",
    "\n",
    "    # ── Log model (sklearn flavor works for XGBRegressor) ───────────────\n",
    "    print(\"[DEBUG] >>> calling mlflow.sklearn.log_model ...\")\n",
    "    try:\n",
    "        import mlflow.sklearn\n",
    "        mlflow.sklearn.log_model(xgb_model, artifact_path=\"model\")\n",
    "        print(\"[DEBUG] >>> mlflow.sklearn.log_model SUCCEEDED\")\n",
    "    except Exception as _e:\n",
    "        import traceback\n",
    "        print(\"[DEBUG] >>> mlflow.sklearn.log_model FAILED:\")\n",
    "        traceback.print_exc()\n",
    "\n",
    "    # ── Feature importance plot ──────────────────────────────────────────\n",
    "    fig, ax = plt.subplots(figsize=(10, 8))\n",
    "    importances = pd.Series(\n",
    "        xgb_model.feature_importances_, index=FEATURES\n",
    "    ).sort_values(ascending=False)[:20]\n",
    "    importances.plot(kind=\"barh\", ax=ax)\n",
    "    ax.set_title(\"XGBoost — Top 20 Feature Importances\")\n",
    "    ax.invert_yaxis()\n",
    "    plt.tight_layout()\n",
    "    mlflow.log_figure(fig, \"xgb_feature_importance.png\")\n",
    "    plt.show()\n",
    "\n",
    "    # ── Save config artifact ─────────────────────────────────────────────\n",
    "    joblib.dump({\"features\": FEATURES, \"model_type\": \"xgboost\"}, \"xgb_config.pkl\")\n",
    "    mlflow.log_artifact(\"xgb_config.pkl\")\n",
    "\n",
    "    XGB_RUN_ID = mlflow.active_run().info.run_id\n",
    "    print(f\"MLflow Run ID: {XGB_RUN_ID}\")",
]

# ── Load notebook ────────────────────────────────────────────────────────────
with NB_PATH.open(encoding="utf-8") as f:
    nb = json.load(f)

# ── Find the target cell ─────────────────────────────────────────────────────
TARGET_MARKER = 'mlflow.start_run(run_name="XGBoost_final")'

patched = 0
for cell in nb["cells"]:
    src = "".join(cell.get("source", []))
    if TARGET_MARKER in src:
        cell["source"] = NEW_SOURCE
        cell["outputs"] = []
        cell["execution_count"] = None
        patched += 1
        break

if patched == 0:
    print("ERROR: target cell not found — check the marker string.", file=sys.stderr)
    sys.exit(1)

# ── Write back ───────────────────────────────────────────────────────────────
with NB_PATH.open("w", encoding="utf-8") as f:
    json.dump(nb, f, indent=1, ensure_ascii=False)

print(f"✅  Patched {patched} cell(s) in {NB_PATH}")
print("   Re-open the notebook in Jupyter and run the XGBoost_final cell.")
