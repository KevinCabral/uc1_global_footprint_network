# Setup — running this project on another machine

For someone who received this project as a zip. Takes about five minutes, most of
it waiting for `pip install`.

See [README.md](README.md) for what the project does and
[RUNBOOK.md](RUNBOOK.md) for day-to-day commands.

---

## What you need

- **Python 3.10 or newer** — `python --version`. Nothing else; no AWS account,
  no Docker, no database server. duckdb is a library, not a service.
- **An internet connection**, but only to fetch the data. The test suite is fully
  mocked and passes offline.

---

## Install

```powershell
# 1. unzip, then step into the project
cd use_case_1

# 2. create a virtual environment and activate it
python -m venv .venv
.venv\Scripts\Activate.ps1

# 3. install the four dependencies
pip install -r requirements.txt
```

macOS / Linux / Git Bash: `python3 -m venv .venv && source .venv/bin/activate`.

If PowerShell blocks the activate script, either run
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` for that window, or
skip activation and prefix every command with `.venv\Scripts\python.exe`.

### The API key

The project reads the key from a `.env` file, which stands in for AWS Secrets
Manager. If the zip contains a `.env`, you are ready. If it only contains
`.env.example`:

```powershell
Copy-Item .env.example .env
```

and put the key from the technical test document into `GFN_API_KEY=`.

---

## Verify the install, in three steps

### 1. The tests — 30 seconds, no internet needed

```powershell
pytest -v
```

Expect `8 passed, 1 deselected`. The deselected one is an optional test against
the real API; the other eight run the whole pipeline against the mock data in
`tests\fixtures\`, so this proves the code works without touching the network.

### 2. One year against the real API — 5 seconds

```powershell
python main.py run --year 2021
```

Expect `succeeded=1  failed=0  rows_loaded=153`.

This is also the quickest way to confirm the API key works. A `403` means the key
in `.env` is wrong.

### 3. The full backfill — about 15 seconds

```powershell
python main.py run
python main.py query
```

Expect 16 years, `rows_loaded=2448`, then three tables of results. The evolution
table should show average carbon per person drifting down from about 1.65 in 2010
to about 1.41 in 2023, with a visible dip in 2020.

You now have:

```
data\raw\        the JSON exactly as the API returned it, plus a manifest  (S3 raw zone)
data\curated\    one Parquet file per year                                 (S3 curated zone)
data\warehouse\  footprint.duckdb, table curated.carbon_footprint          (Snowflake)
```

Nothing in `data\` is precious — delete it and run again whenever you like.

---

## Have a look around

```powershell
python main.py api data --year 2021    # what the API returns, before transformation
python main.py query                   # the prepared reports
python main.py sql "SELECT * FROM curated.carbon_footprint LIMIT 5"
python main.py sql "DESCRIBE curated.carbon_footprint"
```

The seven modules in `src\footprint\` each correspond to one box in the
architecture diagram; the table in [README.md](README.md) maps them.

---

## If something goes wrong

| Symptom | Fix |
|---|---|
| `MissingSecretError: GFN_API_KEY is not set` | create `.env` from `.env.example` and add the key |
| `403 Client Error` | the key in `.env` is wrong or expired |
| `ModuleNotFoundError: requests` / `duckdb` | the venv is not active, or `pip install -r requirements.txt` was skipped |
| `ModuleNotFoundError: footprint` | run commands from inside `use_case_1\` |
| `pytest` is not recognised | use `python -m pytest -v` |
| activate script blocked | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| `Binder Error: table has N columns but M values` | someone changed the schema; delete `data\warehouse` and re-run |

---

## For whoever is sending the zip

Zip the project **without** the generated and local-only folders. `data\` is
2.3 MB of regenerable output, and `.venv` must never be shipped — it contains
absolute paths to the machine that created it.

```powershell
# from the folder that contains use_case_1
Compress-Archive -Path use_case_1 -DestinationPath footprint_use_case_1.zip

# then remove what should not travel
$zip = [IO.Compression.ZipFile]::Open("footprint_use_case_1.zip", "Update")
$zip.Entries | Where-Object { $_.FullName -match '(^|/)(data|\.venv|__pycache__|\.pytest_cache)/' } |
    ForEach-Object { $_.Delete() }
$zip.Dispose()
```

Simpler alternative: delete `data\`, `__pycache__\` and `.pytest_cache\` first,
then zip the folder normally.

```powershell
Remove-Item -Recurse -Force data, .pytest_cache -ErrorAction SilentlyContinue
Get-ChildItem -Recurse -Directory __pycache__ | Remove-Item -Recurse -Force
Compress-Archive -Path use_case_1 -DestinationPath footprint_use_case_1.zip
```

The result is about 1 MB and contains 23 files — most of that is the PDF and the
architecture diagram; the code itself is under 40 KB.

**A note on `.env`.** It holds the API key. For this technical test the key came
from the assignment document, so including `.env` makes the reviewer's life
easier and is the recommended choice. In a real project `.env` is git-ignored and
never shipped — the recipient creates their own from `.env.example`. If you would
rather not send the key, delete `.env` from the zip and tell the recipient to
copy `.env.example`.

### Checklist before sending

- [ ] `pytest -v` passes — 8 passed
- [ ] `python main.py run` completes with `failed=0`
- [ ] `data\`, `.venv\`, `__pycache__\`, `.pytest_cache\` are not in the zip
- [ ] `.env` is either present with a working key, or deliberately removed
- [ ] `README.md`, `RUNBOOK.md` and `SETUP.md` are included
- [ ] the architecture diagram `AWS Architecture for Footprint.drawio.png` is included
