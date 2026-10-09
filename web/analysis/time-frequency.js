/* Analysis controller: original APIs and scientific data, shared request identities. */
function updateTimeFrequencyControls() {
  const method = $("timeFrequencyMethod").value;
  $("stftControls").hidden = method !== "stft";
  $("wpdControls").hidden = method !== "wpd";
  $("cwtControls").hidden = method !== "cwt";
}

function analysisFilterPayload() {
  return {
    enabled: $("analysisFilterEnabled").checked,
    low_hz: number("analysisBandLow") * 1000,
    high_hz: number("analysisBandHigh") * 1000,
    transition_hz: number("analysisTransition") * 1000,
  };
}

function timeFrequencySnapshot() {
  return {
        path: analysisSource,
        channel: $("timeFrequencyChannel").value,
        filter: analysisFilterPayload(),
        method: $("timeFrequencyMethod").value,
        start_us: number("timeFrequencyStart"),
        end_us: number("timeFrequencyEnd"),
        frequency_min_hz: number("timeFrequencyMin") * 1000,
        frequency_max_hz: number("timeFrequencyMax") * 1000,
        floor_db: number("timeFrequencyFloor"),
        window_us: number("stftWindowUs"),
        overlap_ratio: number("stftOverlap") / 100,
        fft_samples: number("stftFftSamples"),
        window_function: $("stftWindowFunction").value,
        wpd_level: number("wpdLevel"),
        wavelet: $("wpdWavelet").value,
        cwt_bins: number("cwtBins"),
        morlet_omega0: number("cwtOmega0"),
        cwt_log_frequency: $("cwtLogFrequency").checked,
      };
}

async function calculateTimeFrequency() {
  if (!analysisSource) {
    setEvent("请先加载一个波形文件。", "error");
    return;
  }
  const snapshot = timeFrequencySnapshot();
  const ticket = timeFrequencyState.begin(snapshot);
  const requestId = ++analysisTimeFrequencyRequestId;
  const sourcePath = analysisSource;
  const method = $("timeFrequencyMethod").value;
  const channel = $("timeFrequencyChannel").value;
  try {
    setAnalysisViewState("processing");
    $("timeFrequencyEmpty").style.display = "grid";
    $("timeFrequencyEmpty").textContent = `${method.toUpperCase()} 正在计算…`;
    $("timeFrequencyMeta").textContent = "正在计算…";
    const result = await api("/api/analysis/time-frequency", {
      method: "POST",
      body: JSON.stringify(snapshot),
    });
    if (requestId !== analysisTimeFrequencyRequestId || sourcePath !== analysisSource) return;
    if (!timeFrequencyState.commit(ticket, timeFrequencySnapshot(), result)) return;
    timeFrequencyResult = result;
    $("timeFrequencyEmpty").style.display = "none";
    $("timeFrequencyTitle").textContent = `${result.method === "wpd" ? "WPD频带分解" : `${result.method.toUpperCase()}时频图`} · CH ${channel}`;
    $("tfFloorLabel").textContent = `${result.floor_db} dB`;
    const details = result.details;
    $("timeFrequencyMeta").textContent = result.method === "stft"
      ? `窗长 ${details.window_us.toFixed(2)} μs · 频率分辨能力约 ${Number(details.window_resolution_hz / 1000).toFixed(3)} kHz · FFT间隔 ${Number(details.frequency_bin_hz / 1000).toFixed(3)} kHz · Δt ${Number(details.time_step_us).toFixed(3)} μs`
      : result.method === "wpd"
        ? `${details.wavelet} · ${details.bands} 频带 · 带宽 ${Number(details.band_width_hz / 1000).toFixed(3)} kHz`
        : `${details.cwt_bins} 频率点 · Morlet ω0=${details.morlet_omega0}`;
    drawTimeFrequency();
    setAnalysisViewState("ready");
    syncAnalysisLensContext();
  } catch (error) {
    if (requestId !== analysisTimeFrequencyRequestId || sourcePath !== analysisSource || !timeFrequencyState.current(ticket, timeFrequencySnapshot())) return;
    timeFrequencyState.status = "error";
    setAnalysisViewState("error");
    $("timeFrequencyEmpty").style.display = "grid";
    $("timeFrequencyEmpty").textContent = `计算失败 · ${error.message}`;
    $("timeFrequencyMeta").textContent = error.message;
    setEvent(error.message, "error");
  }
}

function tfColor(value, floor) {
  const ratio = Math.max(0, Math.min(1, (value - floor) / -floor));
  const stops = workstationDataPalette.stops;
  const scaled = ratio * (stops.length - 1);
  const index = Math.min(stops.length - 2, Math.floor(scaled));
  const fraction = scaled - index;
  return stops[index].map((value0, component) => Math.round(value0 + (stops[index + 1][component] - value0) * fraction));
}

function drawTimeFrequency() {
  if (!timeFrequencyResult || document.body.dataset.page !== "file-analysis") return;
  const canvas = $("timeFrequencyCanvas");
  const { ctx, width, height } = canvasSetup(canvas);
  const result = timeFrequencyResult;
  const columns = result.time_us.length, rows = result.frequency_hz.length;
  if (!columns || !rows) return;
  const image = document.createElement("canvas");
  image.width = columns; image.height = rows;
  const imageContext = image.getContext("2d");
  const pixels = imageContext.createImageData(columns, rows);
  for (let row = 0; row < rows; row += 1) {
    for (let column = 0; column < columns; column += 1) {
      const [red, green, blue] = tfColor(result.values_db[row][column], result.floor_db);
      const targetRow = rows - 1 - row;
      const offset = (targetRow * columns + column) * 4;
      pixels.data[offset] = red; pixels.data[offset + 1] = green; pixels.data[offset + 2] = blue; pixels.data[offset + 3] = 255;
    }
  }
  imageContext.putImageData(pixels, 0, 0);
  const left = 72, right = width - 18, top = 17, bottom = height - 34;
  ctx.clearRect(0, 0, width, height);
  ctx.imageSmoothingEnabled = result.method !== "wpd";
  ctx.drawImage(image, left, top, right - left, bottom - top);
  ctx.strokeStyle = "#32454f"; ctx.strokeRect(left, top, right - left, bottom - top);
  drawAxes(
    ctx, width, height, left, top, right, bottom,
    (ratio) => `${(result.time_us[0] + (result.time_us.at(-1) - result.time_us[0]) * ratio).toFixed(1)} μs`,
    (ratio) => `${(result.frequency_hz[Math.round(ratio * (rows - 1))] / 1000).toFixed(1)}`,
  );
  ctx.fillStyle = "#a5b4c7"; ctx.font = "11px Consolas";
  ctx.fillText(result.method === "cwt" ? "kHz/log" : "kHz", 8, top + 10);
}

