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

        self.contracts = {
            "BTCUSDT": {
                "price_step": "0.1",
                "size_step": "0.0001",
                "min_size": "0.0001",
                "min_notional": "5",
                "max_order_size": None,
                "min_leverage": "1",
                "max_leverage": "125",
                "status": "normal"
            },
            "ETHUSDT": {
                "price_step": "0.01",
                "size_step": "0.01",
                "min_size": "0.01",
                "min_notional": "5",
                "max_order_size": None,
                "min_leverage": "1",
                "max_leverage": "100",
                "status": "normal"
            },
            "SOLUSDT": {
                "price_step": "0.001",
                "size_step": "0.1",
                "min_size": "0.1",
                "min_notional": "5",
                "max_order_size": None,
                "min_leverage": "1",
                "max_leverage": "100",
                "status": "normal"
            }
        }

        contract_patch = patch(
            "trader.CONTRACTS",
            self.contracts
        )

        contract_patch.start()
        self.addCleanup(contract_patch.stop)

        account_patch = patch(
            "trader.get_symbol_account",
            return_value=self.account
        )

        account_patch.start()
        self.addCleanup(account_patch.stop)

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

    def build_test_order(
        self,
        symbol="BTCUSDT",
        direction="LONG",
        strategy="A",
        budget=5.0
    ):

        signal = self.make_signal(
            symbol,
            direction,
            strategy
        )

        return trader.build_order(
            signal,
            self.snapshot,
            budget
        )

    def test_all_strategies(self):

        count = 0

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

                            order = self.build_test_order(
                                symbol,
                                direction,
                                strategy,
                                budget
                            )

                            spec = self.contracts[symbol]

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

                            self.assertGreaterEqual(
                                float(order["size"]),
                                float(spec["min_size"])
                            )

                            self.assertGreaterEqual(
                                order["notional"],
                                float(spec["min_notional"])
                            )

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
                                    entry,
                                    stop
                                ),
                                places=8
                            )

                            self.assertAlmostEqual(
                                order["net_rr"],
                                risk.calculate_rr(
                                    entry,
                                    stop,
                                    target,
                                    direction
                                ),
                                places=8
                            )

                            self.assertAlmostEqual(
                                order["net_profit"],
                                size * risk.profit_per_unit(
                                    entry,
                                    target
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

        self.account["crossedMarginLeverage"] = "101"

        with self.assertRaisesRegex(
            RuntimeError,
            "Invalid leverage"
        ):

            self.build_test_order()

    def test_exchange_max_leverage(self):

        self.contracts["BTCUSDT"]["max_leverage"] = "5"

        with self.assertRaisesRegex(
            RuntimeError,
            "Invalid leverage"
        ):

            self.build_test_order()

    def test_exchange_min_leverage(self):

        self.contracts["BTCUSDT"]["min_leverage"] = "20"

        with self.assertRaisesRegex(
            RuntimeError,
            "Invalid leverage"
        ):

            self.build_test_order()

    def test_valid_exchange_leverage(self):

        self.contracts["BTCUSDT"]["max_leverage"] = "10"

        order = self.build_test_order()

        self.assertEqual(
            order["leverage"],
            10
        )

    def test_insufficient_balance(self):

        self.snapshot["available"] = 0.01

        with self.assertRaises(RuntimeError):

            self.build_test_order()

    def test_invalid_stop(self):

        signal = self.make_signal(
            "BTCUSDT",
            "LONG",
            "A"
        )

        signal["stop"] = signal["entry"] + 100

        with self.assertRaises(RuntimeError):

            trader.build_order(
                signal,
                self.snapshot,
                5.0
            )

    def test_dynamic_price_step(self):

        self.contracts["BTCUSDT"]["price_step"] = "0.5"

        for direction in ("LONG", "SHORT"):

            with self.subTest(direction=direction):

                order = self.build_test_order(
                    direction=direction
                )

                for field in (
                    "price",
                    "stop",
                    "target"
                ):

                    self.assertEqual(
                        Decimal(order[field])
                        % Decimal("0.5"),
                        Decimal("0")
                    )

    def test_dynamic_size_step(self):

        self.contracts["BTCUSDT"]["size_step"] = "0.005"

        order = self.build_test_order()

        self.assertGreater(
            Decimal(order["size"]),
            Decimal("0")
        )

        self.assertEqual(
            Decimal(order["size"])
            % Decimal("0.005"),
            Decimal("0")
        )

    def test_dynamic_min_size(self):

        self.contracts["BTCUSDT"]["min_size"] = "1"

        with self.assertRaisesRegex(
            RuntimeError,
            "Quantity below minimum"
        ):

            self.build_test_order()

    def test_dynamic_min_notional(self):

        self.contracts["BTCUSDT"]["min_notional"] = "2000"

        with self.assertRaisesRegex(
            RuntimeError,
            "Notional below minimum"
        ):

            self.build_test_order()

    def test_max_order_size(self):

        original = self.build_test_order()

        self.contracts["BTCUSDT"]["max_order_size"] = "0.005"

        limited = self.build_test_order()

        self.assertLess(
            float(limited["size"]),
            float(original["size"])
        )

        self.assertLessEqual(
            Decimal(limited["size"]),
            Decimal("0.005")
        )

        self.assertLessEqual(
            limited["estimated_loss"],
            5.0 + 1e-8
        )

    def test_max_order_size_rounding(self):

        self.contracts["BTCUSDT"]["max_order_size"] = "0.00555"

        order = self.build_test_order()

        self.assertEqual(
            Decimal(order["size"]),
            Decimal("0.0055")
        )

        self.assertLessEqual(
            Decimal(order["size"]),
            Decimal("0.00555")
        )

    def test_unknown_max_order_size(self):

        self.contracts["BTCUSDT"]["max_order_size"] = None

        with patch("builtins.print") as mock_print:

            order = self.build_test_order()

        self.assertGreater(
            float(order["size"]),
            0
        )

        self.assertLessEqual(
            order["estimated_loss"],
            5.0 + 1e-8
        )

        mock_print.assert_any_call(
            "WARNING: Exchange maximum order size unavailable",
            "BTCUSDT"
        )

    def test_max_order_size_below_minimum(self):

        self.contracts["BTCUSDT"]["max_order_size"] = "0.00001"

        with self.assertRaisesRegex(
            RuntimeError,
            "Quantity below minimum"
        ):

            self.build_test_order()

    def test_invalid_position_mode(self):

        self.account["posMode"] = "one_way_mode"

        with self.assertRaisesRegex(
            RuntimeError,
            "Unexpected position mode"
        ):

            self.build_test_order()

    def test_invalid_margin_mode(self):

        self.account["marginMode"] = "isolated"

        with self.assertRaisesRegex(
            RuntimeError,
            "Unexpected margin mode"
        ):

            self.build_test_order()

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
