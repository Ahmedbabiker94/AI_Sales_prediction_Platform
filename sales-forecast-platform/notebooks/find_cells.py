
import json
import sys

notebook_path = r'c:\Users\ababi\OneDrive\Desktop\AI_Platform\sales-forecast-platform\notebooks\AutoML_to_predict_future_sales.ipynb'

with open(notebook_path, 'r', encoding='utf-8') as f:
    nb = json.load(f)

for i, cell in enumerate(nb['cells']):
    if cell['cell_type'] == 'code':
        source = "".join(cell['source'])
        if 'log_model' in source or 'LSTM' in source or 'xgb' in source or 'lgb' in source:
            print(f"Cell {i}:")
            print(source[:200] + "...")
            print("-" * 20)
