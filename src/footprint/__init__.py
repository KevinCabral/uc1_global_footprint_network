"""Local simulation of the AWS ingestion pipeline for the Global Footprint Network data.

One module per box in the architecture diagram:

    config.py      -> Secrets Manager
    api_client.py  -> Global Footprint Network API
    storage.py     -> S3 (raw landing zone + curated zone)
    extract.py     -> Lambda Extract        (1 year per invocation)
    transform.py   -> Lambda Transform      (triggered by an S3 event)
    load.py        -> Snowflake MERGE       (duckdb locally)
    pipeline.py    -> Step Functions        (discover years, run each one)
"""
