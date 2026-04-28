import sqlite3
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT /"database" /"predictions.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        store INTEGER,
        dept INTEGER,
        input_date TEXT,
        predicted_units REAL,
        model_version TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS forecast_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        forecast_date TEXT,
        store INTEGER,
        dept INTEGER,
        predicted_units REAL,
        model_version TEXT
    )
    """)

    conn.commit()
    conn.close()

def log_prediction(store, dept, input_date, predicted_units, model_version):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    INSERT INTO predictions (
        created_at,
        store,
        dept,
        input_date,
        predicted_units,
        model_version
    )
    VALUES (?, ?, ?, ?, ?, ?)
    """, (
        datetime.utcnow().isoformat(),
        store,
        dept,
        input_date,
        predicted_units,
        model_version
    ))

    conn.commit()
    conn.close()
def log_forecast(forecast_date, store, dept, predicted_units, model_version):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    INSERT INTO forecast_logs (
        created_at,
        forecast_date,
        store,
        dept,
        predicted_units,
        model_version
    )
    VALUES (?, ?, ?, ?, ?, ?)
    """, (
        datetime.utcnow().isoformat(),
        forecast_date,
        store,
        dept,
        predicted_units,
        model_version
    ))

    conn.commit()
    conn.close()