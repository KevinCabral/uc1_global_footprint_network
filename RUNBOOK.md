# Runbook — operating the pipeline

Everyday commands: reset the data, look at what the API returns, query duckdb,
and read the test results. See [README.md](README.md) for the architecture and
[SETUP.md](SETUP.md) for installing on a fresh machine.

All commands run from `use_case_1\` with the virtual environment active:

```powershell
..\.venv\Scripts\Activate.ps1
```

If you would rather not activate it, prefix every `python` with the full path:
`..\.venv\Scripts\python.exe main.py ...`.

---

## 1. Delete the data and run again

Everything under `data\` is generated. Deleting it loses nothing — the raw JSON
can be re-fetched from the API in about 15 seconds.

```powershell
# wipe the raw zone, the curated zone and the duckdb warehouse
Remove-Item -Recurse -Force data

# rebuild everything: 2010 -> latest, 16 years, ~2450 rows
python main.py run
```

Git Bash equivalent: `rm -rf data && python main.py run`.

### Partial resets

```powershell
# just the warehouse, keeping the raw and curated files
Remove-Item -Recurse -Force data\warehouse

# reload a single year (no need to delete anything first, see below)
python main.py run --year 2021
```

### When you must delete before re-running

Only one case: **you changed `CURATED_SCHEMA` in `src\footprint\transform.py`.**
The table is created with `CREATE TABLE IF NOT EXISTS`, so an existing warehouse
keeps the old columns and the insert fails on a column-count mismatch. Delete
`data\warehouse` and run again.

In every other case, just re-run. The pipeline is idempotent: `run --year 2021`
twice leaves 153 rows for 2021, not 306. The raw zone keeps both downloads under
separate `run_id=` folders; the warehouse keeps only the newest.

### Check the reset worked

```powershell
python main.py sql "SELECT count(*) AS rows, count(DISTINCT year) AS years FROM curated.carbon_footprint"
```

Expect 2448 rows across 16 years after a full run.

---

## 2. See the data the API returns

This shows the untransformed API response, before any of the pipeline's cleaning.

```powershell
python main.py api data --year 2021            # first 3 records of a year
python main.py api data --year 2021 --limit 10 # first 10
python main.py api years                       # every year the API offers
python main.py api countries --limit 5         # the country reference list
```

A single record looks like this. `carbon` is the carbon footprint in global
hectares per person — the number the dashboard is built on. `value` is the total
Ecological Footprint, of which carbon is one part:

```json
{
  "year": 2021,
  "countryCode": 2,
  "countryName": "Afghanistan",
  "isoa2": "AF",
  "record": "EFConsPerCap",
  "cropLand": 0.419456510235304,
  "grazingLand": 0.148886776481619,
  "forestLand": 0.057874551547637,
  "fishingGround": 0.000861147700553316,
  "builtupLand": 0.0219572741510616,
  "carbon": 0.143973180754063,
  "value": 0.793009440870238,
  "score": "3A"
}
```

### The same data, as it was stored on disk

The raw zone holds exactly what the API returned, untouched, so you can inspect a
past run without calling the API again:

```powershell
# list the downloads kept for one year (one folder per run)
Get-ChildItem data\raw\footprintnetwork\EFCpc\year=2021

# the manifest: row count, sha256, source URL, timestamp
Get-Content data\raw\footprintnetwork\EFCpc\year=2021\*\_manifest.json
```

### Calling the API by hand

```powershell
curl.exe -u "any:$env:GFN_API_KEY" "https://api.footprintnetwork.org/v1/data/all/2021/EFCpc"
```

Auth is HTTP Basic. The username is ignored; the API key goes in the password
slot. Without it every endpoint returns `403`.

---

## 3. Query duckdb

There is no duckdb command-line tool installed — only the Python library — so the
project ships its own SQL command:

```powershell
python main.py query                       # the three prepared reports
python main.py sql "<any SQL>"             # anything you want
```

The table is `curated.carbon_footprint`, one row per country per year.

### Useful queries

```powershell
# what columns exist
python main.py sql "DESCRIBE curated.carbon_footprint"

# one country over time
python main.py sql "SELECT year, carbon_gha_per_person FROM curated.carbon_footprint WHERE iso2 = 'PT' ORDER BY year"

# the dashboard's core question: carbon evolution, countries only
python main.py sql "SELECT year, round(avg(carbon_gha_per_person), 3) AS avg_carbon FROM curated.carbon_footprint WHERE NOT is_region GROUP BY year ORDER BY year"

# biggest movers between 2010 and 2023
python main.py sql "SELECT a.country_name, round(b.carbon_gha_per_person - a.carbon_gha_per_person, 2) AS change FROM curated.carbon_footprint a JOIN curated.carbon_footprint b USING (country_code) WHERE a.year = 2010 AND b.year = 2023 AND NOT a.is_region AND a.carbon_gha_per_person IS NOT NULL AND b.carbon_gha_per_person IS NOT NULL ORDER BY change LIMIT 10"

