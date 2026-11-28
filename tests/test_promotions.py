"""Promotion manifests and the gate: lab -> demo -> live."""

import json
import tempfile
import unittest
from pathlib import Path

from bot import promotions as promo


class FakeConn:
    """Captures audit writes without a database."""

    def __init__(self):
        self.calls = []

    def execute(self, sql, args=None):
        self.calls.append((sql, args))
        calls = self.calls

        class _Row:
            def fetchone(self):
                return {"id": len(calls)}

        return _Row()


GOOD_DEMO = {"bars": 6000, "window_days": 600, "trades": 12,
              "fee_bps": 10, "slippage_bps": 5}
GOOD_LIVE = {"weeks_demo": 2, "fills": 3, "duplicate_orders": 0,
             "unexplained_rejects": 0, "max_fill_slippage_bps": 12,
             "fee_drift_pct": 4, "demo_over_backtest_pct": 10,
             "max_heartbeat_gap_minutes": 30}


class ParamsHashTests(unittest.TestCase):
    def test_stable_and_order_insensitive(self):
        a = promo.params_hash({"fast": 20, "slow": 50})
        b = promo.params_hash({"slow": 50, "fast": 20})
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("sha256:"))

    def test_changes_with_params(self):
        self.assertNotEqual(promo.params_hash({"fast": 20}), promo.params_hash({"fast": 21}))


class GateTests(unittest.TestCase):
    def test_demo_gate_passes_on_real_evidence(self):
        evidence = {**GOOD_DEMO, "params_hash": promo.params_hash({})}
        checks = promo.demo_entry_checks(evidence, params_hash_expected=promo.params_hash({}))
        self.assertTrue(all(c["ok"] for c in checks), checks)

    def test_demo_gate_blocks_missing_evidence(self):
        checks = promo.demo_entry_checks({})
        self.assertTrue(any(not c["ok"] for c in checks))

    def test_demo_gate_rejects_window_over_cap(self):
        checks = promo.demo_entry_checks({**GOOD_DEMO, "window_days": 1000})
        self.assertFalse(next(c for c in checks if c["name"] == "backtest_window")["ok"])

    def test_params_hash_must_match(self):
        checks = promo.demo_entry_checks({**GOOD_DEMO, "params_hash": "sha256:dead"},
                                          params_hash_expected="sha256:beef")
        self.assertFalse(next(c for c in checks if c["name"] == "params_hash")["ok"])

    def test_live_gate_passes(self):
        checks = promo.live_entry_checks(GOOD_LIVE)
        self.assertTrue(all(c["ok"] for c in checks), checks)

    def test_live_gate_requires_two_weeks(self):
        checks = promo.live_entry_checks({**GOOD_LIVE, "weeks_demo": 1})
        self.assertFalse(next(c for c in checks if c["name"] == "weeks_demo")["ok"])

    def test_live_gate_blocks_demo_beating_backtest(self):
        checks = promo.live_entry_checks({**GOOD_LIVE, "demo_over_backtest_pct": 90})
        self.assertFalse(next(c for c in checks if c["name"] == "not_beating_backtest")["ok"])

    def test_live_gate_blocks_heartbeat_gap(self):
        checks = promo.live_entry_checks({**GOOD_LIVE, "max_heartbeat_gap_minutes": 120})
        self.assertFalse(next(c for c in checks if c["name"] == "heartbeat")["ok"])

    def test_live_gate_blocks_duplicates(self):
        checks = promo.live_entry_checks({**GOOD_LIVE, "duplicate_orders": 1})
        self.assertFalse(next(c for c in checks if c["name"] == "duplicate_orders")["ok"])


class PromoteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_promote_writes_manifest_and_audit(self):
        conn = FakeConn()
        digest = promo.params_hash({"fast": 20})
        manifest, checks, db_id = promo.promote(
            "fast_cross", env="demo", params={"fast": 20}, symbols=["BTCUSDT"],
            evidence={**GOOD_DEMO, "params_hash": digest}, actor="sahil",
            base=self.base, conn=conn,
        )
        self.assertTrue(manifest.active)
        self.assertEqual(manifest.capital, 1000.0)
        path = promo.manifest_path("fast_cross", "demo", self.base)
        self.assertTrue(path.exists())
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["params_hash"], digest)
        self.assertEqual(db_id, 1)
        self.assertIn("INSERT INTO promotions", conn.calls[0][0])

    def test_refuses_failed_gate(self):
        with self.assertRaises(promo.PromotionError):
            promo.promote("x", env="demo", params={}, symbols=["BTCUSDT"],
                          evidence={}, base=self.base)

    def test_force_without_reason_is_refused(self):
        with self.assertRaises(promo.PromotionError):
            promo.promote("x", env="demo", params={}, symbols=["BTCUSDT"],
                          evidence={}, force=True, base=self.base)

    def test_force_with_reason_records_override(self):
        manifest, checks, _ = promo.promote(
            "x", env="demo", params={}, symbols=["BTCUSDT"], evidence={},
            force=True, reason="owner override for a quick demo", base=self.base,
        )
        self.assertTrue(manifest.override)
        self.assertEqual(manifest.override_reason, "owner override for a quick demo")
        self.assertTrue(any(not c["ok"] for c in checks))

    def test_promote_needs_symbols(self):
        with self.assertRaises(promo.PromotionError):
            promo.promote("x", env="demo", params={}, symbols=[],
                          evidence=GOOD_DEMO, base=self.base)


class LoadAndDemoteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        digest = promo.params_hash({})
        promo.promote("swing", env="demo", params={}, symbols=["BTCUSDT"],
                      timeframe="4h", evidence={**GOOD_DEMO, "params_hash": digest},
                      base=self.base)

    def tearDown(self):
        self.tmp.cleanup()

    def test_require_returns_active(self):
        self.assertEqual(promo.require("swing", "demo", self.base).strategy, "swing")

    def test_require_fails_loud_for_unknown(self):
        with self.assertRaises(promo.PromotionError):
            promo.require("does_not_exist", "demo", self.base)

    def test_demote_deactivates_and_audits(self):
        conn = FakeConn()
        manifest = promo.demote("swing", env="demo", reason="diverged",
                                actor="sahil", base=self.base, conn=conn)
        self.assertFalse(manifest.active)
        self.assertEqual(manifest.demote_reason, "diverged")
        self.assertEqual(promo.load_manifests("demo", self.base)[0].active, False)
        self.assertNotIn("swing", promo.active_manifests("demo", self.base))
        self.assertIn("INSERT INTO promotions", conn.calls[0][0])

    def test_demote_unknown_raises(self):
        with self.assertRaises(promo.PromotionError):
            promo.demote("nope", env="demo", base=self.base)

    def test_malformed_manifest_is_loud(self):
        path = promo.manifest_path("broken", "demo", self.base)
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(promo.PromotionError):
            promo.load_manifests("demo", self.base)

    def test_env_mismatch_is_loud(self):
        payload = {"strategy": "sneaky", "env": "live", "symbols": ["BTCUSDT"],
                   "timeframe": "4h", "capital": 1000}
        promo.manifest_path("sneaky", "demo", self.base).write_text(
            json.dumps(payload), encoding="utf-8")
        with self.assertRaises(promo.PromotionError):
            promo.load_manifests("demo", self.base)

    def test_shipped_manifests_are_valid(self):
        for env in promo.ENVS:
            for manifest in promo.load_manifests(env):
                self.assertIn(manifest.env, promo.ENVS)
                self.assertTrue(manifest.symbols)


if __name__ == "__main__":
    unittest.main()
