"""
app.py
------
Streamlit dashboard for the Sales Forecast Platform.

Displays:
  1. API health
  2. Live single prediction via FastAPI
  3. Logged predictions from SQLite
  4. Historical Walmart dataset overview
  5. MLflow experiment runs

Run:
    streamlit run dashboard/app.py
"""
import os
import sys

import sqlite3
from pathlib import Path
from datetime import date

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
import mlflow
from mlflow.tracking import MlflowClient
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT ))

from src.insights import generate_forecast_insights
from src.reporting import generate_forecast_report
from src.pdf_report import save_report_as_pdf



API_URL = os.getenv("API_URL", "http://144.24.223.13:8000")
DB_PATH = ROOT / "database" / "predictions.db"
SALES_CSV = ROOT / "data" / "walmart_cleaned.csv"

st.set_page_config(
    page_title="Sales Forecast Platform",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)


st.markdown("""
<style>
    .main { background-color: #0f172a; }
    .stApp { background-color: #0f172a; }
    div[data-testid="metric-container"] {
        background: #1e293b;
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 16px;
    }
    div[data-testid="metric-container"] label { color: #64748b !important; }
    div[data-testid="metric-container"] div[data-testid="stMetricValue"] { color: #38bdf8 !important; }
    h1, h2, h3 { color: #f1f5f9 !important; }
</style>
""", unsafe_allow_html=True)


# =========================
# Helpers
# =========================

#helper for single forcast 
@st.cache_data(ttl=1)
def call_predict_api(api_url: str, payload: dict):
    try:
        resp = requests.post(f"{api_url}/predict", json=payload, timeout=15)
        if resp.status_code == 200:
            return resp.json(), None
        return None, f"API error {resp.status_code}: {resp.text}"
    except Exception as e:
        return None, str(e)

@st.cache_data(ttl=60)
def load_sales_data():
    if SALES_CSV.exists():
        df = pd.read_csv(SALES_CSV)
        if "Date" in df.columns:
            df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        return df
    return None


@st.cache_data(ttl=30)
def load_logged_predictions():
    if not DB_PATH.exists():
        return pd.DataFrame()

    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM predictions ORDER BY id DESC", conn)
    conn.close()

    if not df.empty:
        if "created_at" in df.columns:
            df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
        if "input_date" in df.columns:
            df["input_date"] = pd.to_datetime(df["input_date"], errors="coerce")
    return df


@st.cache_data(ttl=15)
def get_api_health(api_url: str):
    try:
        resp = requests.get(f"{api_url}/health", timeout=3)
        if resp.status_code == 200:
            return resp.json()
        return {"status": "error", "model_loaded": False, "model_version": "unknown"}
    except Exception:
        return {"status": "down", "model_loaded": False, "model_version": "unknown"}


