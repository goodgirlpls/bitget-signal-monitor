import math


# =========================
# SHARED RISK SETTINGS
# =========================

ENTRY_FEE = 0.0006
EXIT_FEE = 0.0006
SLIPPAGE = 0.0005

MIN_RR = 1.8
ORB_MIN_RR = 2.0


# =========================
# VALIDATION
# =========================

def positive_number(value):

    value = float(value)

    if not math.isfinite(value) or value <= 0:
        raise ValueError("Invalid positive number")

    return value


def validate_direction(direction):

    if direction not in ("LONG", "SHORT"):
        raise ValueError("Invalid trade direction")


# =========================
# TRANSACTION COST
# =========================

def exit_cost(entry, exit_price):

    entry = positive_number(entry)
    exit_price = positive_number(exit_price)

    return (
        entry * ENTRY_FEE
        + exit_price * EXIT_FEE
        + (entry + exit_price) * SLIPPAGE
    )


# =========================
# UNIT RISK
# =========================

def risk_per_unit(entry, stop):

    entry = positive_number(entry)
    stop = positive_number(stop)

    return (
        abs(entry - stop)
        + exit_cost(entry, stop)
    )


# =========================
# UNIT NET PROFIT
# =========================

def profit_per_unit(entry, target):

    entry = positive_number(entry)
    target = positive_number(target)

    return (
        abs(target - entry)
        - exit_cost(entry, target)
    )


# =========================
# NET RISK REWARD RATIO
# =========================

def calculate_rr(entry, stop, target, direction):

    validate_direction(direction)

    entry = positive_number(entry)
    stop = positive_number(stop)
    target = positive_number(target)

    if direction == "LONG":

        if not stop < entry < target:
            raise ValueError("Invalid LONG prices")

    else:

        if not target < entry < stop:
            raise ValueError("Invalid SHORT prices")

    risk = risk_per_unit(entry, stop)

    profit = profit_per_unit(entry, target)

    return profit / risk


# =========================
# EXACT TARGET CALCULATION
# =========================

def calculate_target(entry, stop, direction, rr):

    validate_direction(direction)

    entry = positive_number(entry)
    stop = positive_number(stop)
    rr = positive_number(rr)

    if direction == "LONG" and stop >= entry:
        raise ValueError("Invalid LONG stop")

    if direction == "SHORT" and stop <= entry:
        raise ValueError("Invalid SHORT stop")

    risk = risk_per_unit(entry, stop)

    entry_rate = ENTRY_FEE + SLIPPAGE
    exit_rate = EXIT_FEE + SLIPPAGE

    required_profit = rr * risk

    if direction == "LONG":

        target = (
            required_profit
            + entry * (1 + entry_rate)
        ) / (1 - exit_rate)

    else:

        target = (
            entry * (1 - entry_rate)
            - required_profit
        ) / (1 + exit_rate)

    target = positive_number(target)

    if direction == "LONG" and target <= entry:
        raise ValueError("Invalid LONG target")

    if direction == "SHORT" and target >= entry:
        raise ValueError("Invalid SHORT target")

    return target


# =========================
# POSITION SIZING
# =========================

def calculate_position(
    entry,
    stop,
    risk_budget,
    max_notional
):

    entry = positive_number(entry)
    stop = positive_number(stop)

    risk_budget = positive_number(risk_budget)
    max_notional = positive_number(max_notional)

    unit_risk = risk_per_unit(entry, stop)

    risk_quantity = risk_budget / unit_risk

    capital_quantity = max_notional / entry

    return min(
        risk_quantity,
        capital_quantity
    )


# =========================
# COMPLETE RISK PLAN
# =========================

def build_risk_plan(
    entry,
    stop,
    direction,
    risk_budget,
    max_notional,
    required_rr
):

    target = calculate_target(
        entry,
        stop,
        direction,
        required_rr
    )

    quantity = calculate_position(
        entry,
        stop,
        risk_budget,
        max_notional
    )

    unit_risk = risk_per_unit(
        entry,
        stop
    )

    unit_profit = profit_per_unit(
        entry,
        target
    )

    estimated_loss = quantity * unit_risk

    net_profit = quantity * unit_profit

    net_rr = calculate_rr(
        entry,
        stop,
        target,
        direction
    )

    return {
        "entry": entry,
        "stop": stop,
        "target": target,
        "quantity": quantity,
        "notional": quantity * entry,
        "estimated_loss": estimated_loss,
        "estimated_cost": (
            quantity * exit_cost(entry, target)
        ),
        "stop_cost": (
            quantity * exit_cost(entry, stop)
        ),
        "target_cost": (
            quantity * exit_cost(entry, target)
        ),
        "net_profit": net_profit,
        "net_rr": net_rr
    }


# =========================
# SELF TEST
# =========================

def self_test():

    test_cases = [
        ("LONG", 60000, 59800, 1.8),
        ("SHORT", 60000, 60200, 1.8),
        ("LONG", 1700, 1690, 2.0),
        ("SHORT", 1700, 1710, 2.0),
        ("LONG", 72, 71.5, 1.8),
        ("SHORT", 72, 72.5, 2.0)
    ]

    for direction, entry, stop, rr in test_cases:

        plan = build_risk_plan(
            entry=entry,
            stop=stop,
            direction=direction,
            risk_budget=5.0,
            max_notional=14500.0,
            required_rr=rr
        )

        assert math.isclose(
            plan["net_rr"],
            rr,
            rel_tol=1e-9,
            abs_tol=1e-9
        )

        assert plan["estimated_loss"] <= 5.0 + 1e-9

        assert plan["notional"] <= 14500.0 + 1e-9

        print(
            direction,
            "ENTRY:", entry,
            "STOP:", stop,
            "TARGET:", round(plan["target"], 6),
            "LOSS:", round(plan["estimated_loss"], 4),
            "PROFIT:", round(plan["net_profit"], 4),
            "RR:", round(plan["net_rr"], 6),
            "PASS"
        )

    print("ALL RISK TESTS PASSED")


if __name__ == "__main__":
    self_test()
