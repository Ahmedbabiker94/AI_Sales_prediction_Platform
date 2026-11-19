"""Offline, static guard: the current PG client lock is incomplete.

This guard is NOT a client build or a PostgreSQL integration test. If a
reviewed rebaseline is later approved, update this guard with its new lock.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _imports(filename):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    return {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    }


def test_current_postgres_client_lock_is_not_complete_dependency_lock():
    lock = (ROOT / "tests/postgres/requirements.lock").read_text()
    dockerfile = (ROOT / "tests/postgres/Dockerfile").read_text()
    assert "psycopg2-binary==2.9.12" in lock
    assert not any(line.startswith(("pytest==", "pydantic==", "pandas=="))
                   for line in lock.splitlines())
    assert "FROM sha256:417063b71e35c7a9889c10ac2516c328f16a483e065ec858df101eb9728c2df2" in dockerfile
    assert "--no-index --no-deps --require-hashes" in dockerfile


def test_pg_test_source_requires_pytest_pydantic_pandas():
    assert "pytest" in _imports("tests/postgres/conftest.py")
    assert "pytest" in _imports("tests/postgres/test_forecast_evidence_postgres.py")
    assert {"pydantic", "pandas"}.issubset(
        _imports("src/data/contracts/forecast_evidence_storage.py"))
