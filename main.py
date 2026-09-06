"""CLI entrypoint.

    python main.py run                  # backfill from GFN_START_YEAR (2010) to the latest year
    python main.py run --year 2021      # a single year
    python main.py query                # carbon footprint evolution, straight from duckdb
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from footprint import load, pipeline  # noqa: E402
from footprint.api_client import FootprintApiClient  # noqa: E402
from footprint.config import get_settings  # noqa: E402

# Regional rollups are excluded: they would otherwise be averaged in alongside
# the countries they already contain.
EVOLUTION_SQL = f"""
SELECT year,
       count(*)                                     AS countries,
       count(carbon_gha_per_person)                 AS with_carbon,
       round(avg(carbon_gha_per_person), 3)         AS avg_carbon_gha_per_person,
       round(avg(carbon_share_pct), 1)              AS avg_carbon_share_pct
FROM {load.TABLE}
WHERE NOT is_region
GROUP BY year
ORDER BY year
"""

WORLD_SQL = f"""
SELECT year, round(carbon_gha_per_person, 3) AS carbon_gha_per_person,
       round(carbon_share_pct, 1) AS carbon_share_pct
FROM {load.TABLE}
WHERE country_name = 'World' AND carbon_gha_per_person IS NOT NULL
ORDER BY year
"""

TOP_EMITTERS_SQL = f"""
SELECT country_name, year, round(carbon_gha_per_person, 3) AS carbon_gha_per_person
FROM {load.TABLE}
WHERE NOT is_region
  AND carbon_gha_per_person IS NOT NULL
  AND year = (SELECT max(year) FROM {load.TABLE}
              WHERE NOT is_region AND carbon_gha_per_person IS NOT NULL)
ORDER BY carbon_gha_per_person DESC
LIMIT 10
"""


def _print_table(title: str, headers: list[str], rows: list[tuple]) -> None:
    print(f"\n{title}")
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(str(h))
              for i, h in enumerate(headers)]
    print("  ".join(str(h).ljust(w) for h, w in zip(headers, widths)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(str(v).ljust(w) for v, w in zip(row, widths)))


def cmd_run(args) -> int:
    settings = get_settings()
    years = [args.year] if args.year else None
    summary = pipeline.run(settings, years=years)

    print(f"\nrun_id={summary['run_id']}  succeeded={summary['succeeded']}  "
          f"failed={summary['failed']}  rows_loaded={summary['rows_loaded']}")
    for result in summary["years"]:
        if result["status"] == "SUCCEEDED":
            print(f"  {result['year']}  OK      {result['rows_loaded']:>4} rows  {result['raw_key']}")
        else:
            print(f"  {result['year']}  FAILED  {result['error']}")
    return 1 if summary["failed"] else 0


def cmd_query(args) -> int:
    settings = get_settings()
    if not settings.duckdb_path.exists():
        print(f"No database at {settings.duckdb_path}. Run `python main.py run` first.")
        return 1

    _print_table(
        "Carbon footprint evolution (gha per person, average across countries, regions excluded)",
        ["year", "countries", "with_carbon", "avg_carbon", "avg_share_pct"],
        load.query(settings.duckdb_path, EVOLUTION_SQL),
    )
    _print_table(
        "World aggregate",
        ["year", "carbon_gha_per_person", "carbon_share_pct"],
        load.query(settings.duckdb_path, WORLD_SQL),
    )
    _print_table(
        "Top 10 countries, latest year with carbon data",
        ["country", "year", "carbon_gha_per_person"],
        load.query(settings.duckdb_path, TOP_EMITTERS_SQL),
    )
    return 0


def cmd_sql(args) -> int:
    """Run any SQL against the warehouse, so no duckdb CLI is needed."""
    settings = get_settings()
    if not settings.duckdb_path.exists():
        print(f"No database at {settings.duckdb_path}. Run `python main.py run` first.")
        return 1

    con = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        result = con.execute(args.statement)
        headers = [d[0] for d in result.description]
        rows = result.fetchall()
    finally:
        con.close()

    _print_table(f"{len(rows)} row(s)", headers, rows)
    return 0


def cmd_api(args) -> int:
    """Show what the API returns, before any transformation."""
    settings = get_settings()
    client = FootprintApiClient(settings.api_key, settings.api_base_url, settings.api_username)

    if args.endpoint == "years":
        print(json.dumps(client.get_years(), indent=2))
        return 0
    if args.endpoint == "countries":
        countries = client.get_countries()
        print(f"{len(countries)} countries (first {args.limit}):")
        print(json.dumps(countries[: args.limit], indent=2))
        return 0

    rows = client.get_data_for_year(args.year, settings.record_code)
    print(f"GET {client.data_url(args.year, settings.record_code)}")
    print(f"{len(rows)} records. First {args.limit}:\n")
    print(json.dumps(rows[: args.limit], indent=2))
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Global Footprint Network ingestion pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="extract, transform and load")
    run_parser.add_argument("--year", type=int, help="load a single year instead of the backfill")
    run_parser.set_defaults(func=cmd_run)

    query_parser = sub.add_parser("query", help="show what landed in duckdb")
    query_parser.set_defaults(func=cmd_query)

    sql_parser = sub.add_parser("sql", help="run your own SQL against the warehouse")
    sql_parser.add_argument("statement", help='e.g. "SELECT * FROM curated.carbon_footprint LIMIT 5"')
    sql_parser.set_defaults(func=cmd_sql)

    api_parser = sub.add_parser("api", help="show the raw API response, before any transformation")
    api_parser.add_argument("endpoint", nargs="?", default="data",
                            choices=["data", "years", "countries"])
    api_parser.add_argument("--year", type=int, default=2021)
    api_parser.add_argument("--limit", type=int, default=3, help="records to print")
    api_parser.set_defaults(func=cmd_api)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
