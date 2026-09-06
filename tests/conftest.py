"""Shared test fixtures. No test in this file's tree touches the network."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from footprint.config import Settings  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


class FakeApiClient:
    """Stands in for FootprintApiClient, serving the JSON fixtures.

    Same interface as the real client, which is the whole point of injecting the
    client into pipeline.run(): the pipeline cannot tell the difference.
    """

    def __init__(self, years: list[int] | None = None) -> None:
        self.years = years or [2021, 2022]
        self.calls: list[tuple[int, str]] = []

    def get_years(self) -> list[int]:
        # The real API returns newest first and includes years before 2010.
        return sorted(self.years + [2009, 2008], reverse=True)

    def get_data_for_year(self, year: int, record: str = "EFCpc") -> list[dict]:
        self.calls.append((year, record))
        path = FIXTURES / f"efcpc_{year}.json"
        if not path.exists():
            raise FileNotFoundError(f"no fixture for year {year}")
        return json.loads(path.read_text(encoding="utf-8"))

    def data_url(self, year: int, record: str = "EFCpc") -> str:
        return f"https://fake.test/v1/data/all/{year}/{record}"


@pytest.fixture
def fake_client() -> FakeApiClient:
    return FakeApiClient()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings with every output redirected into a per-test tmp dir."""
    return Settings(
        api_key="fake-key",
        api_username="any",
        api_base_url="https://fake.test/v1",
        start_year=2010,
        record_code="EFCpc",
        raw_dir=tmp_path / "raw",
        curated_dir=tmp_path / "curated",
        duckdb_path=tmp_path / "warehouse" / "footprint.duckdb",
    )