@st.cache_data(ttl=60)
def load_mlflow_runs():
    try:
        tracking_uri = (ROOT / "mlruns").resolve().as_uri()
        mlflow.set_tracking_uri(tracking_uri)

        client = MlflowClient()
        experiment = client.get_experiment_by_name("walmart_sales_forecast")

        if not experiment:
            return pd.DataFrame()

        runs = client.search_runs(
            experiment_ids=[experiment.experiment_id],
            order_by=["start_time DESC"],
            max_results=20
        )

        rows = []
        for r in runs:
            rows.append({
                "run_id": r.info.run_id[:8] + "...",
                "status": r.info.status,
                "model_type": r.data.params.get("model_type", "—"),
                "mae": round(r.data.metrics.get("mae", 0), 3) if "mae" in r.data.metrics else None,
                "rmse": round(r.data.metrics.get("rmse", 0), 3) if "rmse" in r.data.metrics else None,
                "error_pct": round(r.data.metrics.get("error_pct", 0), 3) if "error_pct" in r.data.metrics else None,
            })

        return pd.DataFrame(rows)
    except Exception as e:
        st.error(f"Could not load MLflow runs: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=30)
def load_actual_vs_predicted():
    if not DB_PATH.exists() or not SALES_CSV.exists():
        return pd.DataFrame()

    # logged predictions from SQLite
    conn = sqlite3.connect(DB_PATH)
    preds_df = pd.read_sql_query("SELECT * FROM predictions", conn)
    conn.close()

    if preds_df.empty:
        return pd.DataFrame()

    sales_df = pd.read_csv(SALES_CSV)

    # normalize dates
    preds_df["input_date"] = pd.to_datetime(preds_df["input_date"], errors="coerce")
    sales_df["Date"] = pd.to_datetime(sales_df["Date"], errors="coerce")

    # normalize key column names/types
    preds_df["store"] = preds_df["store"].astype("Int64")
    preds_df["dept"] = preds_df["dept"].astype("Int64")
    sales_df["Store"] = sales_df["Store"].astype("Int64")
    sales_df["Dept"] = sales_df["Dept"].astype("Int64")

    # aggregate actual sales by Store, Dept, Date
    actual_df = (
        sales_df.groupby(["Store", "Dept", "Date"], as_index=False)["Weekly_Sales"]
        .sum()
    )

    merged = preds_df.merge(
        actual_df,
        left_on=["store", "dept", "input_date"],
        right_on=["Store", "Dept", "Date"],
        how="inner"
    )

    if merged.empty:
        return pd.DataFrame()

    merged = merged.rename(columns={
        "predicted_units": "Predicted",
        "Weekly_Sales": "Actual",
        "store": "Store_logged",
        "dept": "Dept_logged"
    })

    return merged

def compute_eval_metrics(df: pd.DataFrame):
    if df.empty:
        return None

    actual = df["Actual"]
    pred = df["Predicted"]

    mae = (actual - pred).abs().mean()
    rmse = (((actual - pred) ** 2).mean()) ** 0.5
    mape = (((actual - pred).abs() / actual.clip(lower=1)) * 100).mean()

    return {
        "mae": float(mae),
        "rmse": float(rmse),
        "mape": float(mape),
    }

@st.cache_data(ttl=5)
def call_forecast_api(api_url: str, store: int, dept: int, periods: int):
    try:
        resp = requests.post(
            f"{api_url}/forecast-next-7-days",
            json={
                "Store": store,
                "Dept": dept,
                "periods": periods
            },
            timeout=15
        )
        if resp.status_code == 200:
            return resp.json(), None
        return None, f"API error {resp.status_code}: {resp.text}"
    except Exception as e:
        return None, str(e)    

@st.cache_data(ttl=5)
def call_next_week_forecast(
    api_url: str,
    store: int,
    dept: int,
    store_type: str,
    size: int,
    temperature: float,
    fuel_price: float,
    cpi: float,
    unemployment: float,
    is_holiday: bool
):
    try:
        resp = requests.post(
            f"{api_url}/forecast-next-week",
            json={
                "Store": store,
                "Dept": dept,
                "Type": store_type,
                "Size": size,
                "Temperature": temperature,
                "Fuel_Price": fuel_price,
                "CPI": cpi,
                "Unemployment": unemployment,
                "IsHoliday": is_holiday
            },
            timeout=15
        )

        if resp.status_code == 200:
            return resp.json(), None

        return None, f"API error {resp.status_code}: {resp.text}"

    except Exception as e:
        return None, str(e)

@st.cache_data(ttl=5)
def call_next_4_weeks_forecast(
    api_url: str,
    store: int,
    dept: int,
    store_type: str,
    size: int,
    temperature: float,
    fuel_price: float,
    cpi: float,
    unemployment: float,
    is_holiday: bool,
    weeks: int = 4
):
    try:
        resp = requests.post(
            f"{api_url}/forecast-next-4-weeks",
            json={
                "Store": store,
                "Dept": dept,
                "Type": store_type,
                "Size": size,
                "Temperature": temperature,
                "Fuel_Price": fuel_price,
                "CPI": cpi,
                "Unemployment": unemployment,
                "IsHoliday": is_holiday,
                "weeks": weeks
            },
            timeout=60
        )

        if resp.status_code == 200:
            return resp.json(), None

        return None, f"API error {resp.status_code}: {resp.text}"

    except Exception as e:
        return None, str(e)

# =========================
# Sidebar
# =========================
with st.sidebar:
    st.markdown("## ⚙️ Controls")
    api_url = st.text_input("API Base URL", value=API_URL)

    # -------------------------
    # Single Prediction
    # -------------------------
    st.divider()
    st.markdown("### 🔮 Single Prediction")

    pred_date = st.date_input("Date", value=date.today(), key="pred_date")
    pred_store = st.number_input("Store", min_value=1, max_value=45, value=1, key="pred_store")
    pred_dept = st.number_input("Dept", min_value=1, max_value=99, value=1, key="pred_dept")
    pred_type = st.selectbox("Store Type", ["A", "B", "C"], key="pred_type")
    pred_size = st.number_input("Store Size", value=150000, key="pred_size")
    pred_temp = st.number_input("Temperature", value=60.0, key="pred_temp")
    pred_fuel = st.number_input("Fuel Price", value=3.5, key="pred_fuel")
    pred_cpi = st.number_input("CPI", value=215.0, key="pred_cpi")
    pred_unemp = st.number_input("Unemployment", value=7.5, key="pred_unemp")
    pred_holiday = st.toggle("Is Holiday?", value=False, key="pred_holiday")


    run_single_predict = st.button("Predict", use_container_width=True, type="primary")

    # -------------------------
    # Weekly / Monthly Forecast
    # -------------------------
    st.divider()
    st.markdown("### 📅 Weekly / Monthly Forecast")

    forecast_store = st.number_input("Forecast Store", min_value=1, max_value=45, value=1, key="fc_store")
    forecast_dept = st.number_input("Forecast Dept", min_value=1, max_value=99, value=1, key="fc_dept")
    
    forecast_type = st.selectbox(
        "Forecast Store Type",
        ["A", "B", "C"],
        key="fc_type"
    )
    
    forecast_size = st.number_input(
        "Forecast Store Size",
        value=150000,
        key="fc_size"
    )
    
    forecast_temp = st.number_input(
        "Forecast Temperature",
        value=25.0,
        key="fc_temp"
    )
    
    forecast_fuel = st.number_input(
        "Forecast Fuel Price",
        value=3.5,
        key="fc_fuel"
    )
    
    forecast_cpi = st.number_input(
        "Forecast CPI",
        value=220.0,
        key="fc_cpi"
    )

    forecast_unemployment = st.number_input(
        "Forecast Unemployment",
        value=7.0,
        key="fc_unemployment"
    )

    forecast_holiday = st.toggle(
        "Forecast Holiday?",
        value=False,
        key="fc_holiday"
    )
    forecast_weeks = st.number_input("Forecast Weeks", min_value=1, max_value=8, value=4, key="fc_weeks")

    run_next_week = st.button("Forecast Next Week", use_container_width=True)
    run_next_month = st.button("Forecast Next 4 Weeks", use_container_width=True)



    # -------------------------
    # Auto refresh
    # -------------------------
    st.divider()
    auto_refresh = st.checkbox("Auto-refresh", value=False)


# =========================
# Session State
# =========================
if "single_predict_result" not in st.session_state:
    st.session_state["single_predict_result"] = None

if "single_predict_error" not in st.session_state:
    st.session_state["single_predict_error"] = None

if "next_week_result" not in st.session_state:
    st.session_state["next_week_result"] = None

if "next_week_error" not in st.session_state:
    st.session_state["next_week_error"] = None

if "next_month_result" not in st.session_state:
    st.session_state["next_month_result"] = None

if "next_month_error" not in st.session_state:
    st.session_state["next_month_error"] = None


# =========================
# Actions
# =========================
if run_single_predict:
    predict_payload = {
        "Date": str(pred_date),
        "Store": int(pred_store),
        "Dept": int(pred_dept),
        "Type": pred_type,
        "Size": int(pred_size),
        "Temperature": float(pred_temp),
        "Fuel_Price": float(pred_fuel),
        "CPI": float(pred_cpi),
        "Unemployment": float(pred_unemp),
        "MarkDown1": 0.0,
        "MarkDown2": 0.0,
        "MarkDown3": 0.0,
        "MarkDown4": 0.0,
        "MarkDown5": 0.0,
        "IsHoliday": bool(pred_holiday),

    }

    result, error = call_predict_api(api_url, predict_payload)
    st.session_state["single_predict_result"] = result
    st.session_state["single_predict_error"] = error

if run_next_week:
    result, error = call_next_week_forecast(
        api_url=api_url,
        store=int(forecast_store),
        dept=int(forecast_dept),
        store_type=forecast_type,
        size=int(forecast_size),
        temperature=float(forecast_temp),
        fuel_price=float(forecast_fuel),
        cpi=float(forecast_cpi),
        unemployment=float(forecast_unemployment),
        is_holiday=bool(forecast_holiday)
    )

    st.session_state["next_week_result"] = result
    st.session_state["next_week_error"] = error

if run_next_month:
    result, error = call_next_4_weeks_forecast(
        api_url=api_url,
        store=int(forecast_store),
        dept=int(forecast_dept),
        store_type=forecast_type,
        size=int(forecast_size),
        temperature=float(forecast_temp),
        fuel_price=float(forecast_fuel),
        cpi=float(forecast_cpi),
        unemployment=float(forecast_unemployment),
        is_holiday=bool(forecast_holiday),
        weeks=int(forecast_weeks)
    )

    st.session_state["next_month_result"] = result
    st.session_state["next_month_error"] = error

# =========================
# Header
# =========================
st.title("📈 Sales Forecast Platform")
st.caption("XGBoost · MLflow · FastAPI · SQLite · Streamlit")

health = get_api_health(api_url)
c1, c2, c3 = st.columns(3)
with c1:
    st.metric("API Status", "UP" if health.get("status") == "ok" else "DOWN")
with c2:
    st.metric("Model Loaded", str(health.get("model_loaded")))
with c3:
    st.metric("Model Version", health.get("model_version", "unknown"))
# =========================
# Single Prediction Result Display
# =========================
single_result = st.session_state.get("single_predict_result")
single_error = st.session_state.get("single_predict_error")

if single_error:
    st.error(single_error)
elif single_result:
    st.subheader("🔮 Single Prediction Result")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Predicted Units", f"{single_result['predicted_units']:,.2f}")
    c2.metric("Store", single_result["Store"])
    c3.metric("Dept", single_result["Dept"])
    c4.metric("Model Version", single_result["model_version"])

    # st.json(single_result)
#logs helper
#     
@st.cache_data(ttl=30)
def load_forecast_logs():
    if not DB_PATH.exists():
        return pd.DataFrame()

    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM forecast_logs ORDER BY id DESC", conn)
    conn.close()

    if not df.empty:
        if "created_at" in df.columns:
            df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
        if "forecast_date" in df.columns:
            df["forecast_date"] = pd.to_datetime(df["forecast_date"], errors="coerce")

    return df

# =========================
# Tabs
# =========================
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10 = st.tabs([
    "📊 Overview",
    "📝 Logged Predictions",
    "📉 Actual vs Predicted",
    "📈 Dataset Insights",
    "🔮 Daily Forecast",
    "📆 Weekly / Monthly Forecast",
    "🗂 Forecast Logs",
    "🤖 AI Insights",
    "📝 Forecast Report",
    "🧪 MLflow Runs"
])

# -------- Tab 1: Overview --------
with tab1:
    preds_df = load_logged_predictions()

    if preds_df.empty:
        st.info("No logged predictions found yet. Trigger /predict first.")
    else:
        total_preds = len(preds_df)
        avg_pred = preds_df["predicted_units"].mean()
        latest_model = preds_df["model_version"].iloc[0] if "model_version" in preds_df.columns else "unknown"

        m1, m2, m3 = st.columns(3)
        m1.metric("Total Predictions", f"{total_preds:,}")
        m2.metric("Average Predicted Units", f"{avg_pred:,.2f}")
        m3.metric("Latest Model Version", latest_model)

        chart_df = preds_df.sort_values("created_at").copy()

        fig = px.line(
            chart_df,
            x="created_at",
            y="predicted_units",
            title="Logged Predictions Over Time",
            template="plotly_dark"
        )
        st.plotly_chart(fig, use_container_width=True)

# -------- Tab 2: Logged Predictions --------
with tab2:
    preds_df = load_logged_predictions()

    if preds_df.empty:
        st.info("No logged predictions available.")
    else:
        st.subheader("Recent Predictions")
        st.dataframe(preds_df.head(50), use_container_width=True)

        if "store" in preds_df.columns:
            by_store = preds_df.groupby("store", as_index=False)["predicted_units"].mean()
            fig = px.bar(
                by_store,
                x="store",
                y="predicted_units",
                title="Average Predicted Units by Store",
                template="plotly_dark"
            )
            st.plotly_chart(fig, use_container_width=True)
            
# -------- Tab 3: Actual vs Predicted --------
with tab3:
    compare_df = load_actual_vs_predicted()

    if compare_df.empty:
        st.info("No matching actual vs predicted records found yet. Use /predict with real Store, Dept, and Date values that exist in the dataset.")
    else:
        st.subheader("Matched Actual vs Predicted Records")

        metrics = compute_eval_metrics(compare_df)
        c1, c2, c3 = st.columns(3)
        c1.metric("MAE", f"{metrics['mae']:,.2f}")
        c2.metric("RMSE", f"{metrics['rmse']:,.2f}")
        c3.metric("MAPE", f"{metrics['mape']:.2f}%")

        show_df = compare_df[[
            "created_at", "input_date", "Store_logged", "Dept_logged", "Actual", "Predicted", "model_version"
        ]].copy()

        st.dataframe(show_df.sort_values("created_at", ascending=False), use_container_width=True)

        chart_df = compare_df.sort_values("input_date").copy()

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=chart_df["input_date"],
            y=chart_df["Actual"],
            mode="lines+markers",
            name="Actual"
        ))
        fig.add_trace(go.Scatter(
            x=chart_df["input_date"],
            y=chart_df["Predicted"],
            mode="lines+markers",
            name="Predicted"
        ))
        fig.update_layout(
            title="Actual vs Predicted Sales",
            template="plotly_dark",
            xaxis_title="Date",
            yaxis_title="Sales"
        )
        st.plotly_chart(fig, use_container_width=True)

        compare_df["Error"] = compare_df["Actual"] - compare_df["Predicted"]

        fig_err = px.bar(
            compare_df.sort_values("input_date"),
            x="input_date",
            y="Error",
            title="Prediction Error Over Time",
            template="plotly_dark"
        )
        st.plotly_chart(fig_err, use_container_width=True)

