"""Effective rates (scripts/tariffdb/rates.py): AER rates provisional, distributor rates final.

The synthetic cases build a tiny database in memory and run the same code the tariffdb build runs; the rest check
the committed tables and report."""
import csv
import io
import sys
import unittest
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "tariffdb"))
sys.path.insert(0, str(ROOT / "scripts"))
import rates  # noqa: E402
import spec  # noqa: E402


class Fixture:
    """A minimal database: documents, tariffs, listings and charges, in the shape the build keeps in memory."""

    def __init__(self):
        self.t = {n: [] for n in ("source_document", "document_coverage", "tariff", "tariff_listing", "charge",
                                  "listing_flag", "price_adjustment", "price_adjustment_tariff")}

    def doc(self, doc_id, author, status, published, did="dist", fy="2025-26", seq=1, recon_side=None):
        self.t["source_document"].append({
            "document_id": doc_id, "author": author, "price_status": status, "fin_year": fy, "version_seq": seq,
            "version_label": f"v{seq}", "publication_date": published, "publication_date_basis": "printed",
            "retrieved_on": None, "retrieved_on_basis": None, "local_path": f"sources/{doc_id}.pdf",
            "recon_side": recon_side or ("AER" if author == "AER" else "DNSP"),
            "document_type": "aer_consolidated_stakeholder_report" if author == "AER" else "network_price_list"})
        self.t["document_coverage"].append({"document_id": doc_id, "distributor_id": did, "price_status": status})
        return self

    def price(self, doc_id, code, label, value, unit="c/kWh", charge_type="energy", did="dist", band="anytime",
              metering="no", lfit="no"):
        tid = f"{did}:{code}"
        if tid not in {t["tariff_id"] for t in self.t["tariff"]}:
            self.t["tariff"].append({"tariff_id": tid, "distributor_id": did, "tariff_code": code})
        lid = f"{doc_id}/{code}"
        if lid not in {x["listing_id"] for x in self.t["tariff_listing"]}:
            self.t["tariff_listing"].append({"listing_id": lid, "tariff_id": tid, "document_id": doc_id,
                                             "price_availability": "priced", "region": None})
        self.t["charge"].append({
            "charge_id": f"{lid}/{rates.slug(label)}", "listing_id": lid, "component_label": label,
            "unit_published": unit, "unit_interpreted": unit, "value_published": value, "value_num": value,
            "value_std": value, "unit_std": unit, "charge_type": charge_type, "time_band": band, "season": None,
            "gst": "excl", "note": None, "price_basis": "NUoS", "includes_metering": metering, "includes_lfit": lfit,
            "effective_from": "2025-07-01", "effective_to": "2026-06-30"})
        return self

    def adjustment(self, kind, code, delta, did="dist", fy="2025-26"):
        aid = f"{kind}/{did}/{fy}"
        self.t["price_adjustment"].append({"adjustment_id": aid, "kind": kind, "distributor_id": did, "fin_year": fy})
        self.t["price_adjustment_tariff"].append({"adjustment_id": aid, "tariff_id": f"{did}:{code}",
                                                  "expected_delta_std": delta})
        return self

    def run(self):
        out = rates.compute(lambda name: self.t[name])
        self.history = {r["rate_id"]: r for r in out["rate_history"]}
        self.effective = {r["component_id"]: r for r in out["effective_rate"]}
        return self

    def rate(self, doc_id, code, label):
        return self.history[f"{doc_id}/{code}/{rates.slug(label)}"]

    def answer(self, code, label, as_of=None, did="dist"):
        comp = next(r["component_id"] for r in self.history.values()
                    if r["tariff_id"] == f"{did}:{code}" and r["charge_id"].endswith("/" + rates.slug(label)))
        if as_of is None:
            return self.effective[comp]
        return rates.resolve(list(self.history.values()), as_of)[comp]


