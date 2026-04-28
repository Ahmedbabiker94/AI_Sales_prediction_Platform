import json

def fix_notebook(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        nb = json.load(f)
    
    for cell in nb.get('cells', []):
        if cell.get('cell_type') == 'code':
            source = "".join(cell.get('source', []))
            
            # Fix data path
            if "pd.read_csv('walmart_cleaned.csv')" in source:
                source = source.replace("pd.read_csv('walmart_cleaned.csv')", "pd.read_csv('../data/walmart_cleaned.csv')")
            
            # Fix XGBoost early stopping (older API to newer API if needed, 
            # but let's just make sure it's consistent)
            # In Cell 19: model = xgb.XGBRegressor(**params, early_stopping_rounds=30)
            # In Cell 21: xgb_model = xgb.XGBRegressor(**xgb_best, early_stopping_rounds=50)
            # This is actually supported in many versions but let's check.
            
            # Also fix typos
            source = source.replace("unnasseccery", "unnecessary")
            source = source.replace("Handiling", "Handling")
            source = source.replace("clleaning", "cleaning")
            source = source.replace("engneering", "engineering")
            source = source.replace("archticture", "architecture")
            source = source.replace("thte", "the")
            source = source.replace("nagative", "negative")
            source = source.replace("clculating", "calculating")
            source = source.replace("dtermaine", "determine")
            
            cell['source'] = [line + '\n' if not line.endswith('\n') else line for line in source.splitlines()]
            if cell['source'] and not source.endswith('\n'):
                cell['source'][-1] = cell['source'][-1].rstrip('\n')
                
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(nb, f, indent=2)

if __name__ == "__main__":
    fix_notebook('c:/Users/ababi/OneDrive/Desktop/AI_Platform/sales-forecast-platform/notebooks/AutoML_to_predict_future_sales.ipynb')
