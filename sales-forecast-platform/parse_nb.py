import json
import sys

def parse_notebook(file_path, output_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        nb = json.load(f)
    
    with open(output_path, 'w', encoding='utf-8') as out_f:
        for i, cell in enumerate(nb.get('cells', [])):
            if cell.get('cell_type') == 'code':
                out_f.write(f"### Cell {i} ###\n")
                out_f.write("".join(cell.get('source', [])))
                out_f.write("\n\n")

if __name__ == "__main__":
    if len(sys.argv) > 2:
        parse_notebook(sys.argv[1], sys.argv[2])
    else:
        print("Usage: python parse_nb.py <notebook_path> <output_path>")
