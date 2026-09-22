from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


CSV_PATH = Path(r"F:\Project\01guided_waves\software\Pico_4824A\data\capture_20260722_075046.csv")
OUT_DIR = Path(__file__).resolve().parent
CHANNELS = ("A", "C", "G", "H")
COLORS = {"A": "#0072B2", "C": "#D55E00", "G": "#009E73", "H": "#CC79A7"}
DISPLAY_GAIN = {"A": 20.0, "C": 20.0, "G": 20.0, "H": 1.0}


def analytic_envelope(signal: np.ndarray) -> np.ndarray:
    n = signal.size
    spectrum = np.fft.fft(signal)
    multiplier = np.zeros(n)
    multiplier[0] = 1.0
    if n % 2 == 0:
        multiplier[1 : n // 2] = 2.0
        multiplier[n // 2] = 1.0
    else:
        multiplier[1 : (n + 1) // 2] = 2.0
    return np.abs(np.fft.ifft(spectrum * multiplier))


def tapered_bandpass(signal: np.ndarray, fs: float) -> np.ndarray:
    freq = np.fft.rfftfreq(signal.size, 1.0 / fs)
    response = np.zeros_like(freq)
    low0, low1, high1, high0 = 15_000.0, 30_000.0, 95_000.0, 130_000.0
    rising = (freq >= low0) & (freq < low1)
    response[rising] = 0.5 - 0.5 * np.cos(np.pi * (freq[rising] - low0) / (low1 - low0))
    response[(freq >= low1) & (freq <= high1)] = 1.0
    falling = (freq > high1) & (freq <= high0)
    response[falling] = 0.5 + 0.5 * np.cos(np.pi * (freq[falling] - high1) / (high0 - high1))
    return np.fft.irfft(np.fft.rfft(signal) * response, signal.size)


def moving_average(values: np.ndarray, samples: int) -> np.ndarray:
    samples = max(int(samples), 1)
    cumulative = np.cumsum(np.insert(values, 0, 0.0))
    smooth = (cumulative[samples:] - cumulative[:-samples]) / samples
    left = samples // 2
    return np.pad(smooth, (left, values.size - smooth.size - left), mode="edge")


def local_peaks(values: np.ndarray, mask: np.ndarray, distance: int, threshold: float) -> list[int]:
    candidates = np.flatnonzero(
        mask & (values >= threshold) & (values >= np.roll(values, 1)) & (values > np.roll(values, -1))
    )
    chosen: list[int] = []
    for index in candidates[np.argsort(values[candidates])[::-1]]:
        if all(abs(int(index) - old) >= distance for old in chosen):
            chosen.append(int(index))
    return sorted(chosen, key=lambda idx: values[idx], reverse=True)


def matched_filter_delay(
    receiver: np.ndarray,
    template: np.ndarray,
    template_start: int,
    search_start: int,
    search_stop: int,
    fs: float,
) -> dict[str, float]:
    n = receiver.size + template.size - 1
    nfft = 1 << (n - 1).bit_length()
    correlation = np.fft.irfft(
        np.fft.rfft(receiver, nfft) * np.fft.rfft(template[::-1], nfft), nfft
    )[:n]
    starts = np.arange(search_start, min(search_stop, receiver.size - template.size))
    raw = correlation[starts + template.size - 1]
    squared = np.concatenate(([0.0], np.cumsum(receiver * receiver)))
    energy = squared[starts + template.size] - squared[starts]
    normalized = raw / np.sqrt(np.maximum(energy * np.sum(template * template), 1e-30))
    best_offset = int(np.argmax(np.abs(normalized)))
    best_start = int(starts[best_offset])
    return {
        "delay_us": (best_start - template_start) / fs * 1e6,
        "correlation": float(normalized[best_offset]),
        "window_start_us": float(best_start / fs * 1e6),
    }


def spectrum_metrics(signal: np.ndarray, fs: float) -> dict[str, float]:
    window = np.hanning(signal.size)
    spectrum = np.abs(np.fft.rfft((signal - np.mean(signal)) * window))
    freq = np.fft.rfftfreq(signal.size, 1.0 / fs)
    search = (freq >= 10_000) & (freq <= 500_000)
    peak_index = np.flatnonzero(search)[np.argmax(spectrum[search])]
    power = spectrum * spectrum
    band = (freq >= 20_000) & (freq <= 120_000)
    centroid = float(np.sum(freq[band] * power[band]) / np.sum(power[band]))
    return {"peak_frequency_hz": float(freq[peak_index]), "centroid_hz_20_120k": centroid}


def font(size: int, bold: bool = False):
    candidates = [
        Path(r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf"),
        Path(r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def draw_panel(draw, box, title, xdata, series, x_label, y_label, y_limits=None):
    left, top, right, bottom = box
    margin_l, margin_r, margin_t, margin_b = 78, 22, 38, 50
    plot = (left + margin_l, top + margin_t, right - margin_r, bottom - margin_b)
    x0, y0, x1, y1 = plot
    draw.rectangle(plot, fill="#FBFCFD", outline="#B8C4CC", width=1)
    draw.text((left + 8, top + 4), title, fill="#17242B", font=font(20, True))
    xmin, xmax = float(np.min(xdata)), float(np.max(xdata))
    if y_limits is None:
        values = np.concatenate([np.asarray(v) for v in series.values()])
        ymin, ymax = float(np.min(values)), float(np.max(values))
        padding = max((ymax - ymin) * 0.08, 1e-12)
        ymin, ymax = ymin - padding, ymax + padding
    else:
        ymin, ymax = y_limits
    for fraction in np.linspace(0, 1, 6):
        x = x0 + fraction * (x1 - x0)
        y = y1 - fraction * (y1 - y0)
        draw.line((x, y0, x, y1), fill="#E5EAED")
        draw.line((x0, y, x1, y), fill="#E5EAED")
        draw.text((x - 20, y1 + 7), f"{xmin + fraction * (xmax - xmin):.0f}", fill="#52636C", font=font(12))
        draw.text((left + 5, y - 7), f"{ymin + fraction * (ymax - ymin):.3g}", fill="#52636C", font=font(12))
    for name, values in series.items():
        step = max(1, len(xdata) // max(int(x1 - x0), 1))
        xx = xdata[::step]
        yy = np.asarray(values)[::step]
        px = x0 + (xx - xmin) / max(xmax - xmin, 1e-30) * (x1 - x0)
        py = y1 - (yy - ymin) / max(ymax - ymin, 1e-30) * (y1 - y0)
        draw.line(list(zip(px.tolist(), py.tolist())), fill=COLORS[name], width=2)
    draw.text(((x0 + x1) / 2 - 30, bottom - 28), x_label, fill="#34454E", font=font(13))
    draw.text((left + 8, top + 25), y_label, fill="#52636C", font=font(12))
    legend_x = right - 230
    for i, name in enumerate(series):
        draw.line((legend_x + i * 55, top + 18, legend_x + i * 55 + 18, top + 18), fill=COLORS[name], width=3)
        draw.text((legend_x + i * 55 + 21, top + 10), name, fill="#34454E", font=font(12, True))


data = np.loadtxt(CSV_PATH, delimiter=",", skiprows=1)
t = data[:, 0]
signals = {name: data[:, index + 1] for index, name in enumerate(CHANNELS)}
display_signals = {name: signals[name] * DISPLAY_GAIN[name] for name in CHANNELS}
dt = float(np.median(np.diff(t)))
fs = 1.0 / dt
pre_mask = t < -10e-6
post_mask = t >= -10e-6

filtered = {}
envelopes = {}
metrics = {
    "samples": int(t.size),
    "sample_rate_hz": fs,
    "time_start_s": float(t[0]),
    "time_end_s": float(t[-1]),
    "channels": {},
}

for name, signal in signals.items():
    centered = signal - np.mean(signal[pre_mask])
    filtered[name] = tapered_bandpass(centered, fs)
    envelopes[name] = moving_average(analytic_envelope(filtered[name]), round(2e-6 * fs))
    noise_rms = float(np.sqrt(np.mean(centered[pre_mask] ** 2)))
    noise_std = float(np.std(centered[pre_mask]))
    peak_index = int(np.argmax(np.abs(centered[post_mask]))) + int(np.flatnonzero(post_mask)[0])
    peak_abs = float(abs(centered[peak_index]))
    metrics["channels"][name] = {
        "mean_v": float(np.mean(signal)),
        "minimum_v": float(np.min(signal)),
        "maximum_v": float(np.max(signal)),
        "peak_to_peak_v": float(np.ptp(signal)),
        "pretrigger_noise_rms_v": noise_rms,
        "pretrigger_noise_std_v": noise_std,
        "posttrigger_peak_abs_v": peak_abs,
        "posttrigger_peak_time_us": float(t[peak_index] * 1e6),
        "peak_snr_db": float(20 * np.log10(max(peak_abs / max(noise_rms, 1e-30), 1e-30))),
        **spectrum_metrics(centered[(t >= -20e-6) & (t <= 500e-6)], fs),
    }

display_envelopes = {name: envelopes[name] * DISPLAY_GAIN[name] for name in CHANNELS}

# Define the H transmit template from its 10%-envelope region around the main burst.
h_env = envelopes["H"]
h_search = (t >= -30e-6) & (t <= 150e-6)
h_peak = int(np.flatnonzero(h_search)[np.argmax(h_env[h_search])])
h_threshold = 0.10 * h_env[h_peak]
h_start = h_peak
while h_start > 0 and h_env[h_start] >= h_threshold:
    h_start -= 1
h_stop = h_peak
while h_stop < t.size - 1 and h_env[h_stop] >= h_threshold:
    h_stop += 1
pad = round(5e-6 * fs)
template_start = max(0, h_start - pad)
template_stop = min(t.size, h_stop + pad)
template = filtered["H"][template_start:template_stop]
metrics["transmit_H"] = {
    "envelope_10pct_start_us": float(t[h_start] * 1e6),
    "envelope_peak_us": float(t[h_peak] * 1e6),
    "envelope_10pct_end_us": float(t[h_stop] * 1e6),
    "measured_duration_us": float((t[h_stop] - t[h_start]) * 1e6),
    "template_start_us": float(t[template_start] * 1e6),
    "template_end_us": float(t[template_stop - 1] * 1e6),
}

event_mask = (t >= -10e-6) & (t <= 900e-6)
for name in ("A", "C", "G"):
    noise_env = envelopes[name][pre_mask]
    robust_sigma = 1.4826 * np.median(np.abs(noise_env - np.median(noise_env)))
    threshold = max(float(np.median(noise_env) + 8 * robust_sigma), float(np.max(envelopes[name][event_mask]) * 0.08))
    peaks = local_peaks(envelopes[name], event_mask, round(15e-6 * fs), threshold)[:16]
    peaks_by_time = sorted(peaks)
    metrics["channels"][name]["envelope_detection_threshold_v"] = threshold
    metrics["channels"][name]["envelope_peaks"] = [
        {"time_us": float(t[index] * 1e6), "amplitude_v": float(envelopes[name][index])}
        for index in peaks_by_time
    ]
    metrics["channels"][name]["matched_filter"] = matched_filter_delay(
        filtered[name],
        template,
        template_start,
        max(0, template_start - round(5e-6 * fs)),
        min(t.size, template_start + round(900e-6 * fs)),
        fs,
    )

# Pairwise receiver similarity over the full post-trigger record.
metrics["receiver_pair_correlation"] = {}
window = (t >= -10e-6) & (t <= 900e-6)
for first, second in (("A", "C"), ("A", "G"), ("C", "G")):
    corr = float(np.corrcoef(filtered[first][window], filtered[second][window])[0, 1])
    metrics["receiver_pair_correlation"][f"{first}_{second}"] = corr

OUT_DIR.mkdir(parents=True, exist_ok=True)
(OUT_DIR / "analysis_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

# Render a compact four-panel diagnostic image.
image = Image.new("RGB", (1600, 1240), "#F3F6F8")
draw = ImageDraw.Draw(image)
draw.text((30, 14), "PicoScope 4824A — CH H transmit / A C G receive", fill="#13252D", font=font(26, True))
draw.text((30, 48), f"80,000 samples | fs={fs/1e6:.1f} MS/s | nominal AWG 60 kHz, 5-cycle Hann burst", fill="#50636C", font=font(14))

full_step = max(1, t.size // 5000)
draw_panel(draw, (20, 78, 1580, 355), "Raw signals — full record | display gain: A/C/G ×20, H ×1", t[::full_step] * 1e6,
           {name: signal[::full_step] for name, signal in display_signals.items()}, "Time (us)", "Displayed voltage (V)")
zoom = (t >= -30e-6) & (t <= 220e-6)
draw_panel(draw, (20, 365, 1580, 642), "Raw signals — transmit and early arrivals | display gain: A/C/G ×20, H ×1", t[zoom] * 1e6,
           {name: signal[zoom] for name, signal in display_signals.items()}, "Time (us)", "Displayed voltage (V)")
env_window = (t >= -20e-6) & (t <= 900e-6)
draw_panel(draw, (20, 652, 1580, 929), "30–95 kHz tapered-band envelopes | display gain: A/C/G ×20, H ×1", t[env_window] * 1e6,
           {name: display_envelopes[name][env_window] for name in CHANNELS}, "Time (us)", "Displayed envelope (V)", y_limits=(0, max(float(np.max(display_envelopes[name][env_window])) for name in CHANNELS) * 1.08))

freq_series = {}
freq_axis = None
spec_window = (t >= -20e-6) & (t <= 500e-6)
for name in CHANNELS:
    segment = signals[name][spec_window] - np.mean(signals[name][spec_window])
    spectrum = np.abs(np.fft.rfft(segment * np.hanning(segment.size)))
    frequency = np.fft.rfftfreq(segment.size, 1 / fs)
    keep = frequency <= 300_000
    normalized_db = 20 * np.log10(np.maximum(spectrum[keep] / max(np.max(spectrum[keep]), 1e-30), 1e-8))
    freq_axis = frequency[keep] / 1000
    freq_series[name] = normalized_db
draw_panel(draw, (20, 939, 1580, 1216), "Normalized spectra", freq_axis, freq_series, "Frequency (kHz)", "dB re channel peak", y_limits=(-80, 3))
image.save(OUT_DIR / "waveform_diagnostic.png")

print(json.dumps(metrics, ensure_ascii=False))
