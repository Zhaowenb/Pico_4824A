function experimentalSnapshot() { return {
        path: analysisSource,
        reference_channel: $("modeReferenceChannel").value,
        comparison_channel: $("modeComparisonChannel").value,
        filter: analysisFilterPayload(),
        direct_start_us: number("modeDirectStart"),
        direct_end_us: number("modeDirectEnd"),
        search_start_us: number("modeSearchStart"),
        search_end_us: number("modeSearchEnd"),
        end_start_us: number("modeEndStart"),
        end_end_us: number("modeEndEnd"),
        maximum_shift_us: number("modeMaxShift"),
        temporal_window_us: number("modeTemporalWindow"),
        minimum_separation_us: number("modeCandidateSeparation"),
        candidate_count: number("modeCandidateCount"),
      }; }
/* Experimental analysis controller: original algorithm/API and data unchanged. */
async function calculateExperimentalModes() {
  if (!analysisSource) {
    setEvent("请先加载至少包含两个通道的波形文件。", "error");
    return;
  }
  const referenceChannel = $("modeReferenceChannel").value;
  const comparisonChannel = $("modeComparisonChannel").value;
  if (!referenceChannel || !comparisonChannel || referenceChannel === comparisonChannel) {
    setEvent("请选择两个不同的通道进行共模—差模实验。", "error");
    return;
  }
  const snapshot = experimentalSnapshot();
  const ticket = experimentalState.begin(snapshot);
  const requestId = ++analysisExperimentalRequestId;
  const sourcePath = analysisSource;
  try {
    setAnalysisViewState("processing");
    $("modeAnalysisMeta").textContent = "正在计算实验指标…";
    const result = await api("/api/analysis/experimental-modes", {
      method: "POST",
      body: JSON.stringify(snapshot),
    });
    if (requestId !== analysisExperimentalRequestId || sourcePath !== analysisSource) return;
    if (!experimentalState.commit(ticket, experimentalSnapshot(), result)) return;
    experimentalModeResult = result;
    $("modeWaveEmpty").style.display = "none";
    $("modeAnalysisMeta").textContent = result.warning;
    $("modeResultTitle").textContent = `${result.reference_channel}/${result.comparison_channel} 空间—时间对称性与端面匹配`;
    const calibration = result.calibration, metrics = result.metrics;
    const windows = result.window_metrics;
    $("modeMetricCards").innerHTML = [
      [`${result.reference_channel}/${result.comparison_channel}直达相关`, calibration.direct_window_correlation.toFixed(4), "校准窗"],
      [`${result.comparison_channel}→${result.reference_channel}增益`, calibration.comparison_to_reference_gain.toFixed(5), `时移 ${calibration.comparison_shift_us.toFixed(4)} μs`],
      ["直达窗差/共", `${windows.direct.differential_to_common_db.toFixed(2)} dB`, "空间对称性"],
      ["中间窗差/共", `${windows.search.differential_to_common_db.toFixed(2)} dB`, "时间演化"],
      ["端面窗差/共", `${windows.end_reflection.differential_to_common_db.toFixed(2)} dB`, "端面反射"],
      ["全分析窗差/共", `${metrics.differential_to_common_db.toFixed(2)} dB`, "总体"],
      ["匹配候选", `${result.candidates.length} 个`, "不等于缺陷数量"],
    ].map(([label, value, detail]) => `<article><span>${label}</span><strong>${value}</strong><small>${detail}</small></article>`).join("");
    $("modeCandidatesBody").innerHTML = result.candidates.map((candidate) => `<tr><td>${candidate.time_us.toFixed(3)}</td><td>${candidate.absolute_correlation.toFixed(4)}</td><td>${candidate.correlation.toFixed(4)}</td><td>${candidate.polarity > 0 ? "同相" : "反相"}</td></tr>`).join("");
    drawExperimentalModes();
    setAnalysisViewState("ready");
    syncAnalysisLensContext();
  } catch (error) {
    if (requestId !== analysisExperimentalRequestId || sourcePath !== analysisSource) return;
    if (!experimentalState.current(ticket, experimentalSnapshot())) return;
    experimentalState.status = "error";
    setAnalysisViewState("error");
    $("modeAnalysisMeta").textContent = error.message;
    setEvent(error.message, "error");
  }
}

