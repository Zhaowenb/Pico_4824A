import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from pico4824a.analysis import (
    discover_sources,
    export_filtered,
    fft_bandpass,
    load_dataset,
    load_sweep_directory,
    process_dataset,
)


class AnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        sample_rate = 2_000_000.0
        self.time_s = np.arange(-200, 1800, dtype=np.float64) / sample_rate
        low = np.sin(2 * np.pi * 70_000 * self.time_s)
        high = 0.45 * np.sin(2 * np.pi * 300_000 * self.time_s)
        self.path = self.root / "capture.npz"
        np.savez_compressed(
            self.path,
            time_s=self.time_s,
            ch_A_v=low + high,
            ch_G_v=0.8 * low - high,
            metadata_json=json.dumps({"actual_sample_rate_hz": sample_rate}),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_file_and_folder_discovery_and_loading(self) -> None:
        folder = discover_sources(self.root)
        self.assertEqual(folder["kind"], "folder")
        self.assertEqual(folder["files"], [str(self.path.resolve())])
        single = discover_sources(self.path)
        self.assertEqual(single["kind"], "file")
        time_s, channels, metadata = load_dataset(self.path)
        self.assertEqual(time_s.size, 2000)
        self.assertEqual(tuple(sorted(channels)), ("A", "G"))
        self.assertAlmostEqual(metadata["sample_rate_hz"], 2_000_000.0)

    def test_bandpass_suppresses_out_of_band_component(self) -> None:
        _, channels, metadata = load_dataset(self.path)
        filtered = fft_bandpass(
            channels["A"],
            metadata["sample_rate_hz"],
            60_000,
            90_000,
            5_000,
        )
        frequencies = np.fft.rfftfreq(filtered.size, 1 / metadata["sample_rate_hz"])
        spectrum = np.abs(np.fft.rfft(filtered))
        low_peak = spectrum[np.argmin(np.abs(frequencies - 70_000))]
        high_peak = spectrum[np.argmin(np.abs(frequencies - 300_000))]
        self.assertGreater(low_peak / max(high_peak, 1e-30), 100)

    def test_multi_window_spectrum_and_export(self) -> None:
        result = process_dataset(
            self.path,
            {
                "channels": ["A", "G"],
                "filter": {
                    "enabled": True,
                    "low_hz": 60_000,
                    "high_hz": 90_000,
                    "transition_hz": 5_000,
                },
                "show_raw": True,
                "show_filtered": True,
                "time_start_us": -100,
                "time_end_us": 800,
                "spectrum": {
                    "source": "filtered",
                    "mode": "amplitude",
                    "window_function": "hann",
                    "frequency_min_hz": 20_000,
                    "frequency_max_hz": 150_000,
                    "windows": [
                        {"label": "前段", "start_us": 0, "end_us": 250},
                        {"label": "后段", "start_us": 300, "end_us": 700},
                    ],
                },
            },
        )
        self.assertEqual(len(result["spectra"]), 2)
        self.assertEqual(tuple(result["waveform"]), ("A:raw", "A:filtered", "G:raw", "G:filtered"))
        first = result["spectra"][0]
        peak_index = int(np.argmax(first["channels"]["A"]))
        self.assertAlmostEqual(first["frequency_hz"][peak_index], 70_000, delta=5_000)

        exported = export_filtered(
            self.path,
            {"low_hz": 60_000, "high_hz": 90_000, "transition_hz": 5_000},
            "npz",
        )
        self.assertTrue(exported.is_file())
        _, exported_channels, metadata = load_dataset(exported)
        self.assertEqual(tuple(sorted(exported_channels)), ("A", "G"))
        self.assertEqual(metadata["processing"]["raw_modified"], False)

    def test_saved_sweep_directory_is_reconstructed(self) -> None:
        sweep = self.root / "sweep_test"
        raw = sweep / "raw"
        raw.mkdir(parents=True)
        capture = raw / "f070000.000_c0007_r001.npz"
        np.savez_compressed(capture, time_s=self.time_s, ch_A_v=np.zeros_like(self.time_s))
        (sweep / "sweep_config.json").write_text(
            json.dumps(
                {
                    "base_config": {"sample_rate_hz": 2_000_000, "awg": {"waveform": "hann_burst"}},
                    "sweep": {"mode": "grid", "repeats": 1, "receiver_channels": ["A", "G"]},
                }
            ),
            encoding="utf-8",
        )
        (sweep / "runs.csv").write_text(
            "run_index,frequency_hz,cycles,npz_file,direct_start_us,direct_end_us,tail_start_us,tail_end_us,reflection_start_us,reflection_end_us\n"
            "1,70000,7,raw/f070000.000_c0007_r001.npz,150,250,250,650,650,750\n",
            encoding="utf-8",
        )
        (sweep / "summary.csv").write_text(
            "frequency_hz,cycles,repeats_completed,nominal_duration_us,tail_to_direct_db,reflection_to_direct_db,quality_db\n"
            "70000,7,1,100,-20,-1,19.5\n",
            encoding="utf-8",
        )
        result = load_sweep_directory(sweep)
        self.assertTrue(result["is_sweep"])
        self.assertEqual(result["points"], 1)
        self.assertEqual(result["runs_completed"], 1)
        self.assertEqual(result["runs"][0]["absolute_npz_file"], str(capture.resolve()))


if __name__ == "__main__":
    unittest.main()
