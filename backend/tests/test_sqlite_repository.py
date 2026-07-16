import sqlite3

import pytest

from app.config import Settings
from app.repositories import SQLiteFinanceRepository
from scripts.init_sqlite import SCHEMA, seed_demo


@pytest.mark.asyncio
async def test_sqlite_repository_is_scoped_and_recheckable(tmp_path) -> None:
    path = tmp_path / "financial.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        seed_demo(connection)
        connection.commit()

    repository = SQLiteFinanceRepository(
        Settings(data_mode="local", sqlite_path=str(path), sqlite_read_only=True)
    )
    companies = await repository.list_companies()
    evidence = await repository.get_metrics("DEMO01", "2026Q2")
    preview = await repository.get_source_preview(
        "demo01-financial-metrics-2026q2", "DEMO01"
    )

    assert {item.co_code for item in companies} == {"DEMO01", "DEMO02"}
    assert len(evidence) == 2
    assert all(item.co_code == "DEMO01" for item in evidence)
    assert all(item.locator.primary_key for item in evidence)
    assert preview is not None
    assert preview.database_record["data_version"] == "demo-v1"
