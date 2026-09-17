"""Persistence for the index-options engine (spec §59-§66).

`dal.Database` is the PostgreSQL implementation (psycopg 3); `memory.MemoryDAL`
is the same interface in-process for tests and BACKTEST replay. Both are
append-only journals: nothing here is ever read back to make a trading
decision in M1 - the loop keeps its own state and the DB is the record.

Schema lives in `schema_v1.py` as SQL string constants (the deploy zip does
not ship non-.py files) and is applied by `Database.migrate()` at startup.
"""