# -------- Tab 4: Dataset Insights --------
with tab4:
    sales_df = load_sales_data()

    if sales_df is None:
        st.warning("walmart_cleaned.csv not found in data/")
    else:
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Rows", f"{len(sales_df):,}")
        a2.metric("Stores", sales_df["Store"].nunique() if "Store" in sales_df.columns else 0)
        a3.metric("Departments", sales_df["Dept"].nunique() if "Dept" in sales_df.columns else 0)
        a4.metric("Avg Weekly Sales", f"{sales_df['Weekly_Sales'].mean():,.2f}" if "Weekly_Sales" in sales_df.columns else "N/A")

        if "Date" in sales_df.columns and "Weekly_Sales" in sales_df.columns:
            daily = sales_df.groupby("Date", as_index=False)["Weekly_Sales"].sum()
            fig = px.line(
                daily.tail(180),
                x="Date",
                y="Weekly_Sales",
                title="Historical Weekly Sales (Last 180 Records)",
                template="plotly_dark"
            )
            st.plotly_chart(fig, use_container_width=True)

        if "Store" in sales_df.columns and "Weekly_Sales" in sales_df.columns:
            by_store = sales_df.groupby("Store", as_index=False)["Weekly_Sales"].sum()
            fig2 = px.bar(
                by_store,
                x="Store",
                y="Weekly_Sales",
                title="Total Weekly Sales by Store",
                template="plotly_dark"
            )
            st.plotly_chart(fig2, use_container_width=True)