class TestProvisionalToFinal(unittest.TestCase):
    def fixture(self):
        return (Fixture()
                .doc("aer-v1", "AER", "proposed", "2025-04-08", seq=1)
                .doc("aer-v5", "AER", "approved", "2025-06-06", seq=5)
                .doc("dist-list", "dist", "published", "2025-06-20")
                .price("aer-v1", "T1", "Peak", "10.0").price("aer-v5", "T1", "Peak", "10.5")
                .price("dist-list", "T1", "Peak", "10.5")
                .price("aer-v1", "T1", "Off peak", "4.000").price("aer-v5", "T1", "Off peak", "4.000")
                .price("dist-list", "T1", "Off peak", "4.2").run())

    def test_distributor_rate_replaces_the_aer_rate(self):
        f = self.fixture()
        e = f.answer("T1", "Peak")
        self.assertEqual((e["status"], e["value_std"], e["document_id"], e["source_side"]),
                         ("final", "10.5", "dist-list", "distributor"))
        self.assertEqual(e["replaced_rate_id"], f.rate("aer-v5", "T1", "Peak")["rate_id"])
        self.assertEqual(e["validation_status"], "match")
        # every rate is kept, the chain v1 -> v5 -> distributor, and only the final one is current
        v1, v5, d = f.rate("aer-v1", "T1", "Peak"), f.rate("aer-v5", "T1", "Peak"), f.rate("dist-list", "T1", "Peak")
        self.assertEqual((v1["superseded_by"], v5["superseded_by"], d["superseded_by"]), (v5["rate_id"], d["rate_id"],
                                                                                          None))
        self.assertEqual((v1["is_current"], v5["is_current"], d["is_current"]), (0, 0, 1))
        self.assertEqual((v1["role"], v5["role"], d["role"]), ("provisional", "provisional", "final"))

    def test_every_aer_version_is_validated(self):
        f = self.fixture()
        v1 = f.rate("aer-v1", "T1", "Peak")
        self.assertEqual((v1["validation_status"], v1["delta_std"], v1["validated_against"]),
                         ("mismatch", "0.5", f.rate("dist-list", "T1", "Peak")["rate_id"]))
        self.assertEqual(f.rate("aer-v5", "T1", "Peak")["validation_status"], "match")

    def test_as_of_answers_with_the_documents_known_then(self):
        f = self.fixture()
        self.assertEqual(rates.resolve(list(f.history.values()), "2025-04-07"), {})
        before = f.answer("T1", "Peak", "2025-05-01")
        self.assertEqual((before["status"], before["current"]["document_id"], before["validation"]),
                         ("provisional", "aer-v1", "pending"))
        approved = f.answer("T1", "Peak", "2025-06-06")
        self.assertEqual((approved["status"], approved["current"]["document_id"]), ("provisional", "aer-v5"))
        after = f.answer("T1", "Peak", "2025-06-20")
        self.assertEqual((after["status"], after["current"]["value_std"]), ("final", "10.5"))

    def test_a_mismatch_is_recorded_with_its_difference(self):
        f = self.fixture()
        e = f.answer("T1", "Off peak")
        self.assertEqual((e["status"], e["value_std"], e["validation_status"]), ("final", "4.2", "mismatch"))
        self.assertEqual(e["delta_std"], "0.2")
        self.assertEqual(f.rate("aer-v5", "T1", "Off peak")["delta_std"], "0.2")


class TestValidationRules(unittest.TestCase):
    def test_published_rounding_is_a_match(self):
        f = (Fixture().doc("aer", "AER", "approved", "2025-06-01", seq=5).doc("d", "dist", "published", "2025-06-20")
             .price("aer", "T1", "Peak", "12.3456").price("d", "T1", "Peak", "12.35").run())
        self.assertEqual(f.answer("T1", "Peak")["validation_status"], "match_within_rounding")

    def test_metering_adder_is_a_match_after_adjustment(self):
        f = (Fixture().doc("aer", "AER", "approved", "2025-06-01", seq=5).doc("d", "dist", "published", "2025-06-20")
             .adjustment("metering_adder", "T1", "7.5")
             .price("aer", "T1", "Supply", "50.0", unit="c/day", charge_type="fixed")
             .price("d", "T1", "Supply", "57.5", unit="c/day", charge_type="fixed", metering="yes").run())
        e = f.answer("T1", "Supply")
        self.assertEqual((e["status"], e["validation_status"], e["adjustment_id"]),
                         ("final", "match_after_adjustment", "metering_adder/dist/2025-26"))
        self.assertEqual(f.rate("aer", "T1", "Supply")["expected_delta_std"], "7.5")

    def test_a_wrong_adjustment_amount_is_a_mismatch(self):
        f = (Fixture().doc("aer", "AER", "approved", "2025-06-01", seq=5).doc("d", "dist", "published", "2025-06-20")
             .adjustment("lfit_adder", "T1", "2.27")
             .price("aer", "T1", "Peak", "10.000").price("d", "T1", "Peak", "13.000", lfit="yes").run())
        r = f.rate("aer", "T1", "Peak")
        self.assertEqual((r["validation_status"], r["delta_std"]), ("mismatch", "3"))
        self.assertIn("lfit_adder/dist/2025-26 predicts 2.27", r["validation_note"])


