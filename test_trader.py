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
            "market_target": plan["target"] + (
                abs(plan["target"] - plan["entry"])
                if direction == "LONG"
                else -abs(plan["target"] - plan["entry"])
            ),
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

    def test_breakeven_long_short(self):

        entry = 60000.0

        for direction in ("LONG", "SHORT"):

            with self.subTest(direction=direction):

                price = trader.calculate_breakeven(
                    entry, direction
                )

                entry_cost = entry * (
                    risk.ENTRY_FEE + risk.SLIPPAGE
                )

                exit_cost = price * (
                    risk.EXIT_FEE + risk.SLIPPAGE
                )

                gross_profit = (
                    price - entry
                    if direction == "LONG"
                    else entry - price
                )

                self.assertAlmostEqual(
                    gross_profit - entry_cost - exit_cost,
                    0.0,
                    places=8
                )


    def test_dynamic_position_targets(self):

        for direction in ("LONG", "SHORT"):

            with self.subTest(direction=direction):

                entry = 60000.0

                stop = (
                    59800.0 if direction == "LONG"
                    else 60200.0
                )

                targets = trader.calculate_position_targets(
                    entry, stop, direction
                )

                self.assertAlmostEqual(
                    risk.calculate_rr(
                        entry, stop,
                        targets["tp1"],
                        direction
                    ),
                    2.0,
                    places=8
                )

                self.assertAlmostEqual(
                    risk.calculate_rr(
                        entry, stop,
                        targets["trail_start"],
                        direction
                    ),
                    3.0,
                    places=8
                )


    def test_partial_tp_quantity(self):

        for symbol in trader.SYMBOLS:

            for direction in ("LONG", "SHORT"):

                with self.subTest(
                    symbol=symbol,
                    direction=direction
                ):

                    order = self.build_test_order(
                        symbol=symbol,
                        direction=direction
                    )

                    size = Decimal(order["size"])
                    tp1 = Decimal(order["target_size"])

                    step = Decimal(
                        self.contracts[symbol]["size_step"]
                    )

                    minimum = Decimal(
                        self.contracts[symbol]["min_size"]
                    )

                    self.assertGreaterEqual(tp1, minimum)
                    self.assertGreaterEqual(size - tp1, minimum)

                    self.assertEqual(
                        tp1 % step,
                        Decimal("0")
                    )

                    self.assertLessEqual(
                        tp1,
                        size * Decimal("0.5")
                    )

                    self.assertLess(
                        size * Decimal("0.5") - tp1,
                        step
                    )


    def test_breakeven_requires_tp1_fill(self):

        record = {
            "breakeven_activated": False
        }

        with patch("trader.MODE", "auto_trade"), \
             patch("trader.EXECUTION_ENABLED", True), \
             patch(
                 "trader.confirm_tp1_fill",
                 return_value=False
             ), \
             patch("trader.modify_stop_loss") as modify:

            result = trader.activate_breakeven(
                record, {}
            )

            self.assertFalse(result)
            modify.assert_not_called()


    def test_trailing_requires_breakeven(self):

        record = {
            "tp1_completed": True,
            "breakeven_activated": False
        }

        with patch("trader.MODE", "auto_trade"), \
             patch("trader.EXECUTION_ENABLED", True), \
             patch("trader.modify_stop_loss") as modify:

            result = trader.update_trailing_stop(
                record, {}
            )

            self.assertFalse(result)
            modify.assert_not_called()


    def test_trailing_activation_threshold(self):

        for direction in ("LONG", "SHORT"):

            with self.subTest(direction=direction):

                record = {
                    "symbol": "BTCUSDT",
                    "direction": direction,
                    "remaining_size": 0.005,
                    "tp1_completed": True,
                    "breakeven_activated": True,
                    "trailing_activated": False,
                    "trail_start_price": 61000.0,
                    "status": "BREAKEVEN"
                }

                position = {
                    "symbol": "BTCUSDT",
                    "holdSide": (
                        "long" if direction == "LONG"
                        else "short"
                    ),
                    "total": "0.005",
                    "markPrice": (
                        "60999" if direction == "LONG"
                        else "61001"
                    )
                }

                with patch("trader.MODE", "auto_trade"), \
                     patch("trader.EXECUTION_ENABLED", True), \
                     patch("trader.modify_stop_loss") as modify:

                    self.assertFalse(
                        trader.update_trailing_stop(
                            record, position
                        )
                    )

                    self.assertFalse(
                        record["trailing_activated"]
                    )

                    position["markPrice"] = "61000"

                    self.assertTrue(
                        trader.update_trailing_stop(
                            record, position
                        )
                    )

                    self.assertTrue(
                        record["trailing_activated"]
                    )

                    self.assertEqual(
                        record["status"],
                        "TRAILING"
                    )

                    modify.assert_not_called()


    def test_trailing_stop_long(self):

        improved = trader.calculate_trailing_stop(
            "LONG", 60000, 60500, 100, 61000
        )

        self.assertGreater(improved, 60000)
        self.assertLess(improved, 61000)

        unchanged = trader.calculate_trailing_stop(
            "LONG", 60000, 59500, 100, 61000
        )

        self.assertEqual(unchanged, 60000)


    def test_trailing_stop_short(self):

        improved = trader.calculate_trailing_stop(
            "SHORT", 60000, 59500, 100, 59000
        )

        self.assertLess(improved, 60000)
        self.assertGreater(improved, 59000)

        unchanged = trader.calculate_trailing_stop(
            "SHORT", 60000, 60500, 100, 59000
        )

        self.assertEqual(unchanged, 60000)


    def test_dynamic_management_disabled(self):

        with patch("trader.MODE", "auto_trade"), \
             patch("trader.EXECUTION_ENABLED", False), \
             patch("trader.modify_stop_loss") as modify:

            with self.assertRaises(RuntimeError):

                trader.activate_breakeven({}, {})

            with self.assertRaises(RuntimeError):

                trader.update_trailing_stop({}, {})

            modify.assert_not_called()


    def test_empty_position_risk(self):

        state = {
            "positions": {}
        }

        with patch(
            "trader.load_state",
            return_value=(state, "test-sha")
        ), patch(
            "trader.get_protection_orders",
            return_value=[]
        ):

            result = trader.open_position_risk(
                self.snapshot
            )

            self.assertEqual(result, 0.0)


    def test_unmanaged_position_rejected(self):

        self.snapshot["positions"] = [{
            "symbol": "BTCUSDT",
            "holdSide": "long",
            "total": "0.01"
        }]

        with patch(
            "trader.load_state",
            return_value=({"positions": {}}, "test-sha")
        ), patch(
            "trader.get_protection_orders",
            return_value=[]
        ):

            with self.assertRaisesRegex(
                RuntimeError,
                "Unmanaged position"
            ):

                trader.open_position_risk(
                    self.snapshot
                )


    def test_residual_protection_rejected(self):

        with patch(
            "trader.load_state",
            return_value=({"positions": {}}, "test-sha")
        ), patch(
            "trader.get_protection_orders",
            return_value=[{"orderId": "RESIDUAL"}]
        ):

            with self.assertRaisesRegex(
                RuntimeError,
                "Residual protection orders"
            ):

                trader.open_position_risk(
                    self.snapshot
                )


    def test_multiple_position_risk(self):

        records = {}
        positions = []
        expected_risk = 0.0

        for symbol, entry, stop, size in (
            ("BTCUSDT", 60000, 59800, 0.01),
            ("ETHUSDT", 1700, 1690, 0.1)
        ):

            records[symbol + ":LONG"] = {
                "status": "ACTIVE",
                "current_stop": stop,
                "remaining_size": size,
                "tp1_size": size * 0.5,
                "tp1_price": entry * 1.02,
                "stop_order_ids": ["STOP-" + symbol],
                "target_order_ids": ["TP-" + symbol]
            }

            positions.append({
                "symbol": symbol,
                "holdSide": "long",
                "total": str(size),
                "openPriceAvg": str(entry),
                "unrealizedPL": "0"
            })

            expected_risk += size * risk.risk_per_unit(
                entry, stop
            )

        self.snapshot["positions"] = positions

        def mock_verify(order):

            symbol = order["symbol"]

            return {
                "verified": True,
                "stop_ids": ["STOP-" + symbol],
                "target_ids": ["TP-" + symbol]
            }

        with patch(
            "trader.load_state",
            return_value=(
                {"positions": records},
                "test-sha"
            )
        ), patch(
            "trader.get_protection_orders",
            return_value=[]
        ), patch(
            "trader.verify_protection",
            side_effect=mock_verify
        ):

            result = trader.open_position_risk(
                self.snapshot
            )

            self.assertAlmostEqual(
                result,
                expected_risk,
                places=8
            )
    def test_missing_market_target(self):
        signal = self.make_signal("BTCUSDT", "LONG", "A")
        signal.pop("market_target", None)

        with self.assertRaisesRegex(
            RuntimeError,
            "Missing market profit space validation"
        ):
            trader.build_order(signal, self.snapshot, 5.0)

    def test_insufficient_long_market_space(self):
        signal = self.make_signal("BTCUSDT", "LONG", "A")
        signal["market_target"] = signal["entry"] + 1

        with self.assertRaisesRegex(
            RuntimeError,
            "Target exceeds market profit space"
        ):
            trader.build_order(signal, self.snapshot, 5.0)

    def test_insufficient_short_market_space(self):
        signal = self.make_signal("BTCUSDT", "SHORT", "A")
        signal["market_target"] = signal["entry"] - 1

        with self.assertRaisesRegex(
            RuntimeError,
            "Target exceeds market profit space"
        ):
            trader.build_order(signal, self.snapshot, 5.0)
    def test_stop_modification_success(self):

        for plan_type in ("loss_plan", "pos_loss"):

            with self.subTest(plan_type=plan_type):

                record = {
                    "symbol": "BTCUSDT",
                    "direction": "LONG",
                    "breakeven_activated": False,
                    "breakeven_price": 60150.0,
                    "current_stop": 59800.0,
                    "remaining_size": 0.01,
                    "stop_order_ids": ["STOP-1"],
                    "status": "ACTIVE"
                }

                position = {
                    "symbol": "BTCUSDT",
                    "holdSide": "long",
                    "total": "0.005",
                    "markPrice": "61000"
                }

                plans = [{
                    "symbol": "BTCUSDT",
                    "holdSide": "long",
                    "planStatus": "live",
                    "tradeSide": "close",
                    "planType": plan_type,
                    "orderId": "STOP-1",
                    "triggerType": "fill_price",
                    "triggerPrice": "59800"
                }]

                with patch("trader.MODE", "auto_trade"), \
                     patch("trader.EXECUTION_ENABLED", True), \
                     patch("trader.confirm_tp1_fill", return_value=True), \
                     patch("trader.get_protection_orders", return_value=plans), \
                     patch("trader.modify_stop_loss", return_value={"orderId": "STOP-1"}) as modify, \
                     patch("trader.verify_protection", return_value={
                         "verified": True,
                         "stop_ids": ["STOP-1"]
                     }):

                    result = trader.activate_breakeven(
                        record, position
                    )

                    self.assertTrue(result)

                    modify.assert_called_once_with(
                        "BTCUSDT",
                        "LONG",
                        "STOP-1",
                        59800.0,
                        "60150.0",
                        0.005,
                        plan_type
                    )

                    self.assertEqual(
                        record["remaining_size"], 0.005
                    )

                    self.assertEqual(
                        record["current_stop"], 60150.0
                    )

                    self.assertEqual(
                        record["status"], "BREAKEVEN"
                    )

                    self.assertTrue(
                        record["breakeven_activated"]
                    )

    def test_stop_modification_failure(self):

        record = {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "breakeven_activated": False,
            "breakeven_price": 60150.0,
            "current_stop": 59800.0,
            "remaining_size": 0.01,
            "stop_order_ids": ["STOP-1"],
            "status": "ACTIVE"
        }

        position = {
            "symbol": "BTCUSDT",
            "holdSide": "long",
            "total": "0.005",
            "markPrice": "61000"
        }

        plans = [{
            "symbol": "BTCUSDT",
            "holdSide": "long",
            "planStatus": "live",
            "tradeSide": "close",
            "planType": "loss_plan",
            "orderId": "STOP-1",
            "triggerType": "fill_price",
            "triggerPrice": "59800"
        }]

        with patch("trader.MODE", "auto_trade"), \
             patch("trader.EXECUTION_ENABLED", True), \
             patch("trader.confirm_tp1_fill", return_value=True), \
             patch("trader.get_protection_orders", return_value=plans), \
             patch("trader.modify_stop_loss", side_effect=RuntimeError("API rejected")), \
             patch("trader.verify_protection") as verify:

            with self.assertRaisesRegex(
                RuntimeError, "API rejected"
            ):
                trader.activate_breakeven(
                    record, position
                )

            verify.assert_not_called()

            self.assertFalse(
                record["breakeven_activated"]
            )

            self.assertEqual(
                record["current_stop"], 59800.0
            )

            self.assertEqual(
                record["status"], "ACTIVE"
            )

    def test_closed_position_requires_history(self):

        record = {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "initial_size": 0.01,
            "opened_at": "2026-11-27T12:00:00+00:00"
        }

        with patch(
            "trader.get_history_positions",
            return_value={"list": []}
        ):

            with self.assertRaisesRegex(
                RuntimeError,
                "Closed position history not uniquely confirmed"
            ):

                trader.confirm_position_closed(record)
if __name__ == "__main__":

    unittest.main(verbosity=2)
