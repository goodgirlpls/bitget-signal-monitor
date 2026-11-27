import math

from risk import (
    ENTRY_FEE,
    EXIT_FEE,
    SLIPPAGE,
    risk_per_unit,
    calculate_target
)

TP1_R = 2.0
TP1_RATIO = 0.5
TRAIL_START_R = 3.0
TRAIL_ATR_BUFFER = 0.15


def positive(value):

    value = float(value)

    if not math.isfinite(value) or value <= 0:
        raise ValueError("Invalid positive value")

    return value


def calculate_breakeven(entry, direction):

    entry = positive(entry)

    entry_cost = ENTRY_FEE + SLIPPAGE
    exit_cost = EXIT_FEE + SLIPPAGE

    if direction == "LONG":

        return (
            entry * (1 + entry_cost)
            / (1 - exit_cost)
        )

    if direction == "SHORT":

        return (
            entry * (1 - entry_cost)
            / (1 + exit_cost)
        )

    raise ValueError("Invalid direction")


def calculate_position_targets(
    entry,
    initial_stop,
    direction
):

    entry = positive(entry)
    initial_stop = positive(initial_stop)

    if direction == "LONG":
        if initial_stop >= entry:
            raise ValueError("Invalid LONG stop")

    elif direction == "SHORT":
        if initial_stop <= entry:
            raise ValueError("Invalid SHORT stop")

    else:
        raise ValueError("Invalid direction")

    initial_risk = risk_per_unit(
        entry,
        initial_stop
    )

    tp1 = calculate_target(
        entry,
        initial_stop,
        direction,
        TP1_R
    )

    trail_start = calculate_target(
        entry,
        initial_stop,
        direction,
        TRAIL_START_R
    )

    breakeven = calculate_breakeven(
        entry,
        direction
    )

    return {
        "initial_risk": initial_risk,
        "tp1": tp1,
        "tp1_ratio": TP1_RATIO,
        "breakeven": breakeven,
        "trail_start": trail_start
    }


def calculate_trailing_stop(
    direction,
    current_stop,
    structure_level,
    atr,
    market_price
):

    current_stop = positive(current_stop)
    structure_level = positive(structure_level)
    atr = positive(atr)
    market_price = positive(market_price)

    buffer = atr * TRAIL_ATR_BUFFER

    if direction == "LONG":

        candidate = structure_level - buffer

        if current_stop < candidate < market_price:
            return candidate

    elif direction == "SHORT":

        candidate = structure_level + buffer

        if market_price < candidate < current_stop:
            return candidate

    else:
        raise ValueError("Invalid direction")

    return current_stop