class TestOneSourceComponents(unittest.TestCase):
    def fixture(self, published=True):
        f = (Fixture().doc("aer", "AER", "approved", "2025-06-01", seq=5)
             .price("aer", "T1", "Peak", "10.0").price("aer", "T1", "Demand", "30.0", unit="c/kW/day",
                                                       charge_type="demand")
             .price("aer", "T2", "Peak", "8.0"))
        if published:
            (f.doc("d", "dist", "published", "2025-06-20").price("d", "T1", "Peak", "10.0")
             .price("d", "T1", "Solar export", "-1.0").price("d", "T3", "Peak", "9.0"))
        return f.run()

    def test_distributor_only_components_and_tariffs_are_final_and_flagged(self):
        f = self.fixture()
        comp, tariff = f.answer("T1", "Solar export"), f.answer("T3", "Peak")
        self.assertEqual((comp["status"], comp["validation_status"], comp["only_in"]),
                         ("final", "distributor_only", "distributor_component"))
        self.assertEqual((tariff["status"], tariff["validation_status"], tariff["only_in"]),
                         ("final", "distributor_only", "distributor_tariff"))
        self.assertTrue(comp["component_id"].split("|")[2].startswith("dnsp:"))

    def test_aer_only_components_and_tariffs_stay_provisional_and_flagged(self):
        f = self.fixture()
        comp, tariff = f.answer("T1", "Demand"), f.answer("T2", "Peak")
        self.assertEqual((comp["status"], comp["validation_status"], comp["only_in"], comp["value_std"]),
                         ("provisional", "aer_only", "aer_component", "30.0"))
        self.assertEqual((tariff["status"], tariff["only_in"]), ("provisional", "aer_tariff"))
        self.assertEqual(f.rate("aer", "T2", "Peak")["validation_status"], "aer_only")

    def test_before_the_distributor_publishes_nothing_is_one_source(self):
        f = self.fixture(published=False)
        for code, label in (("T1", "Peak"), ("T1", "Demand"), ("T2", "Peak")):
            e = f.answer(code, label)
            self.assertEqual((e["status"], e["validation_status"], e["only_in"]), ("provisional", "pending", None))

    def test_a_component_the_approved_version_drops_is_dropped(self):
        f = (Fixture().doc("v1", "AER", "proposed", "2025-04-08", seq=1).doc("v5", "AER", "approved", "2025-06-01",
                                                                             seq=5)
             .price("v1", "T1", "Peak", "10.0").price("v1", "T1", "Shoulder", "6.0").price("v5", "T1", "Peak", "10.0")
             .run())
        e = f.answer("T1", "Shoulder")
        self.assertEqual((e["status"], e["value_std"], e["rate_id"], e["document_id"]), ("dropped", None, None, "v1"))
        self.assertEqual(f.answer("T1", "Shoulder", "2025-05-01")["status"], "provisional")


class TestWaitRule(unittest.TestCase):
    def fixture(self, did):
        return (Fixture().doc("v1", "AER", "proposed", "2025-04-08", did=did, seq=1)
                .doc("v5", "AER", "approved", "2025-06-06", did=did, seq=5)
                .doc("d", "dist", "published", "2025-06-20", did=did)
                .price("v1", "T1", "Peak", "11.0", did=did).price("v5", "T1", "Peak", "10.0", did=did)
                .price("d", "T1", "Peak", "10.0", did=did).run())

    def test_jemena_and_power_and_water_wait_for_approved_prices(self):
        self.assertEqual(set(rates.WAIT_FOR_APPROVED), {"jemena", "powerwater"})
        for did in rates.WAIT_FOR_APPROVED:
            f = self.fixture(did)
            v1 = f.rate("v1", "T1", "Peak")
            self.assertEqual(v1["role"], "withheld", did)
            self.assertEqual(f.answer("T1", "Peak", "2025-05-01", did)["status"], "awaiting_approval", did)
            self.assertIsNone(f.answer("T1", "Peak", "2025-05-01", did)["current"], did)
            approved = f.answer("T1", "Peak", "2025-06-10", did)
            self.assertEqual((approved["status"], approved["current"]["document_id"]), ("provisional", "v5"), did)
            self.assertEqual(f.answer("T1", "Peak", did=did)["status"], "final", did)
            # the withheld rate is still validated, and v1 points at the next usable rate
            self.assertEqual((v1["validation_status"], v1["superseded_by"]),
                             ("mismatch", f.rate("v5", "T1", "Peak")["rate_id"]), did)

    def test_other_distributors_use_v1_at_once(self):
        f = self.fixture("ausgrid")
        self.assertEqual(f.rate("v1", "T1", "Peak")["role"], "provisional")
        e = f.answer("T1", "Peak", "2025-05-01", "ausgrid")
        self.assertEqual((e["status"], e["current"]["document_id"]), ("provisional", "v1"))

    def test_distributor_list_before_any_approved_version_is_final(self):
        f = (Fixture().doc("v1", "AER", "proposed", "2025-04-08", did="jemena", seq=1)
             .doc("d", "dist", "published", "2025-05-20", did="jemena")
             .price("v1", "T1", "Peak", "11.0", did="jemena").price("d", "T1", "Peak", "10.0", did="jemena").run())
        e = f.answer("T1", "Peak", did="jemena")
        self.assertEqual((e["status"], e["value_std"], e["replaced_rate_id"], e["validation_status"]),
                         ("final", "10.0", None, "mismatch"))


