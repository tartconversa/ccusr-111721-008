import tempfile
import unittest
from pathlib import Path

from exchange import CorrectionError, CorrectionFork, Exchange, NoEffectiveVersion


class ExchangeFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Exchange(Path(self.temp.name) / "exchange.db")
        self.service.catalog.register("flow", "机构甲", "甲地", "restricted", [("count", "通行计数", "integer", False)], "2026-11-01")
        self.service.catalog.ingest("item-1", "flow", {"count": 8}, "2026-11-02")
        self.service.grants.issue("g-1", "flow", "机构乙", "运行协调", "2026-12-31", "2026-11-03")
        self.service.policies.set("甲地", "restricted", "运行协调", "allow", "2026-11-03", "机构甲")
        self.revision = self.service.products.assemble("report", "通行观察", ["item-1"], "2026-11-04", "机构甲")

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_delivery_keeps_revision_and_request_identity(self):
        desk = self.service.delivery
        receipt = desk.send("d-1", "report", self.revision, "机构乙", "运行协调", "2026-11-05")
        self.assertEqual(receipt["product_revision"], self.revision)
        self.assertEqual(receipt, desk.send("d-1", "report", self.revision, "机构乙", "运行协调", "2026-11-05"))
        with self.assertRaises(ValueError):
            desk.send("d-1", "report", self.revision, "其他机构", "运行协调", "2026-11-05")

    def test_grant_and_policy_gate_delivery(self):
        with self.assertRaises(PermissionError):
            self.service.delivery.send("d-2", "report", self.revision, "陌生机构", "运行协调", "2026-11-05")
        self.service.policies.set("甲地", "restricted", "运行协调", "deny", "2026-11-06", "机构甲")
        with self.assertRaises(PermissionError):
            self.service.delivery.send("d-3", "report", self.revision, "机构乙", "运行协调", "2026-11-07")
        self.assertTrue(self.service.history("report"))

    def test_unknown_fields_rejected(self):
        with self.assertRaises(ValueError):
            self.service.catalog.ingest("item-2", "flow", {"unknown": 9}, "2026-11-03")


class CorrectionFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Exchange(Path(self.temp.name) / "exchange.db")
        self.service.catalog.register("flow", "机构甲", "甲地", "restricted", [("count", "通行计数", "integer", False)], "2026-11-01")
        self.service.catalog.ingest("item-1", "flow", {"count": 8}, "2026-11-02")
        self.service.grants.issue("g-1", "flow", "机构乙", "运行协调", "2026-12-31", "2026-11-03")
        self.service.policies.set("甲地", "restricted", "运行协调", "allow", "2026-11-03", "机构甲")
        self.revision = self.service.products.assemble("report", "通行观察", ["item-1"], "2026-11-04", "机构甲")
        self.receipt = self.service.delivery.send("d-1", "report", self.revision, "机构乙", "运行协调", "2026-11-05")

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def withdraw_and_correct(self):
        self.service.catalog.withdraw("item-1", "2026-11-06", "机构甲")
        self.service.catalog.correct("item-2", "item-1", {"count": 9}, "2026-11-06")

    def test_orchestration_switches_to_effective_version(self):
        self.withdraw_and_correct()
        revision = self.service.products.assemble("report", "通行观察", ["item-1"], "2026-11-07", "机构甲")
        inputs = self.service.products.inputs("report", revision)
        self.assertEqual([row["id"] for row in inputs], ["item-2"])
        self.assertEqual(inputs[0]["requested_asset_id"], "item-1")
        receipt = self.service.delivery.send("d-2", "report", revision, "机构乙", "运行协调", "2026-11-07")
        self.assertEqual(receipt["product_revision"], revision)

    def test_old_revision_no_longer_deliverable_after_correction(self):
        self.withdraw_and_correct()
        with self.assertRaises(PermissionError) as caught:
            self.service.delivery.send("d-2", "report", self.revision, "机构乙", "运行协调", "2026-11-07")
        reasons = caught.exception.args[0]
        self.assertEqual(reasons[0]["reason"], "superseded")
        self.assertEqual(reasons[0]["effective"], "item-2")

    def test_delivered_receipt_keeps_inputs_and_marks_later_correction(self):
        self.withdraw_and_correct()
        view = self.service.delivery.receipt("d-1")
        self.assertEqual(view["delivery"]["input_digest"], self.receipt["input_digest"])
        self.assertEqual(len(view["inputs"]), 1)
        original = view["inputs"][0]
        self.assertEqual(original["asset"], "item-1")
        self.assertEqual(original["state"], "withdrawn")
        self.assertTrue(original["later_corrected"])
        self.assertEqual(original["effective"], "item-2")
        self.assertIsNone(original["issue"])

    def test_rejected_correction_of_withdrawn_original_leaves_no_trusted_input(self):
        self.withdraw_and_correct()
        self.service.catalog.reject("item-2", "2026-11-07", "机构甲")
        with self.assertRaises(NoEffectiveVersion):
            self.service.catalog.effective("item-1")
        with self.assertRaises(CorrectionError):
            self.service.products.assemble("report", "通行观察", ["item-1"], "2026-11-08", "机构甲")
        with self.assertRaises(PermissionError) as caught:
            self.service.delivery.send("d-2", "report", self.revision, "机构乙", "运行协调", "2026-11-08")
        self.assertEqual(caught.exception.args[0][0]["reason"], "unresolved")
        view = self.service.delivery.receipt("d-1")
        self.assertFalse(view["inputs"][0]["later_corrected"])
        self.assertIsNotNone(view["inputs"][0]["issue"])

    def test_rejected_correction_leaves_active_original_authoritative(self):
        self.service.catalog.correct("item-2", "item-1", {"count": 9}, "2026-11-06")
        self.service.catalog.reject("item-2", "2026-11-07", "机构甲")
        self.assertEqual(self.service.catalog.effective("item-1"), "item-1")
        revision = self.service.products.assemble("report", "通行观察", ["item-1"], "2026-11-08", "机构甲")
        self.assertEqual([row["id"] for row in self.service.products.inputs("report", revision)], ["item-1"])

    def test_correction_fork_is_not_silently_resolved(self):
        self.service.catalog.withdraw("item-1", "2026-11-06", "机构甲")
        self.service.catalog.correct("item-2", "item-1", {"count": 9}, "2026-11-06")
        self.service.catalog.correct("item-3", "item-1", {"count": 10}, "2026-11-06")
        with self.assertRaises(CorrectionFork):
            self.service.catalog.effective("item-1")
        with self.assertRaises(CorrectionError):
            self.service.products.assemble("report", "通行观察", ["item-1"], "2026-11-07", "机构甲")
        # 明确否决其中一支后，链条才恢复唯一可信版本
        self.service.catalog.reject("item-3", "2026-11-08", "机构甲")
        self.assertEqual(self.service.catalog.effective("item-1"), "item-2")

    def test_correction_chain_follows_multiple_generations(self):
        self.withdraw_and_correct()
        self.service.catalog.withdraw("item-2", "2026-11-07", "机构甲")
        self.service.catalog.correct("item-3", "item-2", {"count": 11}, "2026-11-07")
        self.assertEqual(self.service.catalog.effective("item-1"), "item-3")
        revision = self.service.products.assemble("report", "通行观察", ["item-1"], "2026-11-08", "机构甲")
        self.assertEqual([row["id"] for row in self.service.products.inputs("report", revision)], ["item-3"])

    def test_correction_validation(self):
        with self.assertRaises(KeyError):
            self.service.catalog.correct("item-2", "missing", {"count": 9}, "2026-11-06")
        with self.assertRaises(ValueError):
            self.service.catalog.correct("item-1", "item-1", {"count": 9}, "2026-11-06")
        with self.assertRaises(ValueError):
            self.service.catalog.correct("item-2", "item-1", {"unknown": 9}, "2026-11-06")
        self.withdraw_and_correct()
        self.service.catalog.reject("item-2", "2026-11-07", "机构甲")
        with self.assertRaises(ValueError):
            self.service.catalog.correct("item-3", "item-2", {"count": 10}, "2026-11-08")

    def test_withdraw_and_reject_state_machine(self):
        self.service.catalog.withdraw("item-1", "2026-11-06", "机构甲")
        with self.assertRaises(ValueError):
            self.service.catalog.withdraw("item-1", "2026-11-07", "机构甲")
        with self.assertRaises(ValueError):
            self.service.catalog.reject("item-1", "2026-11-07", "机构甲")
        with self.assertRaises(KeyError):
            self.service.catalog.withdraw("missing", "2026-11-07", "机构甲")

    def test_impact_traces_records_products_and_deliveries(self):
        self.withdraw_and_correct()
        revision = self.service.products.assemble("report", "通行观察", ["item-1"], "2026-11-07", "机构甲")
        self.service.delivery.send("d-2", "report", revision, "机构乙", "运行协调", "2026-11-07")
        impact = self.service.impact("item-1")
        self.assertEqual(impact["asset"]["id"], "item-1")
        self.assertEqual(impact["chain"]["effective"], "item-2")
        self.assertEqual({node["id"] for node in impact["chain"]["nodes"]}, {"item-1", "item-2"})
        roles = {(product["product_id"], product["revision"]): product["role"] for product in impact["products"]}
        self.assertEqual(roles[("report", self.revision)], "input")
        self.assertEqual(roles[("report", revision)], "requested")
        self.assertEqual({delivery["id"] for delivery in impact["deliveries"]}, {"d-1", "d-2"})
        downstream = self.service.impact("item-2")
        self.assertEqual({(p["product_id"], p["revision"]) for p in downstream["products"]}, {("report", revision)})


if __name__ == "__main__":
    unittest.main()
