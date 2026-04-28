import sqlite3
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "database" / "predictions.db"


def load_forecast_logs():
    if not DB_PATH.exists():
        return pd.DataFrame()

    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT * FROM forecast_logs ORDER BY created_at DESC",
        conn
    )
    conn.close()

    if not df.empty:
        df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
        df["forecast_date"] = pd.to_datetime(df["forecast_date"], errors="coerce")

    return df


def generate_forecast_insights(store: int | None = None, dept: int | None = None):
    df = load_forecast_logs()

    if df.empty:
        return {
            "summary": "No forecast logs available yet.",
            "insights": [],
            "stats": {}
        }

    if store is not None:
        df = df[df["store"] == store]

    if dept is not None:
        df = df[df["dept"] == dept]

    if df.empty:
        return {
            "summary": "No forecast logs found for the selected store/department.",
            "insights": [],
            "stats": {}
        }

    df = df.sort_values("forecast_date").reset_index(drop=True)

    latest_created_at = df["created_at"].max()
    latest_batch = df[df["created_at"] == latest_created_at].copy()

    if latest_batch.empty:
        return {
            "summary": "No recent forecast batch found.",
            "insights": [],
            "stats": {}
        }

    latest_batch = latest_batch.sort_values("forecast_date").reset_index(drop=True)

    avg_forecast = float(latest_batch["predicted_units"].mean())
    max_row = latest_batch.loc[latest_batch["predicted_units"].idxmax()]
    min_row = latest_batch.loc[latest_batch["predicted_units"].idxmin()]
    total_forecast = float(latest_batch["predicted_units"].sum())

    trend = "stable"
    if len(latest_batch) >= 2:
        first_val = float(latest_batch["predicted_units"].iloc[0])
        last_val = float(latest_batch["predicted_units"].iloc[-1])

        if last_val > first_val * 1.03:
            trend = "upward"
        elif last_val < first_val * 0.97:
            trend = "downward"

    insights = [
        f"Average forecasted units: {avg_forecast:,.2f}",
        f"Highest forecast: {max_row['predicted_units']:,.2f} on {max_row['forecast_date'].date()}",
        f"Lowest forecast: {min_row['predicted_units']:,.2f} on {min_row['forecast_date'].date()}",
        f"Total forecast across the latest batch: {total_forecast:,.2f}",
        f"Trend across the latest batch appears {trend}.",
    ]

    if trend == "upward":
        insights.append("Expected demand is increasing across the forecast horizon.")
    elif trend == "downward":
        insights.append("Expected demand is softening across the forecast horizon.")
    else:
        insights.append("Expected demand is relatively stable across the forecast horizon.")

    summary = (
        f"Latest forecast batch contains {len(latest_batch)} records. "
        f"Average predicted units are {avg_forecast:,.2f}, "
        f"with a {trend} trend."
    )

    stats = {
        "records": int(len(latest_batch)),
        "average_forecast": avg_forecast,
        "total_forecast": total_forecast,
        "trend": trend,
    }

    return {
        "summary": summary,
        "insights": insights,
        "stats": stats,
        "data": latest_batch
    }