# -------- Tab 5: Daily Forecast --------
with tab5:
    st.subheader("🔮 Single Prediction")

    single_result = st.session_state.get("single_predict_result")
    single_error = st.session_state.get("single_predict_error")

    if single_error:
        st.error(single_error)
    elif single_result:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Predicted Units", f"{single_result['predicted_units']:,.2f}")
        c2.metric("Store", single_result["Store"])
        c3.metric("Dept", single_result["Dept"])
        c4.metric("Model Version", single_result["model_version"])
        
        st.caption(f"Prediction Date: {single_result['Date']}")
        # st.json(single_result)
    else:
        st.info("Use the Predict button in the sidebar to generate a single prediction.")

# -------- Tab 6: Weekly / Monthly Forecast --------
with tab6:
    st.subheader("Next Week / Next 4 Weeks Forecast")

    week_result = st.session_state.get("next_week_result")
    week_error = st.session_state.get("next_week_error")

    month_result = st.session_state.get("next_month_result")
    month_error = st.session_state.get("next_month_error")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### 📅 Next Week Forecast")
        if week_error:
            st.error(week_error)
        elif week_result and "forecast" in week_result:
            week_df = pd.DataFrame([week_result["forecast"]]).copy()

        # make one categorical label for the whole week
            week_df["forecast_range"] = (
                week_df["forecast_start_date"].astype(str)
                + " → "
                + week_df["forecast_end_date"].astype(str)
            )

            st.dataframe(week_df, use_container_width=True)

            st.metric(
                "Predicted Units (Next Week)",
                f"{week_df['predicted_units'].iloc[0]:,.2f}"
            )

            fig_week = px.bar(
                week_df,
                x="forecast_range",
                y="predicted_units",
                title="Next Week Forecast",
                template="plotly_dark"
            )
            fig_week.update_layout(
                xaxis_title="Forecast Week",
                yaxis_title="Predicted Units"
            )
            st.plotly_chart(fig_week, use_container_width=True)
        else:
            st.info("Use the sidebar button to run next week forecast.")
    with col2:
        st.markdown("### 📆 Next 4 Weeks Forecast")
        if month_error:
            st.error(month_error)
        elif month_result and "forecasts" in month_result:
            month_df = pd.DataFrame(month_result["forecasts"])
            month_df["forecast_start_date"] = pd.to_datetime(month_df["forecast_start_date"], errors="coerce")
            month_df = month_df.sort_values("forecast_start_date")

            st.dataframe(month_df, use_container_width=True)

            fig_line = px.line(
                month_df,
                x="forecast_start_date",
                y="predicted_units",
                markers=True,
                title="Next 4 Weeks Forecast Trend",
                template="plotly_dark"
            )
            st.plotly_chart(fig_line, use_container_width=True)

            fig_bar = px.bar(
                month_df,
                x="forecast_start_date",
                y="predicted_units",
                title="Next 4 Weeks Forecast",
                template="plotly_dark"
            )
            st.plotly_chart(fig_bar, use_container_width=True)
        else:
            st.info("Use the sidebar button to run next 4 weeks forecast.")