# where the data is missing
python main.py sql "SELECT year, count(*) AS total, count(carbon_gha_per_person) AS with_carbon FROM curated.carbon_footprint WHERE NOT is_region GROUP BY year ORDER BY year"
```

### Two things to remember when querying

**Filter `WHERE NOT is_region`.** The API mixes 27 rollups (`Europe`, `Oceania`,
`World`) into the same list as the 126 real countries. Without the filter you
count every country twice, and rollups win any "top emitters" ranking.

**Country carbon data ends at 2023.** 2024 and 2025 load 153 rows each, but every
country has `carbon = NULL`; only the rollups have values there.

### Querying the Parquet files directly

The curated zone can be read without the warehouse at all, which is how Snowflake
would consume it from S3:

```powershell
python main.py sql "SELECT year, count(*) FROM read_parquet('data/curated/carbon_footprint/*/*.parquet') GROUP BY year ORDER BY year"
```

### Opening the database in Python

```python
import duckdb
con = duckdb.connect("data/warehouse/footprint.duckdb", read_only=True)
con.execute("SELECT * FROM curated.carbon_footprint LIMIT 5").fetchall()
```

Use `read_only=True` if the pipeline might be running at the same time — duckdb
allows only one writer.

---

## 4. Run the tests and see what passed

```powershell
pytest -v
```

`-v` lists every test by name with PASSED or FAILED instead of a row of dots:

```
tests/test_pipeline.py::test_end_to_end PASSED                           [ 12%]
tests/test_pipeline.py::test_pipeline_discovers_years_from_the_api PASSED [ 25%]
tests/test_pipeline.py::test_normalize_rules PASSED                      [ 37%]
tests/test_pipeline.py::test_normalize_handles_an_empty_payload PASSED   [ 50%]
tests/test_pipeline.py::test_pipeline_is_idempotent PASSED               [ 62%]
tests/test_pipeline.py::test_raw_bucket_refuses_to_overwrite PASSED      [ 75%]
tests/test_pipeline.py::test_manifest_matches_the_raw_payload PASSED     [ 87%]
tests/test_pipeline.py::test_a_failing_year_does_not_stop_the_others PASSED [100%]

======================= 8 passed, 1 deselected in 1.03s =======================
```

"1 deselected" is the live API test, skipped by default. That is configured in
`pytest.ini` and is normal.

### Other reporting flags

| Command | What it gives you |
|---|---|
| `pytest -v` | one line per test, PASSED / FAILED |
| `pytest -q` | one character per test, just the summary |
| `pytest -rA` | a short reason line for every test, passes included |
| `pytest -v -s` | also prints the pipeline's log output as tests run |
| `pytest -x` | stop at the first failure |
| `pytest --tb=short` | shorter tracebacks when something fails |
| `pytest --durations=5` | the five slowest tests |
| `pytest -k idempotent` | run only tests whose name matches |
| `pytest -m live` | run the real-API test instead of the mocked ones |
| `pytest -v -m ""` | run everything, mocked and live together |

`pytest -v -s` is the one worth knowing for a demo: it interleaves the pipeline's
own `INFO` logs with the test names, so you can watch extract → transform → load
happen inside the test.

### Reading a failure

A failing test prints the assertion, the values on both sides, and any log output
captured during the test:

```
E       assert 2 == 0
tests\test_pipeline.py:112: AssertionError
------------------------------ Captured log call ------------------------------
ERROR    footprint.pipeline: year 2021 failed: ... already exists and the bucket is immutable
```

The `Captured log call` section is usually where the real cause is.

### What the 8 tests cover

| Test | Checks |
|---|---|
| `test_end_to_end` | full run against mock data: raw, manifest, Parquet, duckdb rows |
| `test_pipeline_discovers_years_from_the_api` | years before `start_year` are skipped |
| `test_normalize_rules` | renames, casts, `carbon_share_pct`, dedupe, region flag, nulls kept |
| `test_normalize_handles_an_empty_payload` | an empty year does not crash |
| `test_pipeline_is_idempotent` | running twice leaves the row count unchanged |
| `test_raw_bucket_refuses_to_overwrite` | the raw zone is immutable |
| `test_manifest_matches_the_raw_payload` | row count and sha256 agree with the file |
| `test_a_failing_year_does_not_stop_the_others` | one bad year does not sink the backfill |

No test touches the network — they use the fixtures in `tests\fixtures\`.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `MissingSecretError: GFN_API_KEY is not set` | no `.env` file. Copy `.env.example` to `.env` and add the key. |
| `403 Client Error` from the API | the key is wrong or expired. Check `.env`. |
| `Binder Error: table has N columns but M values` | you changed `CURATED_SCHEMA`. Delete `data\warehouse` and re-run. |
| `already exists and the bucket is immutable` | the same `run_id` wrote to the raw zone twice. Expected only if you pass a fixed `run_id` by hand. |
| `No database at ...` | run `python main.py run` first. |
| `IO Error: Could not set lock on file` | the duckdb file is open elsewhere. Close the other session. |
| `ModuleNotFoundError: footprint` | you are not in `use_case_1\`, or the venv is not active. |
