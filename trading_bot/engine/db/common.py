"""Helpers shared by the Postgres and in-memory DALs (no psycopg import
here, so MemoryDAL works on a box without the driver)."""
import hashlib
import json

from trading_bot.engine.context import _json_default

ENGINE_VERSION = "m1"


def plain(obj):
    """Round-trip through our JSON encoder: datetimes/sets/dataclasses ->
    JSON-native values, exactly as they would land in a jsonb column."""
    return json.loads(json.dumps(obj, default=_json_default))


def config_hash(payload: dict) -> str:
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_default)
    return hashlib.sha256(canon.encode()).hexdigest()[:32]