# -------- Tab 7: Forecast Logs --------
with tab7:
    st.subheader("Forecast Logs")

    forecast_logs_df = load_forecast_logs()

    if forecast_logs_df.empty:
        st.info("No forecast logs found yet. Run next week or next 4 weeks forecast first.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Forecast Records", f"{len(forecast_logs_df):,}")
        c2.metric("Stores Forecasted", forecast_logs_df["store"].nunique() if "store" in forecast_logs_df.columns else 0)
        c3.metric("Departments Forecasted", forecast_logs_df["dept"].nunique() if "dept" in forecast_logs_df.columns else 0)

        st.dataframe(forecast_logs_df, use_container_width=True)

        if "forecast_date" in forecast_logs_df.columns and "predicted_units" in forecast_logs_df.columns:
            chart_df = forecast_logs_df.sort_values("forecast_date").copy()

            fig_line = px.line(
                chart_df,
                x="forecast_date",
                y="predicted_units",
                color="store" if "store" in chart_df.columns else None,
                markers=True,
                title="Forecasted Units Over Time",
                template="plotly_dark"
            )
            st.plotly_chart(fig_line, use_container_width=True)

            fig_bar = px.bar(
                chart_df,
                x="forecast_date",
                y="predicted_units",
                color="dept" if "dept" in chart_df.columns else None,
                title="Forecast Logs by Date",
                template="plotly_dark"
            )
            st.plotly_chart(fig_bar, use_container_width=True)

