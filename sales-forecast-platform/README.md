# 📈 Sales Forecast Platform

A production-grade, end-to-end sales forecasting system built with **LightGBM**, **MLflow**, **FastAPI**, **Streamlit**, and **Docker Compose**.

---

## Architecture

```
Sales Data (CSV)
     │
     ▼
src/features.py        ← Feature engineering (lags, rolling stats, calendar)
     │
     ▼
src/train.py           ← LightGBM training + MLflow experiment tracking
     │
     ▼
MLflow Model Registry  ← Best run promoted to "Production" stage
     │
     ▼
api/main.py            ← FastAPI service (/predict, /batch-predict, /health)
     │
     ▼
report_service/        ← HTML report with metrics + drift charts
     │
     ▼
dashboard/app.py       ← Streamlit dashboard (predictions + drift monitor)
     │
     ▼
docker-compose.yml     ← Orchestrates all 4 services
     │
     ▼
src/scheduler.py       ← APScheduler (daily predict · weekly retrain)
```

---

## Project Structure

```
sales-forecast-platform/
├── data/
│   ├── sales.csv             ← Your external dataset goes here
│   └── predictions.csv       ← Written by scheduler daily job
├── src/
│   ├── features.py           ← Feature engineering pipeline
│   ├── train.py              ← Training entry-point
│   ├── evaluate.py           ← Standalone evaluation
│   └── scheduler.py          ← APScheduler jobs
├── api/
│   ├── main.py               ← FastAPI app
│   └── schemas.py            ← Pydantic schemas
├── report_service/
│   └── generator.py          ← HTML report + drift charts
├── dashboard/
│   └── app.py                ← Streamlit dashboard
├── mlruns/                   ← MLflow file backend (auto-populated)
├── reports/                  ← Generated HTML reports
├── Dockerfile.api
├── Dockerfile.dashboard
├── docker-compose.yml
├── requirements.txt          ← All deps (local dev)
├── requirements.api.txt      ← API service deps
├── requirements.dashboard.txt← Dashboard deps
└── README.md
```

---

## Quick Start (Local)

### 1. Install dependencies
```bash
cd sales-forecast-platform
pip install -r requirements.txt
```

### 2. Supply your dataset
Make sure your dataset is placed at `data/sales.csv` using the same format as the outer notebook.

### 3. Train the model
```bash
# Train + automatically promote best run to Production
python src/train.py --data data/sales.csv --promote
```

### 4. Launch the API
```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```
Test it:
```bash
curl http://localhost:8000/health

curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"date":"2026-01-15","store_id":"STORE_01","product_id":"PROD_001","price":29.99,"promotion":0}'
```

### 5. Launch the dashboard
```bash
streamlit run dashboard/app.py
```
→ Opens at **http://localhost:8501**

### 6. View MLflow UI
```bash
mlflow ui --backend-store-uri mlruns --port 5000
```
→ Opens at **http://localhost:5000**

### 7. Generate a report
```bash
python report_service/generator.py --data data/sales.csv --out reports
```

### 8. Run the scheduler (background)
```bash
python src/scheduler.py
```
| Job | Schedule | Action |
|-----|----------|--------|
| `daily_predict` | 02:00 UTC | Run inference, write `data/predictions.csv` |
| `weekly_retrain` | Mon 03:00 UTC | Re-train + promote best model |

---

## Docker Compose

```bash
# Build and start all services
docker compose up --build

# Run the report generator (on-demand)
docker compose --profile report up report_service
```

| Service | URL |
|---------|-----|
| MLflow UI | http://localhost:5000 |
| FastAPI | http://localhost:8000 |
| FastAPI Docs | http://localhost:8000/docs |
| Streamlit | http://localhost:8501 |

---

## Feature Engineering

| Category | Features |
|----------|----------|
| Calendar | day_of_week, day_of_month, week_of_year, month, quarter, year |
| Cyclical | month_sin/cos, dow_sin/cos |
| Weekend | is_weekend |
| Lags | lag_1, lag_7, lag_14, lag_28 |
| Rolling | rolling_mean/std at 7, 14, 28 day windows |
| Contextual | price, promotion flag |
| Categorical | store_id, product_id (ordinal encoded) |

---

## Drift Monitoring

Uses **Population Stability Index (PSI)**:

| PSI Range | Status |
|-----------|--------|
| < 0.10 | 🟢 Stable |
| 0.10 – 0.20 | 🟡 Moderate — monitor |
| > 0.20 | 🔴 High Drift — retrain recommended |

---

## API Reference

### `POST /predict`
Single-row prediction.

```json
{
  "date": "2026-01-15",
  "store_id": "STORE_01",
  "product_id": "PROD_001",
  "price": 29.99,
  "promotion": 0
}
```

### `POST /batch-predict`
Batch predictions — pass a list of rows inside `{"rows": [...]}`.

### `GET /health`
Returns model load status and version.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Model | LightGBM |
| Tracking | MLflow (file backend) |
| Registry | MLflow Model Registry |
| API | FastAPI + Uvicorn |
| Dashboard | Streamlit + Plotly |
| Reports | Matplotlib + HTML |
| Scheduling | APScheduler |
| Containers | Docker + Compose |
| Language | Python 3.11 |
