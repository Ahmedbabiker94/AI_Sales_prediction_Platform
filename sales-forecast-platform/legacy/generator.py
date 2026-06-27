"""
generator.py
------------
Report Generator Service.
Generates HTML + Markdown reports with:
  - Model performance metrics from the latest MLflow run
  - Prediction vs actual charts
  - Data drift summary (PSI-based)

Usage:
    python report_service/generator.py --data data/sales.csv --out reports/
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from mlflow.tracking import MlflowClient
import mlflow.sklearn

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from features import (
    load_raw, add_calendar_features, add_lag_features,
    add_rolling_features, encode_categoricals,
    train_test_split_temporal, FEATURE_COLS, TARGET_COL,
)

# ────────────────────────────────────────────────────────────────────────────
# Drift: Population Stability Index
# ────────────────────────────────────────────────────────────────────────────

def compute_psi(expected: np.ndarray, actual: np.ndarray, bins: int = 10) -> float:
    """Population Stability Index — PSI > 0.2 = significant drift."""
    eps = 1e-8
    breakpoints = np.percentile(expected, np.linspace(0, 100, bins + 1))
    breakpoints[0] = -np.inf
    breakpoints[-1] = np.inf
    exp_counts = np.histogram(expected, bins=breakpoints)[0]
    act_counts = np.histogram(actual, bins=breakpoints)[0]
    exp_pct = (exp_counts + eps) / (len(expected) + eps)
    act_pct = (act_counts + eps) / (len(actual) + eps)
    psi = np.sum((act_pct - exp_pct) * np.log(act_pct / exp_pct))
    return float(psi)


# ────────────────────────────────────────────────────────────────────────────
# Chart helpers
# ────────────────────────────────────────────────────────────────────────────

STYLE = {
    "figure.facecolor": "#0f172a",
    "axes.facecolor": "#1e293b",
    "axes.edgecolor": "#334155",
    "axes.labelcolor": "#94a3b8",
    "xtick.color": "#94a3b8",
    "ytick.color": "#94a3b8",
    "grid.color": "#334155",
    "text.color": "#f1f5f9",
    "lines.linewidth": 1.8,
}


def _apply_style():
    plt.rcParams.update(STYLE)


def plot_forecast(df: pd.DataFrame, out_dir: Path) -> str:
    _apply_style()
    # Aggregate by date for readability
    agg = df.groupby("date")[["units_sold", "predicted_units"]].sum().reset_index()
    agg = agg.tail(90)  # last 90 days

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(agg["date"], agg["units_sold"], label="Actual", color="#38bdf8")
    ax.plot(agg["date"], agg["predicted_units"], label="Predicted", color="#f472b6", linestyle="--")
    ax.set_title("Actual vs Predicted Units (All Stores, Last 90 Days)", fontsize=14, pad=15)
    ax.set_xlabel("Date")
    ax.set_ylabel("Units Sold")
    ax.legend(facecolor="#1e293b", edgecolor="#334155")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
    plt.xticks(rotation=30)
    plt.tight_layout()
    path = out_dir / "forecast_chart.png"
    fig.savefig(path, dpi=140, bbox_inches="tight")
    plt.close()
    return str(path)


def plot_error_distribution(df: pd.DataFrame, out_dir: Path) -> str:
    _apply_style()
    errors = df["units_sold"] - df["predicted_units"]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(errors, bins=60, color="#818cf8", edgecolor="#0f172a", alpha=0.85)
    ax.axvline(0, color="#f472b6", linestyle="--", linewidth=1.5)
    ax.set_title("Prediction Error Distribution", fontsize=13, pad=12)
    ax.set_xlabel("Error (Actual − Predicted)")
    ax.set_ylabel("Frequency")
    plt.tight_layout()
    path = out_dir / "error_dist.png"
    fig.savefig(path, dpi=140, bbox_inches="tight")
    plt.close()
    return str(path)


# ────────────────────────────────────────────────────────────────────────────
# Core report logic
# ────────────────────────────────────────────────────────────────────────────

def load_predictions(data_path: str, test_days: int = 90):
    mlflow.set_tracking_uri(str(ROOT / "mlruns"))
    model = mlflow.sklearn.load_model("models:/sales_forecast_lgbm/Production")

    df_raw = load_raw(data_path)
    _, test_raw = train_test_split_temporal(df_raw, test_days=test_days)
    test_raw = add_calendar_features(test_raw)
    test_raw = add_lag_features(test_raw)
    test_raw = add_rolling_features(test_raw)
    test_raw = encode_categoricals(test_raw)
    test_raw = test_raw.dropna(subset=FEATURE_COLS).reset_index(drop=True)
    test_raw["predicted_units"] = model.predict(test_raw[FEATURE_COLS])
    return test_raw


def compute_summary_metrics(df: pd.DataFrame) -> dict:
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
    mae = mean_absolute_error(df[TARGET_COL], df["predicted_units"])
    rmse = np.sqrt(mean_squared_error(df[TARGET_COL], df["predicted_units"]))
    r2 = r2_score(df[TARGET_COL], df["predicted_units"])
    mape = np.mean(np.abs((df[TARGET_COL] - df["predicted_units"]) / np.clip(df[TARGET_COL], 1, None))) * 100
    return {"mae": round(mae, 3), "rmse": round(rmse, 3), "r2": round(r2, 4), "mape": round(mape, 2)}


def compute_drift(df: pd.DataFrame) -> dict:
    mid = len(df) // 2
    baseline = df.iloc[:mid][TARGET_COL].values
    current = df.iloc[mid:][TARGET_COL].values
    psi = compute_psi(baseline, current)
    drift_level = "🟢 Stable" if psi < 0.1 else ("🟡 Moderate" if psi < 0.2 else "🔴 High Drift")
    return {"psi": round(psi, 4), "drift_level": drift_level}


def get_mlflow_run_info() -> dict:
    try:
        client = MlflowClient()
        experiment = client.get_experiment_by_name("sales_forecast")
        if experiment is None:
            return {}
        runs = client.search_runs([experiment.experiment_id], order_by=["metrics.rmse ASC"], max_results=1)
        if not runs:
            return {}
        r = runs[0]
        return {
            "run_id": r.info.run_id,
            "rmse": round(r.data.metrics.get("rmse", 0), 4),
            "mae": round(r.data.metrics.get("mae", 0), 4),
            "r2": round(r.data.metrics.get("r2", 0), 4),
            "mape": round(r.data.metrics.get("mape", 0), 2),
        }
    except Exception:
        return {}


# ────────────────────────────────────────────────────────────────────────────
# HTML report
# ────────────────────────────────────────────────────────────────────────────

def generate_html_report(metrics: dict, drift: dict, mlflow_info: dict,
                          forecast_img: str, error_img: str, out_dir: Path) -> Path:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Sales Forecast Report — {now}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 40px; }}
  h1 {{ color: #38bdf8; font-size: 2rem; margin-bottom: 6px; }}
  .subtitle {{ color: #64748b; margin-bottom: 40px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 20px; margin-bottom: 40px; }}
  .card {{ background: #1e293b; border-radius: 12px; padding: 24px; border: 1px solid #334155; }}
  .card .label {{ font-size: 0.8rem; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; }}
  .card .value {{ font-size: 2rem; font-weight: 700; color: #38bdf8; margin-top: 8px; }}
  .card .unit {{ font-size: 0.75rem; color: #475569; margin-top: 4px; }}
  .section {{ margin-bottom: 40px; }}
  .section h2 {{ color: #94a3b8; font-size: 1.1rem; border-bottom: 1px solid #334155; padding-bottom: 8px; margin-bottom: 20px; }}
  img {{ width: 100%; border-radius: 12px; border: 1px solid #334155; }}
  .drift-badge {{ display: inline-block; background: #1e293b; border: 1px solid #334155; border-radius: 20px; padding: 6px 16px; font-size: 0.9rem; }}
  table {{ width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 12px; overflow: hidden; }}
  th {{ background: #0f172a; padding: 12px 16px; text-align: left; color: #64748b; font-size: 0.8rem; text-transform: uppercase; }}
  td {{ padding: 12px 16px; border-top: 1px solid #334155; }}
  .footer {{ margin-top: 60px; color: #334155; font-size: 0.8rem; text-align: center; }}
</style>
</head>
<body>
<h1>📊 Sales Forecast Report</h1>
<p class="subtitle">Generated: {now}</p>

<div class="section">
  <h2>Model Performance</h2>
  <div class="grid">
    <div class="card"><div class="label">MAE</div><div class="value">{metrics['mae']}</div><div class="unit">units</div></div>
    <div class="card"><div class="label">RMSE</div><div class="value">{metrics['rmse']}</div><div class="unit">units</div></div>
    <div class="card"><div class="label">R²</div><div class="value">{metrics['r2']}</div><div class="unit">score</div></div>
    <div class="card"><div class="label">MAPE</div><div class="value">{metrics['mape']}%</div><div class="unit">error</div></div>
  </div>
</div>

<div class="section">
  <h2>Data Drift</h2>
  <p>PSI: <strong>{drift['psi']}</strong> — <span class="drift-badge">{drift['drift_level']}</span></p>
  <p style="color:#475569; font-size:0.85rem; margin-top:8px;">PSI &lt; 0.1: stable | 0.1–0.2: monitor | &gt; 0.2: retrain suggested</p>
</div>

<div class="section">
  <h2>Forecast Chart</h2>
  <img src="{Path(forecast_img).name}" alt="Forecast">
</div>

<div class="section">
  <h2>Error Distribution</h2>
  <img src="{Path(error_img).name}" alt="Error Distribution">
</div>

<div class="section">
  <h2>Latest MLflow Run</h2>
  <table>
    <tr><th>Field</th><th>Value</th></tr>
    {"".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in mlflow_info.items())}
  </table>
</div>

<div class="footer">Sales Forecast Platform — Auto-generated report</div>
</body>
</html>"""
    out_path = out_dir / "report.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


# ────────────────────────────────────────────────────────────────────────────
# Entry-point
# ────────────────────────────────────────────────────────────────────────────

def generate(data_path: str = "data/sales.csv", out_dir: str = "reports") -> str:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    print("Loading predictions…")
    df = load_predictions(data_path)

    print("Computing metrics…")
    metrics = compute_summary_metrics(df)
    drift = compute_drift(df)
    mlflow_info = get_mlflow_run_info()

    print("Generating charts…")
    forecast_img = plot_forecast(df, out)
    error_img = plot_error_distribution(df, out)

    print("Writing HTML report…")
    report_path = generate_html_report(metrics, drift, mlflow_info, forecast_img, error_img, out)

    print(f"✅ Report saved → {report_path}")
    return str(report_path)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data/sales.csv")
    p.add_argument("--out", default="reports")
    args = p.parse_args()
    generate(args.data, args.out)
