"""Ingestion into the target database. duckdb locally, Snowflake in the diagram.

The load is a partition overwrite inside a transaction: delete the year, insert
the year. That is what makes the whole pipeline re-runnable -- loading 2021 twice
leaves 2021 exactly once. In Snowflake this is the `MERGE` fed by a Stream+Task;
the semantics are the same because a year's Parquet file is the full truth for
that year.
"""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb

from .transform import CURATED_COLUMNS, CURATED_SCHEMA

log = logging.getLogger(__name__)

TABLE = "curated.carbon_footprint"


def _ensure_table(con) -> None:
    columns = ", ".join(f"{name} {sql_type}" for name, sql_type in CURATED_SCHEMA)
    con.execute("CREATE SCHEMA IF NOT EXISTS curated")
    con.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} ({columns})")


def load_year(duckdb_path: Path, parquet_path: str | Path, year: int) -> dict:
    """Replace the given year in the target table with the contents of its Parquet file."""
    duckdb_path = Path(duckdb_path)
    duckdb_path.parent.mkdir(parents=True, exist_ok=True)
    columns = ", ".join(CURATED_COLUMNS)

    con = duckdb.connect(str(duckdb_path))
    try:
        _ensure_table(con)
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute(f"DELETE FROM {TABLE} WHERE year = ?", [year])
            con.execute(
                f"INSERT INTO {TABLE} SELECT {columns} FROM read_parquet(?)",
                [str(parquet_path)],
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        loaded = con.execute(f"SELECT count(*) FROM {TABLE} WHERE year = ?", [year]).fetchone()[0]
    finally:
        con.close()

    log.info("load: year=%s rows=%s -> %s", year, loaded, TABLE)
    return {"year": year, "rows_loaded": loaded}


def query(duckdb_path: Path, sql: str, params: list | None = None) -> list[tuple]:
    """Small read helper for the CLI and the tests."""
    con = duckdb.connect(str(duckdb_path), read_only=True)
    try:
        return con.execute(sql, params or []).fetchall()
    finally:
        con.close()