# -------- Tab 8: AI Insights --------
with tab8:
    st.subheader("AI Forecast Insights")

    insight_store = st.number_input("Insight Store Filter", min_value=1, max_value=45, value=1, key="insight_store")
    insight_dept = st.number_input("Insight Dept Filter", min_value=1, max_value=99, value=1, key="insight_dept")

    insights_result = generate_forecast_insights(
        store=int(insight_store),
        dept=int(insight_dept)
    )

    st.markdown("### Summary")
    st.info(insights_result["summary"])

    stats = insights_result.get("stats", {})
    if stats:
        c1, c2, c3 = st.columns(3)
        c1.metric("Records", stats.get("records", 0))
        c2.metric("Average Forecast", f"{stats.get('average_forecast', 0):,.2f}")
        c3.metric("Trend", stats.get("trend", "unknown"))

    st.markdown("### Key Insights")
    for item in insights_result.get("insights", []):
        st.markdown(f"- {item}")

    data = insights_result.get("data")
    if isinstance(data, pd.DataFrame) and not data.empty:
        st.markdown("### Latest Forecast Batch")
        st.dataframe(data, use_container_width=True)

        fig = px.line(
            data.sort_values("forecast_date"),
            x="forecast_date",
            y="predicted_units",
            markers=True,
            title="Latest Forecast Batch Trend",
            template="plotly_dark"
        )
        st.plotly_chart(fig, use_container_width=True)

