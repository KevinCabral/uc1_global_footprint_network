"""Configuration and secrets.

`get_api_key()` is the seam where AWS Secrets Manager plugs in. Locally it reads
a .env file; in AWS the body becomes:

    boto3.client("secretsmanager").get_secret_value(SecretId="gfn/api-key")

Nothing else in the codebase reads the key, so that is a one-function change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Project root: .../use_case_1
PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")


class MissingSecretError(RuntimeError):
    """Raised when the API key is not available."""


def get_api_key() -> str:
    """Return the Global Footprint Network API key.

    Simulates `secretsmanager:GetSecretValue`. In AWS the key is encrypted and
    rotated; locally it lives in .env, which is git-ignored.
    """
    key = os.getenv("GFN_API_KEY", "").strip()
    if not key:
        raise MissingSecretError(
            "GFN_API_KEY is not set. Copy .env.example to .env and fill in the key."
        )
    return key


@dataclass(frozen=True)
class Settings:
    """Everything the pipeline needs to run, resolved once at start-up."""

    api_key: str
    api_username: str
    api_base_url: str
    start_year: int
    record_code: str

    # Local stand-ins for the two S3 buckets and for Snowflake.
    raw_dir: Path
    curated_dir: Path
    duckdb_path: Path


def get_settings(data_dir: Path | None = None) -> Settings:
    """Build Settings from the environment.

    `data_dir` is overridable so tests can point the whole pipeline at a tmp dir.
    """
    root = data_dir or (PROJECT_ROOT / "data")
    return Settings(
        api_key=get_api_key(),
        api_username=os.getenv("GFN_API_USERNAME", "any"),
        api_base_url=os.getenv("GFN_API_BASE_URL", "https://api.footprintnetwork.org/v1"),
        start_year=int(os.getenv("GFN_START_YEAR", "2010")),
        record_code=os.getenv("GFN_RECORD_CODE", "EFCpc"),
        raw_dir=root / "raw",
        curated_dir=root / "curated",
        duckdb_path=root / "warehouse" / "footprint.duckdb",
    )
