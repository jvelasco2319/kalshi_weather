"""Tests for selective one-minute historical replay controls."""
from datetime import datetime, timezone
from decimal import Decimal
import unittest

from klax_lab.evaluation import FeeScenario
from klax_lab.market_policy_v3 import (
    SelectiveControls, latest_completed_quote, quote_from_normalized_candle,
    screen_minute_purchase,
)


UTC = timezone.utc


def candle(stamp: int, **overrides):
    row = {
        "ticker": "KXHIGHLAX-FIXTURE", "period_minutes": 1,
        "end_period_ts": stamp, "yes_bid_close": "0.40", "yes_ask_close": "0.42",
        "volume_contracts": "25.00", "evidence_grade": "B_aggregated_quote",
        "historical_depth_available": False, "hypothetical_fill_supported": False,
        "source_sha256": "a" * 64,
        "source_endpoint_type": "historical_market_candlesticks_1m",
    }
    row.update(overrides)
    return row


class MinutePolicyTests(unittest.TestCase):
    def setUp(self):
        self.decision = datetime(2025, 2, 1, 15, tzinfo=UTC)
        self.fees = FeeScenario("fixture", "0.07", "synthetic historical proxy")

    def screen(self, quote=None, controls=None, **overrides):
        arguments = dict(
            decision_id="fixture-decision", information_cutoff=self.decision,
            decision_at=self.decision, yes_probability="0.60", side="YES",
            quote=quote or quote_from_normalized_candle(candle(int(self.decision.timestamp()))),
            controls=controls or SelectiveControls(uncertainty_buffer="0.02"),
            fees=self.fees, dataset_version="fixture-data", model_version="fixture-model",
        )
        arguments.update(overrides)
        return screen_minute_purchase(**arguments)

    def test_latest_completed_quote_never_uses_future_candle(self):
        rows = [candle(int(self.decision.timestamp()) - 60),
                candle(int(self.decision.timestamp()) + 60, yes_ask_close="0.20")]
        selected = latest_completed_quote(rows, "KXHIGHLAX-FIXTURE", self.decision)
        self.assertEqual(selected.observed_at.timestamp(), self.decision.timestamp() - 60)

    def test_uncertainty_buffer_reduces_purchased_probability(self):
        result = self.screen()
        self.assertEqual(result.purchased_probability, Decimal("0.58"))
        self.assertEqual(result.status, "ACCEPTED")
        no = self.screen(side="NO", yes_probability="0.30")
        self.assertEqual(no.purchased_probability, Decimal("0.68"))

    def test_volume_spread_side_and_price_controls_skip(self):
        low_volume = quote_from_normalized_candle(candle(int(self.decision.timestamp()), volume_contracts="1"))
        self.assertEqual(self.screen(quote=low_volume).reason, "MINUTE_VOLUME_BELOW_CONTROL")
        wide = quote_from_normalized_candle(candle(int(self.decision.timestamp()), yes_bid_close="0.20", yes_ask_close="0.42"))
        self.assertEqual(self.screen(quote=wide).reason, "MINUTE_SPREAD_ABOVE_CONTROL")
        no_yes = SelectiveControls(allowed_sides=("NO",), uncertainty_buffer="0.02")
        self.assertEqual(self.screen(controls=no_yes).reason, "SIDE_POLICY")
        expensive = quote_from_normalized_candle(candle(int(self.decision.timestamp()), yes_bid_close="0.94", yes_ask_close="0.96"))
        self.assertEqual(self.screen(quote=expensive).reason, "ENTRY_PRICE_OUTSIDE_CONTROL_BAND")

    def test_boundary_contract_prices_skip_without_undefined_roi(self):
        zero_yes = quote_from_normalized_candle(candle(
            int(self.decision.timestamp()), yes_bid_close="0", yes_ask_close="0"))
        zero_result = self.screen(quote=zero_yes, side="YES")
        self.assertEqual(zero_result.status, "SKIPPED")
        self.assertEqual(zero_result.reason, "ENTRY_PRICE_OUTSIDE_CONTROL_BAND")
        self.assertEqual(zero_result.entry_price, Decimal("0"))
        self.assertEqual(zero_result.entry_outlay, Decimal("0"))
        self.assertEqual(zero_result.expected_net_return, Decimal("0"))

        one_yes = quote_from_normalized_candle(candle(
            int(self.decision.timestamp()), yes_bid_close="1", yes_ask_close="1"))
        one_result = self.screen(quote=one_yes, side="YES")
        self.assertEqual(one_result.status, "SKIPPED")
        self.assertEqual(one_result.reason, "ENTRY_PRICE_OUTSIDE_CONTROL_BAND")
        self.assertEqual(one_result.entry_price, Decimal("1"))
        self.assertEqual(one_result.entry_outlay, Decimal("0"))

        zero_no = self.screen(quote=one_yes, side="NO")
        self.assertEqual(zero_no.entry_price, Decimal("0"))
        self.assertEqual(zero_no.status, "SKIPPED")

    def test_candle_cannot_claim_depth_or_fill(self):
        with self.assertRaisesRegex(ValueError, "promoted"):
            quote_from_normalized_candle(candle(
                int(self.decision.timestamp()), historical_depth_available=True))


if __name__ == "__main__":
    unittest.main()
