from airflow import DAG
from airflow.operators.bash import BashOperator
from datetime import datetime

with DAG(
    dag_id="forecast_accuracy_pipeline",
    start_date=datetime(2025, 1, 1),
    schedule="@daily",
    catchup=False,
) as dag:

    evaluate_accuracy = BashOperator(
        task_id="evaluate_accuracy",
        bash_command="""
        cd /home/ubuntu/AI_Sales_prediction_Platform/sales-forecast-platform &&
        source .venv/bin/activate &&
        python -m src.jobs.forecast_accuracy_job
        """
    )