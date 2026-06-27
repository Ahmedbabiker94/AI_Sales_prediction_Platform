from sqlalchemy import text

from src.database.db import engine


class StoreMetadataRepository:

    def get_store_metadata(
        self,
        store: int
    ):

        query = text("""
            SELECT
                type,
                size
            FROM store_metadata
            WHERE store = :store
        """)

        with engine.connect() as conn:

            row = conn.execute(
                query,
                {"store": store}
            ).fetchone()

        if not row:
            return None

        return {
            "type": row[0],
            "size": row[1]
        }