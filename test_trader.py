import unittest

from decimal import Decimal
from unittest.mock import patch

import trader
import risk


class TraderTests(unittest.TestCase):

    def setUp(self):

        self.snapshot = {
            "equity": 145.0,
            "available": 145.0,
            "positions": [],
            "pending": []
        }

        self.account = {
            "posMode": "hedge_mode",
            "marginMode": "crossed",
            "crossedMarginLeverage": "10"
        }

    def make_signal(self, symbol, direction, strategy):

        prices = {
            "BTCUSDT": (60000.03, 200),
            "ETHUSDT": (1700.003, 10),
            "SOLUSDT": (72.0003, 0.5)
        }

        entry, distance = prices[symbol]

        stop = (
            entry - distance
            if direction == "LONG"
            else entry + distance
        )

        rr = trader.STRATEGY_MIN_RR[strategy]

        plan = risk.build_risk_plan(
            entry=entry,
            stop=stop,
            direction=direction,
            risk_budget=5.0,
            max_notional=14500,
            required_rr=rr
        )

        return {
            **plan,
            "symbol": symbol,
            "direction": direction,
            "strategy": strategy,
            "signal_id": (
                f"TEST-{symbol}-{direction}-{strategy}"
            )
        }

    def test_all_strategies(self):

        count = 0

        with patch(
            "trader.get_symbol_account",
            return_value=self.account
        ):

            for symbol in trader.SYMBOLS:

                for direction in ("LONG", "SHORT"):

                    for strategy in ("A", "B", "C", "D"):

                        for budget in (5.0, 2.5):

                            with self.subTest(
                                symbol=symbol,
                                direction=direction,
                                strategy=strategy,
                                budget=budget
                            ):

                                signal = self.make_signal(
                                    symbol,
                                    direction,
                                    strategy
                                )

                                order = trader.build_order(
                                    signal,
                                    self.snapshot,
                                    budget
                                )

                                self.assertLessEqual(
                                    order["estimated_loss"],
                                    budget + 1e-8
                                )

                                self.assertGreaterEqual(
                                    order["net_rr"] + 1e-9,
                                    trader.STRATEGY_MIN_RR[
                                        strategy
                                    ]
                                )

                                self.assertGreater(
                                    float(order["size"]),
                                    0
                                )

                                self.assertGreaterEqual(
                                    order["notional"],
                                    5
                                )

                                spec = trader.CONTRACTS[symbol]

                                for field in (
                                    "price",
                                    "stop",
                                    "target"
                                ):

                                    self.assertEqual(
                                        Decimal(order[field])
                                        % Decimal(spec["price_step"]),
                                        Decimal("0")
                                    )

                                self.assertEqual(
                                    Decimal(order["size"])
                                    % Decimal(spec["size_step"]),
                                    Decimal("0")
                                )

                                entry = float(order["price"])
                                stop = float(order["stop"])
                                target = float(order["target"])
                                size = float(order["size"])

                                if direction == "LONG":
                                    self.assertTrue(
                                        0 < stop < entry < target
                                    )
                                else:
                                    self.assertTrue(
                                        0 < target < entry < stop
                                    )

                                self.assertAlmostEqual(
                                    order["estimated_loss"],
                                    size * risk.risk_per_unit(
                                        entry, stop
                                    ),
                                    places=8
                                )

                                self.assertLessEqual(
                                    order["margin"]
                                    + order["notional"]
                                    * risk.ENTRY_FEE,
                                    self.snapshot["available"]
                                    * 0.80 + 1e-8
                                )

                                count += 1

                                print(
                                    symbol,
                                    direction,
                                    strategy,
                                    "BUDGET:", budget,
                                    "RISK:", round(
                                        order["estimated_loss"], 4
                                    ),
                                    "RR:", round(
                                        order["net_rr"], 4
                                    ),
                                    "PASS"
                                )

        self.assertEqual(count, 48)

        print("ALL 48 ORDER BUILD CASES PASSED")

    def test_invalid_leverage(self):

        account = dict(self.account)
        account["crossedMarginLeverage"] = "101"

        signal = self.make_signal(
            "BTCUSDT", "LONG", "A"
        )

        with patch(
            "trader.get_symbol_account",
            return_value=account
        ):

            with self.assertRaises(RuntimeError):

                trader.build_order(
                    signal,
                    self.snapshot,
                    5.0
                )

    def test_insufficient_balance(self):

        snapshot = dict(self.snapshot)
        snapshot["available"] = 0.01

        signal = self.make_signal(
            "BTCUSDT", "LONG", "A"
        )

        with patch(
            "trader.get_symbol_account",
            return_value=self.account
        ):

            with self.assertRaises(RuntimeError):

                trader.build_order(
                    signal,
                    snapshot,
                    5.0
                )

    def test_invalid_stop(self):

        signal = self.make_signal(
            "BTCUSDT", "LONG", "A"
        )

        signal["stop"] = signal["entry"] + 100

        with patch(
            "trader.get_symbol_account",
            return_value=self.account
        ):

            with self.assertRaises(RuntimeError):

                trader.build_order(
                    signal,
                    self.snapshot,
                    5.0
                )

    def test_execution_disabled(self):

        with patch("trader.MODE", "auto_trade"):

            with patch(
                "trader.place_order"
            ) as mock_order:

                self.assertFalse(
                    trader.EXECUTION_ENABLED
                )

                result = trader.execute_order({}, {})

                self.assertFalse(result)

                mock_order.assert_not_called()


if __name__ == "__main__":

    unittest.main(verbosity=2)