# -------- Tab 9: Forecast Report --------
with tab9:
    st.subheader("Forecast Report")

    report_store = st.number_input("Report Store Filter", min_value=1, max_value=45, value=1, key="report_store")
    report_dept = st.number_input("Report Dept Filter", min_value=1, max_value=99, value=1, key="report_dept")

    report_text = generate_forecast_report(
        store=int(report_store),
        dept=int(report_dept)
    )

    st.text_area("Generated Report", value=report_text, height=350)

    if st.button("Generate PDF Report"):
        pdf_path = save_report_as_pdf(
            report_text=report_text,
            filename_prefix=f"forecast_report_store_{report_store}_dept_{report_dept}"
        )
        st.success(f"PDF created: {pdf_path.name}")

        with open(pdf_path, "rb") as f:
            st.download_button(
                label="Download PDF Report",
                data=f,
                file_name=pdf_path.name,
                mime="application/pdf"
            )

# -------- Tab 10: MLflow Runs --------
with tab10:
    runs_df = load_mlflow_runs()

    if runs_df.empty:
        st.info("No MLflow runs found.")
    else:
        st.dataframe(runs_df, use_container_width=True)

        if "mae" in runs_df.columns and "rmse" in runs_df.columns:
            chart_runs = runs_df.dropna(subset=["mae", "rmse"]).copy()
            if not chart_runs.empty:
                fig = px.scatter(
                    chart_runs,
                    x="mae",
                    y="rmse",
                    color="model_type",
                    hover_data=["run_id"],
                    title="MLflow Runs: MAE vs RMSE",
                    template="plotly_dark"
                )
                st.plotly_chart(fig, use_container_width=True)

if auto_refresh:
    st.rerun()