def table(name):
    with open(ROOT / "data" / "tariffdb" / "tables" / f"{name}.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class TestCommittedTables(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.history = table("rate_history")
        cls.effective = table("effective_rate")
        cls.docs = {d["document_id"]: d for d in table("source_document")}

    def test_one_effective_rate_per_component_from_its_history(self):
        by_rate = {r["rate_id"]: r for r in self.history}
        current = [r for r in self.history if r["is_current"] == "1"]
        self.assertEqual(len(current), sum(1 for e in self.effective if e["rate_id"]))
        self.assertEqual(Counter(r["component_id"] for r in current).most_common(1)[0][1], 1)
        for e in self.effective:
            if e["status"] in ("final", "provisional"):
                r = by_rate[e["rate_id"]]
                self.assertEqual((r["component_id"], r["value_std"], r["is_current"]),
                                 (e["component_id"], e["value_std"], "1"))
                self.assertEqual(r["role"], e["status"])

    def test_final_rates_are_the_distributors_own_published_lists(self):
        for e in self.effective:
            d = self.docs[e["document_id"]]
            if e["status"] == "final":
                self.assertEqual((d["author"] != "AER", d["price_status"], e["source_side"]),
                                 (True, "published", "distributor"), e["component_id"])
            elif e["status"] == "provisional":
                self.assertIn(e["source_side"], ("aer", "aer_hosted"))

    def test_wait_rule_holds_in_the_data(self):
        for r in self.history:
            if r["role"] != "final":
                waits = r["distributor_id"] in rates.WAIT_FOR_APPROVED and r["price_status"] != "approved"
                self.assertEqual(r["role"] == "withheld", waits, r["rate_id"])
        withheld = {r["rate_id"] for r in self.history if r["role"] == "withheld"}
        self.assertTrue(withheld)
        self.assertFalse([e for e in self.effective if e["rate_id"] in withheld])
        # the AER's 2025-26 v1 for Jemena and Power and Water is the one the approved version changed
        v1 = Counter(r["validation_status"] for r in self.history if r["role"] == "withheld"
                     and r["fin_year"] == "2025-26" and r["price_status"] == "proposed")
        self.assertEqual(v1, Counter({"mismatch": 112, "match": 5}))

    def test_every_aer_side_rate_has_a_validation_result(self):
        published = {(r["distributor_id"], r["fin_year"]) for r in self.history if r["role"] == "final"}
        for r in self.history:
            self.assertIn(r["validation_status"], spec.VALIDATIONS)
            if r["role"] != "final":
                key = (r["distributor_id"], r["fin_year"])
                self.assertEqual(r["validation_status"] == "pending", key not in published, r["rate_id"])
                if r["validation_status"] in ("match", "match_within_rounding", "match_after_adjustment", "mismatch"):
                    self.assertTrue(r["validated_against"] and r["delta_std"], r["rate_id"])

    def test_known_differences_are_recorded(self):
        """The AER's 2023-24 Evoenergy figures exclude the LFiT rebate the distributor's schedule includes; Ergon's
        2025-26 SAC Large rows the AER carries as withdrawn are re-priced in Ergon's own list."""
        mismatch = Counter((e["distributor_id"], e["fin_year"]) for e in self.effective
                           if e["validation_status"] == "mismatch")
        self.assertEqual(mismatch, Counter({("evoenergy", "2023-24"): 53, ("ergon", "2025-26"): 5}))

    def test_report_is_current(self):
        self.assertEqual(rates.REPORT_PATH, str(ROOT / "docs" / "effective_rates.md"))
        with open(rates.REPORT_PATH, encoding="utf-8") as f:
            self.assertEqual(f.read(), rates.report(rates.Db()),
                             "docs/effective_rates.md is stale: run scripts/tariffdb/rates.py report")

    def test_cli(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rates.main(["rate", "--distributor", "jemena", "--tariff", "A100", "--date", "2025-08-01"])
        self.assertIn("| final |", out.getvalue())
        comp = next(r["component_id"] for r in self.history if r["role"] == "withheld")
        out = io.StringIO()
        with redirect_stdout(out):
            rates.main(["history", comp])
            rates.main(["changes", "--distributor", "ergon", "--year", "2025-26", "--detail"])
        self.assertIn("withheld", out.getvalue())
        self.assertIn("EDSTT1", out.getvalue())


if __name__ == "__main__":
    unittest.main()
