import unittest

from unittest.mock import patch

import protection


class ProtectionTests(unittest.TestCase):

    def setUp(self):

        self.order = {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "size": "0.01",
            "price": "60000",
            "stop": "59800",
            "target": "60800"
        }

        self.position = {
            "symbol": "BTCUSDT",
            "holdSide": "long",
            "total": "0.01"
        }

        self.stop = {
            "symbol": "BTCUSDT",
            "holdSide": "long",
            "planStatus": "live",
            "tradeSide": "close",
            "orderId": "STOP-001",
            "planType": "loss_plan",
            "triggerPrice": "59800",
            "size": "0.01"
        }

        self.target = {
            "symbol": "BTCUSDT",
            "holdSide": "long",
            "planStatus": "live",
            "tradeSide": "close",
            "orderId": "TARGET-001",
            "planType": "profit_plan",
            "triggerPrice": "60800",
            "size": "0.01"
        }

        sleep_patch = patch(
            "protection.time.sleep"
        )

        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

        print_patch = patch(
            "builtins.print"
        )

        print_patch.start()
        self.addCleanup(print_patch.stop)

    def verify(self, plans, positions=None):

        if positions is None:
            positions = [self.position]

        with patch(
            "protection.get_active_positions",
            return_value=positions
        ):

            with patch(
                "protection.get_protection_orders",
                return_value=plans
            ):

                return protection.verify_protection(
                    self.order
                )

    def test_valid_protection(self):

        result = self.verify([
            self.stop,
            self.target
        ])

        self.assertTrue(result["verified"])
        self.assertTrue(result["stop_verified"])
        self.assertTrue(result["target_verified"])

    def test_missing_stop(self):

        result = self.verify([
            self.target
        ])

        self.assertFalse(result["verified"])

    def test_missing_target(self):

        result = self.verify([
            self.stop
        ])

        self.assertFalse(result["verified"])

    def test_missing_all_protection(self):

        result = self.verify([])

        self.assertFalse(result["verified"])

    def test_wrong_stop_price(self):

        self.stop["triggerPrice"] = "59700"

        result = self.verify([
            self.stop,
            self.target
        ])

        self.assertFalse(result["verified"])

    def test_wrong_target_price(self):

        self.target["triggerPrice"] = "61000"

        result = self.verify([
            self.stop,
            self.target
        ])

        self.assertFalse(result["verified"])

    def test_wrong_position_side(self):

        self.stop["holdSide"] = "short"

        result = self.verify([
            self.stop,
            self.target
        ])

        self.assertFalse(result["verified"])

    def test_wrong_symbol(self):

        self.stop["symbol"] = "ETHUSDT"

        result = self.verify([
            self.stop,
            self.target
        ])

        self.assertFalse(result["verified"])

    def test_inactive_stop(self):

        self.stop["planStatus"] = "cancelled"

        result = self.verify([
            self.stop,
            self.target
        ])

        self.assertFalse(result["verified"])

    def test_not_close_order(self):

        self.stop["tradeSide"] = "open"

        result = self.verify([
            self.stop,
            self.target
        ])

        self.assertFalse(result["verified"])

    def test_insufficient_stop_size(self):

        self.stop["size"] = "0.005"

        result = self.verify([
            self.stop,
            self.target
        ])

        self.assertFalse(result["verified"])

    def test_insufficient_target_size(self):

        self.target["size"] = "0.005"

        result = self.verify([
            self.stop,
            self.target
        ])

        self.assertFalse(result["verified"])

    def test_duplicate_order_id(self):

        self.stop["size"] = "0.005"

        result = self.verify([
            self.stop,
            dict(self.stop),
            self.target
        ])

        self.assertFalse(result["verified"])

    def test_split_stop_orders(self):

        self.stop["size"] = "0.004"

        second_stop = dict(self.stop)

        second_stop["orderId"] = "STOP-002"
        second_stop["size"] = "0.006"

        result = self.verify([
            self.stop,
            second_stop,
            self.target
        ])

        self.assertTrue(result["verified"])

    def test_position_size_mismatch(self):

        self.position["total"] = "0.02"

        result = self.verify([
            self.stop,
            self.target
        ])

        self.assertFalse(result["verified"])

    def test_missing_position(self):

        result = self.verify(
            [self.stop, self.target],
            positions=[]
        )

        self.assertFalse(result["verified"])

    def test_ambiguous_position(self):

        result = self.verify(
            [self.stop, self.target],
            positions=[
                self.position,
                dict(self.position)
            ]
        )

        self.assertFalse(result["verified"])

    def test_position_level_protection(self):

        self.stop["planType"] = "pos_loss"
        self.target["planType"] = "pos_profit"

        self.stop["size"] = ""
        self.target["size"] = ""

        result = self.verify([
            self.stop,
            self.target
        ])

        self.assertTrue(result["verified"])

    def test_short_protection(self):

        self.order["direction"] = "SHORT"
        self.order["stop"] = "60200"
        self.order["target"] = "59200"

        self.position["holdSide"] = "short"

        self.stop["holdSide"] = "short"
        self.stop["triggerPrice"] = "60200"

        self.target["holdSide"] = "short"
        self.target["triggerPrice"] = "59200"

        result = self.verify([
            self.stop,
            self.target
        ])

        self.assertTrue(result["verified"])

    def test_delayed_protection(self):

        with patch(
            "protection.get_active_positions",
            return_value=[self.position]
        ):

            with patch(
                "protection.get_protection_orders",
                side_effect=[
                    [],
                    [self.stop],
                    [self.stop, self.target]
                ]
            ) as mock_plans:

                result = protection.verify_protection(
                    self.order
                )

                self.assertTrue(result["verified"])

                self.assertEqual(
                    mock_plans.call_count,
                    3
                )

    def test_null_protection_response(self):

        with patch(
            "protection.demo_get",
            return_value={
                "entrustedList": None,
                "endId": None
            }
        ):

            result = protection.get_protection_orders(
                "BTCUSDT"
            )

            self.assertEqual(result, [])

    def test_pos_side_normalization(self):

        plan = dict(self.stop)

        del plan["holdSide"]

        plan["posSide"] = "long"

        with patch(
            "protection.demo_get",
            return_value={
                "entrustedList": [plan]
            }
        ):

            result = protection.get_protection_orders(
                "BTCUSDT"
            )

            self.assertEqual(
                result[0]["holdSide"],
                "long"
            )

    def test_conflicting_position_sides(self):

        plan = dict(self.stop)

        plan["posSide"] = "short"

        with patch(
            "protection.demo_get",
            return_value={
                "entrustedList": [plan]
            }
        ):

            with self.assertRaises(RuntimeError):

                protection.get_protection_orders(
                    "BTCUSDT"
                )

    def test_invalid_api_response(self):

        with patch(
            "protection.demo_get",
            return_value=[]
        ):

            with self.assertRaises(RuntimeError):

                protection.get_protection_orders(
                    "BTCUSDT"
                )

    def test_api_failure(self):

        with patch(
            "protection.get_active_positions",
            side_effect=RuntimeError("API unavailable")
        ):

            with self.assertRaises(RuntimeError):

                protection.verify_protection(
                    self.order
                )

    def test_invalid_direction(self):

        self.order["direction"] = "INVALID"

        with self.assertRaises(RuntimeError):

            self.verify([
                self.stop,
                self.target
            ])

    
    def test_base_volume_compatibility(self):

        self.stop["baseVolume"] = self.stop.pop("size")

        self.target["baseVolume"] = self.target.pop("size")

        result = self.verify([
            self.stop,
            self.target
        ])

        self.assertTrue(result["verified"])

    
    def test_excessive_stop_size(self):

        self.stop["size"] = "0.02"

        result = self.verify([
            self.stop,
            self.target
        ])

        self.assertFalse(result["verified"])

    
    def test_excessive_target_size(self):

        self.target["size"] = "0.02"

        result = self.verify([
            self.stop,
            self.target
        ])

        self.assertFalse(result["verified"])


if __name__ == "__main__":

    unittest.main(verbosity=2)
