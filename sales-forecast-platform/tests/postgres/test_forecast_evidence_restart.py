"""Executed by a NEW client AFTER the harness restarts the same PostgreSQL server."""
from conftest import make_batch, repository, admin


def test_new_connection_after_server_restart_finds_exact_durable_receipt():
    batch = make_batch('restart-seed')
    store = repository(batch)
    found = store._lookup(batch.idempotency_id, expected=batch)
    assert found is not None and found.batch.canonical_bytes == batch.canonical_bytes
    assert store._append_initial(batch).receipt == found.receipt
    with admin() as connection, connection.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM canonical_forecast.evidence WHERE organization_id=%s',
            (str(batch.scope.organization_id),))
        assert cursor.fetchone() == (4,)
