"""Re-export shim. The market-structure logic was promoted to
trading_bot/engine/structure.py (the live bot ships only trading_bot/;
research/ is not in the deploy zip). Research code and tests keep
importing from here unchanged."""
from trading_bot.engine.structure import (  # noqa: F401
    StructuralSwing,
    StructureEvent,
    find_structure_events,
    label_swings,
)
