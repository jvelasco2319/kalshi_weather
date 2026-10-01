import unittest

from v5b.hypotheses import (COLONIES, blocked_hypotheses, fingerprint,
                            normalize_parameters, propose_epoch, seeds,
                            synthesis, validate_hypothesis)


class HypothesisTests(unittest.TestCase):
    def test_seeds_independent_causal_and_unique(self):
        records = seeds()
        self.assertEqual(len(records), 22)
        self.assertEqual(len({h["fingerprint"] for h in records}), 22)
        self.assertEqual({h["colony"] for h in records}, set(COLONIES))
        for h in records:
            self.assertEqual(validate_hypothesis(h), h)
            self.assertTrue(h["falsification_tests"])

    def test_canonical_effective_parameters(self):
        self.assertEqual(fingerprint({}), fingerprint({"probability_power": 1, "calibration_strength": 999}))
        self.assertEqual(fingerprint({"allowed_grades": ["B", "A"]}), fingerprint({"allowed_grades": ["A", "B", "A"]}))
        self.assertNotEqual(fingerprint({}), fingerprint({"neighbor_smoothing": .1}))

    def test_reject_unsupported_and_nonfinite(self):
        for params in ({"decision_time": "17:00"}, {"probability_power": float("nan")}, {"maximum_entropy": 2}, {"minimum_price_cents": True}):
            with self.assertRaises(ValueError):
                normalize_parameters(params)

    def test_synthesis_compatible_lineage_and_conflict(self):
        records = seeds()
        joined = synthesis(records[2], records[7])
        self.assertEqual(joined["parent_ids"], [records[2]["id"], records[7]["id"]])
        self.assertEqual(joined["parameters"]["neighbor_smoothing"], .25)
        self.assertEqual(joined["parameters"]["allowed_grades"], ["A"])
        self.assertIsNone(synthesis(records[7], records[20]))
        self.assertIsNone(synthesis(records[0], records[1]))

    def test_finite_novelty_and_blocked_denial(self):
        records = seeds()
        pops = {c: [r for r in records if r["colony"] == c] for c in COLONIES}
        first = propose_epoch(pops, [r["fingerprint"] for r in records], 0)
        repeated = propose_epoch(pops, [r["fingerprint"] for r in records + first["proposals"]], 0)
        self.assertEqual(repeated["proposals"], [])
        self.assertEqual(len(repeated["redirects"]), 4)
        self.assertEqual(propose_epoch(pops, [], 4)["proposals"], [])
        for record in blocked_hypotheses():
            with self.assertRaises(ValueError):
                validate_hypothesis(record)


if __name__ == "__main__":
    unittest.main()
