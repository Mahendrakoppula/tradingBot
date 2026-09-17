"""The second bot's analysis/trading engine, built to the index-options spec
(see .claude/plans/goofy-plotting-sedgewick.md for the phase checklist).

Hard rule for everything under this package: nothing here may import
RestClient.place_order / modify_order / cancel_order, directly or via
strategy.place_split_order / debit_strategy.LongOptionStrategy. Order
placement, when it arrives in M2, lives behind an execution gate in its own
subpackage, and even then only a LIVE-mode broker adapter touches the real
endpoint. tests/test_engine_no_orders.py enforces this with an AST scan.
"""
