from airflow import DAG
from airflow.operators.bash import BashOperator
from datetime import datetime

with DAG(
    dag_id="sales_model_retraining",
    start_date=datetime(2025, 1, 1),
    schedule="@weekly",
    catchup=False,
) as dag:

    retrain_model = BashOperator(
        task_id="retrain_model",
        bash_command="""
        cd /home/ubuntu/AI_Sales_prediction_Platform/sales-forecast-platform &&
        source .venv/bin/activate &&
        python train.py
        """
    )
