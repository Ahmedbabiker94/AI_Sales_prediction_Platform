"""
scheduler.py
------------
APScheduler automation for the Sales Forecast Platform.

Jobs:
  • Daily 08:00 UTC  -> next week forecast
  • Daily 08:05 UTC  -> next 4 weeks forecast

Usage:
    python src/scheduler.py
"""

import logging
from pathlib import Path

import requests
from apscheduler.schedulers.blocking import BlockingScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
API_BASE = "http://127.0.0.1:8000"

# Stores / departments to forecast automatically
FORECAST_TARGETS = [
    {"Store": 1, "Dept": 1},
    # add more later if needed
    # {"Store": 2, "Dept": 5},
    # {"Store": 3, "Dept": 10},
]


def run_next_week_forecast():
    log.info("▶ Next-week forecast job started")

    for target in FORECAST_TARGETS:
        try:
            resp = requests.post(
                f"{API_BASE}/forecast-next-week",
                json=target,
                timeout=60,
            )

            if resp.status_code == 200:
                data = resp.json()
                forecast = data.get("forecast", {})
                log.info(
                    f"✅ Next-week forecast saved | "
                    f"Store={target['Store']} Dept={target['Dept']} "
                    f"Pred={forecast.get('predicted_units')}"
                )
            else:
                log.error(
                    f"❌ Next-week forecast failed | "
                    f"Store={target['Store']} Dept={target['Dept']} "
                    f"Status={resp.status_code} Body={resp.text}"
                )

        except Exception as e:
            log.error(
                f"❌ Exception in next-week forecast | "
                f"Store={target['Store']} Dept={target['Dept']} Error={e}"
            )


def run_next_4_weeks_forecast():
    log.info("▶ Next-4-weeks forecast job started")

    for target in FORECAST_TARGETS:
        payload = {
            "Store": target["Store"],
            "Dept": target["Dept"],
            "weeks": 4,
        }

        try:
            resp = requests.post(
                f"{API_BASE}/forecast-next-4-weeks",
                json=payload,
                timeout=60,
            )

            if resp.status_code == 200:
                data = resp.json()
                total_weeks = data.get("total_weeks", 0)
                log.info(
                    f"✅ Next-4-weeks forecast saved | "
                    f"Store={target['Store']} Dept={target['Dept']} "
                    f"Weeks={total_weeks}"
                )
            else:
                log.error(
                    f"❌ Next-4-weeks forecast failed | "
                    f"Store={target['Store']} Dept={target['Dept']} "
                    f"Status={resp.status_code} Body={resp.text}"
                )

        except Exception as e:
            log.error(
                f"❌ Exception in next-4-weeks forecast | "
                f"Store={target['Store']} Dept={target['Dept']} Error={e}"
            )


def main():
    scheduler = BlockingScheduler(timezone="UTC")

    # Real scheduled jobs
    scheduler.add_job(
        run_next_week_forecast,
        trigger="cron",
        hour=8,
        minute=0,
        id="next_week_forecast",
        replace_existing=True,
    )

    scheduler.add_job(
        run_next_4_weeks_forecast,
        trigger="cron",
        hour=8,
        minute=5,
        id="next_4_weeks_forecast",
        replace_existing=True,
    )

    log.info("Scheduler started. Jobs:")
    for job in scheduler.get_jobs():
        log.info(f"  {job.id}: {job.trigger}")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")


if __name__ == "__main__":
    main()