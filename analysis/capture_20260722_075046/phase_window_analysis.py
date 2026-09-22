from pathlib import Path

import numpy as np


CSV_PATH = Path(r"F:\Project\01guided_waves\software\Pico_4824A\data\capture_20260722_075046.csv")


def tapered_bandpass(signal: np.ndarray, fs: float) -> np.ndarray:
    frequency = np.fft.rfftfreq(signal.size, 1.0 / fs)
    response = np.zeros_like(frequency)
    low0, low1, high1, high0 = 15_000.0, 30_000.0, 95_000.0, 130_000.0
    rising = (frequency >= low0) & (frequency < low1)
    response[rising] = 0.5 - 0.5 * np.cos(np.pi * (frequency[rising] - low0) / (low1 - low0))
    response[(frequency >= low1) & (frequency <= high1)] = 1.0
    falling = (frequency > high1) & (frequency <= high0)
    response[falling] = 0.5 + 0.5 * np.cos(np.pi * (frequency[falling] - high1) / (high0 - high1))
    return np.fft.irfft(np.fft.rfft(signal) * response, signal.size)


def analytic(signal: np.ndarray) -> np.ndarray:
    spectrum = np.fft.fft(signal)
    multiplier = np.zeros(signal.size)
    multiplier[0] = 1.0
    multiplier[1 : signal.size // 2] = 2.0
    multiplier[signal.size // 2] = 1.0
    return np.fft.ifft(spectrum * multiplier)


data = np.loadtxt(CSV_PATH, delimiter=",", skiprows=1)
t = data[:, 0]
fs = 1.0 / np.median(np.diff(t))
names = ("A", "C", "G", "H")
signals = {name: data[:, index + 1] for index, name in enumerate(names)}
filtered = {name: tapered_bandpass(signal - np.mean(signal[t < -10e-6]), fs) for name, signal in signals.items()}
analytic_signals = {name: analytic(signal) for name, signal in filtered.items()}

windows = (
    ("direct_start", 170.0, 230.0),
    ("transition", 230.0, 275.0),
    ("C_dominant", 275.0, 315.0),
    ("ringdown_1", 315.0, 350.0),
    ("ringdown_2", 350.0, 400.0),
    ("late_echo", 650.0, 730.0),
)

for label, start_us, stop_us in windows:
    mask = (t >= start_us * 1e-6) & (t < stop_us * 1e-6)
    a = filtered["A"][mask]
    g = filtered["G"][mask]
    za = analytic_signals["A"][mask]
    zg = analytic_signals["G"][mask]
    correlation = float(np.corrcoef(a, g)[0, 1])
    cross = np.sum(za * np.conj(zg))
    coherence = float(abs(cross) / np.sqrt(np.sum(abs(za) ** 2) * np.sum(abs(zg) ** 2)))
    phase_deg = float(np.angle(cross, deg=True))
    rms = {name: float(np.sqrt(np.mean(filtered[name][mask] ** 2)) * 1e3) for name in ("A", "C", "G")}
    print(
        f"{label:12s} {start_us:6.1f}-{stop_us:6.1f} us | "
        f"corr(A,G)={correlation:+.4f} | phase(A-G)={phase_deg:+7.2f} deg | "
        f"coherence={coherence:.4f} | rms_mV A={rms['A']:.3f} C={rms['C']:.3f} G={rms['G']:.3f}"
    )
