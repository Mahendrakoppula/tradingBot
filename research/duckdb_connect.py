"""One-line setup for querying the bot's S3-synced historical data with
real SQL - no ETL/conversion pipeline, no running database. DuckDB reads
the JSON/JSONL files directly off S3 (verified live 2026-09-08: including
UNNEST() over the nested `contracts` arrays in option_chain_log). Revisit
with a Parquet conversion step only if/when query speed actually becomes a
problem at real data volume - premature right now with days, not months,
of history.

Usage:
    from research.duckdb_connect import connect
    con = connect()
    con.sql("SELECT * FROM read_json_auto('s3://.../historical/journal.jsonl')").show()

Needs local AWS credentials already configured (same ones used throughout
this project - `aws configure` or the deployer profile) with read access to
the deploy bucket; see deployer_iam_policy.json's S3DeployBucket statement,
already scoped to trading-bot-*/*.
"""
import duckdb

DEPLOY_BUCKET = "trading-bot-deploy-396913392704"  # stable for the life of this project, see deploy/DEPLOY.md
REGION = "ap-south-1"
HISTORICAL_PREFIX = f"s3://{DEPLOY_BUCKET}/trading-bot/historical"


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("CALL load_aws_credentials();")
    con.execute(f"SET s3_region='{REGION}';")
    return con


if __name__ == "__main__":
    con = connect()
    print(f"Connected. Historical data root: {HISTORICAL_PREFIX}")
    print("Try: con.sql(f\"SELECT * FROM read_json_auto('{HISTORICAL_PREFIX}/journal.jsonl')\").show()")
