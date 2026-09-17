"""Execution layer (spec §39-§46, §47, §52).

    Risk Engine -> Execution Gate -> Execution Engine -> Broker

`Broker` is an interface; `PaperBroker` is the only implementation in M2
and simulates fills, spread, slippage, latency, timeouts, partial fills
and rejections. There is NO live broker here: it arrives in M4 as the
single allow-listed exception to tests/test_engine_no_orders.py, behind
the three-switch gate in engine/config.py.
"""
from trading_bot.engine.execution.broker import Broker, BrokerOrder, Fill, OrderRequest
from trading_bot.engine.execution.gate import GateParams, GateResult, execution_gate
from trading_bot.engine.execution.orders import OrderRecord, OrderStateMachine, ORDER_STATES
from trading_bot.engine.execution.paper import PaperBroker, PaperParams
from trading_bot.engine.execution.snapshot import SignalSnapshot, drift_check, lock_snapshot

__all__ = ["Broker", "BrokerOrder", "Fill", "OrderRequest", "GateParams", "GateResult", "execution_gate",
           "OrderRecord", "OrderStateMachine", "ORDER_STATES", "PaperBroker", "PaperParams", "SignalSnapshot",
           "drift_check", "lock_snapshot"]
