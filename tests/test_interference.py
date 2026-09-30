import tempfile
import unittest
from pathlib import Path

import numpy as np

from pico4824a.config import AcquisitionConfig
from pico4824a.device import CaptureResult
from pico4824a.interference import analyze, create_session, export_csv, load_session, preview, reanalyze, save_run, summarize, _fits, _comparisons


class InterferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = AcquisitionConfig(sample_rate_hz=1_000_000, pre_trigger_samples=100,
                                        post_trigger_samples=500)
        self.config.awg.frequency_hz = 75_000
        self.config.awg.cycles = 5
        self.windows = {"t0": 0, "baseline_start": -50, "baseline_end": -5,
                        "tail_end": 200, "echo_start": 300, "echo_end": 400}
        self.session = create_session({"config": self.config.to_dict(), "repeats": 3,
            "channels": {"rx": "B", "rod": "C", "trigger": "A", "rx_probe": 1, "rod_probe": 10},
            "windows_us": self.windows}, self.root)

    def tearDown(self):
        self.temp.cleanup()

    def capture(self, overflow=()):
        t = (np.arange(600)-100)/1_000_000
        signal = .25*np.sin(2*np.pi*75_000*t) + .01
        return CaptureResult(t, {"A": signal, "B": signal, "C": signal/10},
            1e-6, 1_000_000, 1_000_000, overflow, self.config, True)

    def test_windows_fft_correlation_and_overflow(self):
        result = analyze(self.capture(("B",)), {"channels": self.session["channels"],
            "windows_us": self.windows, "experiment": 3})
        self.assertAlmostEqual(result["baseline_dc_v"], .01, delta=.01)
        self.assertGreater(result["tx"]["a75_v"], .15)
        self.assertAlmostEqual(result["rod_relation"]["tx"]["rho"], 1, places=6)
        self.assertEqual(result["overflow_channels"], ["B"])
        self.assertIsNotNone(result["snr_echo_db"])
        self.assertEqual(len(result["tx"]["fft_hz"]), len(result["tx"]["fft_phase_rad"]))

    def test_missing_echo_and_invalid_window(self):
        w = {**self.windows, "echo_start": None, "echo_end": None}
        out = analyze(self.capture(), {"channels": self.session["channels"],
                                       "windows_us": w, "experiment": 1})
        self.assertIsNone(out["echo"])
        self.assertIsNone(out["snr_echo_db"])
        w["baseline_start"] = -1000
        with self.assertRaises(ValueError):
            analyze(self.capture(), {"channels": self.session["channels"],
                                     "windows_us": w, "experiment": 1})

    def test_twelve_experiments_persist_resume_and_reanalyze(self):
        cases = {
            1: [("float", {}), ("grounded", {})],
            2: [(str(x), {"resistance_ohm": x}) for x in (0, 2, 10)],
            3: [("float", {})],
            4: [(f"{x}_grounded", {"distance_cm": x}) for x in (5, 10, 20, 40)],
            5: [("normal", {}), ("twisted", {})],
            6: [(str(x), {"area_cm2": x}) for x in (1, 2, 5)],
            7: [(str(x), {"angle_deg": x}) for x in (0, 45, 90, 135)],
            8: [("real", {}), ("dummy", {})],
            9: [("normal", {}), ("no_mech", {})],
            10: [("common", {}), ("star", {})],
            11: [(str(x), {"length_cm": x}) for x in (10, 50, 100)],
            12: [("no_shield", {}), ("shield", {})],
        }
        capture_id = 0
        for experiment, conditions in cases.items():
            for condition, variables in conditions:
                for _ in range(3):
                    capture_id += 1
                    save_run(self.session["id"], capture_id, self.capture(),
                             {"experiment": experiment, "condition": condition,
                              "variables": variables}, self.root)
        restored = load_session(self.session["id"], self.root)
        self.assertEqual(len(restored["runs"]), capture_id)
        self.assertEqual(len(summarize(restored)["rows"]), sum(map(len, cases.values())))
        self.assertTrue(Path(self.root, self.session["id"], restored["runs"][0]["raw_file"]).is_file())
        self.assertTrue(Path(self.root, self.session["id"], restored["runs"][0]["fft_file"]).is_file())
        self.assertIn("spectra", preview(self.session["id"], restored["runs"][0]["id"], self.root))
        self.assertTrue(export_csv(self.session["id"], self.root).is_file())
        with self.assertRaises(ValueError):
            save_run(self.session["id"], capture_id, self.capture(),
                     {"experiment": 12, "condition": "shield"}, self.root)
        changed = {**self.windows, "tail_end": 100}
        updated = reanalyze(self.session["id"], changed, self.root)
        self.assertEqual(updated["session"]["runs"][0]["windows_us"], changed)

    def test_distance_fit_and_paired_suppression(self):
        rows=[]
        for distance in (5,10,20,40,80):
            for state,multiplier in (("float",2),("grounded",1)):
                peak=(100/distance**2+0.1)*multiplier
                rows.append({"experiment":4,"condition":f"{distance}_{state}",
                    "variables":{"distance_cm":distance},"overflow":False,
                    "metrics":{"tx_peak_v":{"mean":peak,"sd":0,"n":3}}})
        fit=_fits(rows)["distance"]
        self.assertAlmostEqual(fit["n"],2,delta=.05)
        self.assertGreater(fit["r2"],.99)
        comparisons=_comparisons(rows)
        self.assertEqual(len(comparisons),5)
        self.assertAlmostEqual(comparisons[0]["metrics"]["tx_peak_v"]["suppression_db"],20*np.log10(2))


if __name__ == "__main__":
    unittest.main()
