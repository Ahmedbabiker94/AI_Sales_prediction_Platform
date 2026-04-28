
import json
import sys

notebook_path = r'c:\Users\ababi\OneDrive\Desktop\AI_Platform\sales-forecast-platform\notebooks\AutoML_to_predict_future_sales.ipynb'

try:
    with open(notebook_path, 'r', encoding='utf-8') as f:
        nb = json.load(f)
except Exception as e:
    print(f"Error loading notebook: {e}")
    sys.exit(1)

with open('cells_summary.txt', 'w', encoding='utf-8') as out:
    for i, cell in enumerate(nb['cells']):
        if cell['cell_type'] == 'code':
            source = "".join(cell.get('source', []))
            # Just find any cell that has keywords
            keywords = ['log_model', 'LSTM', 'xgb', 'lgb', 'mlflow']
            matched = [kw for kw in keywords if kw.lower() in source.lower()]
            if matched:
                out.write(f"Cell {i} (Matched {matched}):\n")
                out.write(source)
                out.write("\n" + "="*40 + "\n")
