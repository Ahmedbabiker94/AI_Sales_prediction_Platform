from src.database.db import engine

with engine.connect() as conn:
    print("connected successfuly")