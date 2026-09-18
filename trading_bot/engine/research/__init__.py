"""Validation and research tooling (spec §73-§79, §81, §83): everything
here reads the journal (trade results, signals, executions) or drives the
engine offline. Nothing in this package is on the live decision path and
nothing can reach an order endpoint (AST guard covers engine/).
"""