function drawExperimentalModes() {
  if (!experimentalModeResult || document.body.dataset.page !== "file-analysis") return;
  const result = experimentalModeResult;
  const canvas = $("modeWaveCanvas");
  let setup = canvasSetup(canvas);
  let { ctx, width, height } = setup;
  const left = 70, right = width - 15, top = 16, bottom = height - 31;
  const times = result.time_us;
  const all = [...result.common_v, ...result.differential_v];
  const maximum = Math.max(...all.map(Math.abs), 1e-12);
  ctx.clearRect(0, 0, width, height);
  drawAxes(ctx, width, height, left, top, right, bottom,
    (ratio) => `${(times[0] + (times.at(-1) - times[0]) * ratio).toFixed(0)} μs`,
    (ratio) => `${((-maximum + 2 * maximum * ratio) * 1000).toFixed(2)}`);
  [["common_v", workstationDataColor(), []], ["differential_v", "#c98435", [5, 3]]].forEach(([key, color, dash]) => {
    ctx.strokeStyle = color; ctx.lineWidth = 1.2; ctx.setLineDash(dash); ctx.beginPath();
    result[key].forEach((value, index) => {
      const x = left + index / Math.max(result[key].length - 1, 1) * (right - left);
      const y = bottom - (value + maximum) / (2 * maximum) * (bottom - top);
      if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
  ctx.setLineDash([]); ctx.fillStyle = workstationDataColor(); ctx.fillText("共模", left + 6, top + 13);
  ctx.fillStyle = "#c98435"; ctx.fillText("差模", left + 48, top + 13);
  ctx.fillStyle = "#a5b4c7"; ctx.fillText("mV", 8, top + 10);

  const symmetryCanvas = $("modeSymmetryCanvas");
  setup = canvasSetup(symmetryCanvas);
  ({ ctx, width, height } = setup);
  const symmetryTimes = result.symmetry_time_us;
  const symmetryValues = result.temporal_symmetry_db;
  const sLeft = 70, sRight = width - 15, sTop = 15, sBottom = height - 31;
  const finiteSymmetry = symmetryValues.filter(Number.isFinite);
  const symmetryMin = Math.min(...finiteSymmetry, -60);
  const symmetryMax = Math.max(...finiteSymmetry, 0);
  ctx.clearRect(0, 0, width, height);
  drawAxes(ctx, width, height, sLeft, sTop, sRight, sBottom,
    (ratio) => `${(symmetryTimes[0] + (symmetryTimes.at(-1) - symmetryTimes[0]) * ratio).toFixed(0)} μs`,
    (ratio) => (symmetryMin + (symmetryMax - symmetryMin) * ratio).toFixed(1));
  ctx.strokeStyle = workstationDataColor("high"); ctx.lineWidth = 1.25; ctx.beginPath();
  symmetryValues.forEach((value, index) => {
    const x = sLeft + index / Math.max(symmetryValues.length - 1, 1) * (sRight - sLeft);
    const y = sBottom - (value - symmetryMin) / Math.max(symmetryMax - symmetryMin, 1e-12) * (sBottom - sTop);
    if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.fillStyle = "#a5b4c7";
  ctx.fillText(`局部差模/共模 dB · ${result.windows.temporal_window_us.toFixed(2)} μs窗`, 7, sTop + 10);

  const matchCanvas = $("modeMatchCanvas");
  setup = canvasSetup(matchCanvas);
  ({ ctx, width, height } = setup);
  const matchTimes = result.match_time_us, correlation = result.match_correlation;
  const mLeft = 70, mRight = width - 15, mTop = 15, mBottom = height - 31;
  ctx.clearRect(0, 0, width, height);
  drawAxes(ctx, width, height, mLeft, mTop, mRight, mBottom,
    (ratio) => `${(matchTimes[0] + (matchTimes.at(-1) - matchTimes[0]) * ratio).toFixed(0)} μs`,
    (ratio) => (-1 + 2 * ratio).toFixed(1));
  ctx.strokeStyle = workstationDataColor(); ctx.lineWidth = 1.25; ctx.beginPath();
  correlation.forEach((value, index) => {
    const x = mLeft + index / Math.max(correlation.length - 1, 1) * (mRight - mLeft);
    const y = mBottom - (value + 1) / 2 * (mBottom - mTop);
    if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
  result.candidates.forEach((candidate) => {
    const x = mLeft + (candidate.time_us - matchTimes[0]) / Math.max(matchTimes.at(-1) - matchTimes[0], 1e-12) * (mRight - mLeft);
    ctx.strokeStyle = "rgba(201,132,53,.72)"; ctx.beginPath(); ctx.moveTo(x, mTop); ctx.lineTo(x, mBottom); ctx.stroke();
  });
  ctx.fillStyle = "#718892"; ctx.fillText("相关系数", 7, mTop + 10);
}

