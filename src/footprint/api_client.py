"""Client for the Global Footprint Network API.

    https://api.footprintnetwork.org/v1

Auth is HTTP Basic: the username is ignored, the API key is sent as the password.
Without it every endpoint returns 403.
"""

from __future__ import annotations

import logging
import time

import requests
from requests.auth import HTTPBasicAuth

log = logging.getLogger(__name__)

# Retried once per attempt with a growing pause. Anything else fails immediately:
# a 403 will not fix itself by trying again.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class FootprintApiClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.footprintnetwork.org/v1",
        username: str = "any",
        timeout: int = 30,
        max_attempts: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth(username, api_key)

    def _get(self, path: str):
        """GET {base_url}/{path} with a small backoff on transient failures."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        for attempt in range(1, self.max_attempts + 1):
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code in RETRYABLE_STATUS and attempt < self.max_attempts:
                pause = 2**attempt
                log.warning(
                    "GET %s returned %s, retrying in %ss (attempt %s/%s)",
                    url, response.status_code, pause, attempt, self.max_attempts,
                )
                time.sleep(pause)
                continue
            response.raise_for_status()
            return response.json()

    def get_years(self) -> list[int]:
        """Years for which the API holds data, newest first."""
        return [item["year"] for item in self._get("years")]

    def get_countries(self) -> list[dict]:
        """Reference list of countries (code, name, ISO alpha-2)."""
        return self._get("countries")

    def get_data_for_year(self, year: int, record: str = "EFCpc") -> list[dict]:
        """One row per country for the given year.

        This is the unit of work of the pipeline: ~153 records / ~50 KB, which is
        why the diagram gives one year to each Lambda Extract invocation.
        """
        return self._get(f"data/all/{year}/{record}")

    def data_url(self, year: int, record: str = "EFCpc") -> str:
        """The URL `get_data_for_year` would call. Recorded in the manifest for lineage."""
        return f"{self.base_url}/data/all/{year}/{record}"
