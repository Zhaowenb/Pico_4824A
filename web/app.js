const channelNames = [..."ABCDEFGH"];
const colors = ["#35d0ba", "#ffb64d", "#68a8ff", "#ff6f82", "#b88cff", "#59d5ec", "#d6e86c", "#e990c8"];
const inputRanges = ["10mV", "20mV", "50mV", "100mV", "200mV", "500mV", "1V", "2V", "5V", "10V", "20V", "50V"];
const $ = (id) => document.getElementById(id);
let latestResult = null;
let latestSweep = null;
let latestSweepRun = null;
let latestLcr = null;
let latestLcrWave = null;
let polling = null;
let previewTimer = null;
let previewRequest = 0;
let measureFilterTimer = null;
let measureFilterRequest = 0;
let scopeTimeStart = null;
let scopeTimeEnd = null;
let scopeAmplitudeScale = 1;
const hiddenScopeChannels = new Set();

function buildChannels() {
  const channelBox = $("channels");
  const trigger = $("triggerSource");
  const transmitter = $("sweepTransmitterChannel");
  const archiveTransmitter = $("archiveTransmitterChannel");
  channelNames.forEach((name) => {
    const card = document.createElement("div");
    card.className = "channel-card enabled";
    card.innerHTML = `
      <label><span>CH ${name}</span><input id="ch${name}" type="checkbox" checked></label>
      <select id="chRange${name}" aria-label="CH ${name} 输入量程">
        ${inputRanges.map((range) => `<option value="${range}" ${range === "5V" ? "selected" : ""}>±${range}</option>`).join("")}
      </select>`;
    channelBox.appendChild(card);
    trigger.add(new Option(`CH ${name}`, name));
    transmitter.add(new Option(`CH ${name}`, name));
    archiveTransmitter.add(new Option(`CH ${name}`, name));
  });
  trigger.value = "H";
  transmitter.value = "H";
  ["lcrVoltageChannel", "lcrCurrentChannel"].forEach((id) => {
    $(id).innerHTML = channelNames.map((name) => `<option value="${name}">CH ${name}</option>`).join("");
  });
  $("lcrVoltageChannel").value = "A";
  $("lcrCurrentChannel").value = "B";
  ["lcrVoltageRange", "lcrCurrentRange"].forEach((id) => {
    $(id).innerHTML = inputRanges.map((range) => `<option value="${range}">±${range}</option>`).join("");
    $(id).value = "2V";
  });
  $("sweepReceiverChannels").innerHTML = channelNames.map((name) => `
    <label><input id="sweepRx${name}" type="checkbox" ${name !== "H" ? "checked" : ""}><span>${name}</span></label>
  `).join("");
  $("archiveReceiverChannels").innerHTML = channelNames.map((name) => `
    <label><input id="archiveRx${name}" type="checkbox"><span>${name}</span></label>
  `).join("");
  const unavailableOptions = channelNames.map((name) =>
    `<option value="${name}" disabled>CH ${name}（尚未加载）</option>`
  ).join("");
  $("timeFrequencyChannel").innerHTML = unavailableOptions;
  $("modeReferenceChannel").innerHTML = unavailableOptions;
  $("modeComparisonChannel").innerHTML = unavailableOptions;
  buildAnalysisChannelButtons([]);
}

function syncSweepReceiverChannels() {
  syncAcquisitionChannelState();
  const transmitter = $("sweepTransmitterChannel").value;
  const available = channelNames.filter((name) => $(`ch${name}`).checked && name !== transmitter);
  channelNames.forEach((name) => {
    const checkbox = $(`sweepRx${name}`);
    const enabled = available.includes(name);
    if (!enabled) checkbox.checked = false;
    checkbox.disabled = !enabled;
    checkbox.closest("label").classList.toggle("disabled", !enabled);
  });
  let selected = channelNames.filter((name) => $(`sweepRx${name}`).checked && available.includes(name));
  if (!selected.length && available.length) {
    const fallback = available.includes("D") ? "D" : available[0];
    $(`sweepRx${fallback}`).checked = true;
    selected = [fallback];
  }
  $("sweepReceiverSummary").textContent = selected.length
    ? `评价接收通道 ${selected.join("/")}`
    : "没有可用接收通道";
  return selected;
}

function syncAcquisitionChannelState() {
  const enabled = channelNames.filter((name) => $(`ch${name}`).checked);
  const maximumRateMsps = enabled.length >= 5 ? 40 : 80;
  $("sampleRate").max = String(maximumRateMsps);
  if (number("sampleRate") > maximumRateMsps) {
    $("sampleRate").value = String(maximumRateMsps);
  }
  $("sampleRate").title = `${enabled.length} 个启用通道时最高 ${maximumRateMsps} MS/s`;
  channelNames.forEach((name) => {
    const active = enabled.includes(name);
    $(`ch${name}`).closest(".channel-card").classList.toggle("enabled", active);
    $(`chRange${name}`).disabled = !active;
    const transmitterOption = [...$("sweepTransmitterChannel").options]
      .find((option) => option.value === name);
    if (transmitterOption) transmitterOption.disabled = !active;
  });
  if (
    $("sweepTransmitterChannel").value !== "NONE"
    && !enabled.includes($("sweepTransmitterChannel").value)
  ) {
    $("sweepTransmitterChannel").value = "NONE";
  }
  if ($("triggerEnabled").checked && !enabled.includes($("triggerSource").value) && enabled.length) {
    $("triggerSource").value = enabled.includes("H") ? "H" : enabled[0];
  }
}

function number(id) { return Number($(id).value); }

function captureSampleCounts() {
  const sampleRateMsps = number("sampleRate");
  const durationUs = number("captureDurationUs");
  const triggerPercent = Math.max(0, Math.min(99.999, number("triggerPositionPercent")));
  const total = Math.max(2, Math.round(sampleRateMsps * durationUs));
  const pre = Math.max(0, Math.min(total - 1, Math.round(total * triggerPercent / 100)));
  return { total, pre, post: total - pre, sampleRateMsps, durationUs, triggerPercent };
}

function updateCapturePlanSummary() {
  const plan = captureSampleCounts();
  const preUs = plan.pre / plan.sampleRateMsps;
  const postUs = plan.post / plan.sampleRateMsps;
  $("capturePlanSummary").textContent =
    `${plan.sampleRateMsps} MS/s × ${plan.durationUs} μs = ${plan.total.toLocaleString("zh-CN")} 点/通道；`
    + `触发前 ${preUs.toFixed(3)} μs（${plan.triggerPercent.toFixed(2)}%），`
    + `触发后 ${postUs.toFixed(3)} μs`;
}
function buildAwgPayload() {
  return {
    enabled: $("awgEnabled").checked,
    waveform: $("waveform").value,
    frequency_hz: number("awgFrequency") * 1e3,
    cycles: number("awgCycles"),
    pk_to_pk_v: number("awgVpp"),
    offset_v: number("awgOffset"),
    buffer_samples: number("awgBufferSamples"),
    trigger_source: "software",
    custom_csv: null,
    cancel_amplitude_ratio: number("cancelAmplitude") / 100,
    cancel_frequency_hz: number("cancelFrequency") * 1e3,
    cancel_cycles: number("cancelCycles"),
    cancel_phase_deg: number("cancelPhase"),
    cancel_delay_cycles: number("cancelDelayCycles"),
    ramp_up_cycles: number("rampUpCycles"),
    hold_cycles: number("holdCycles"),
    ramp_down_cycles: number("rampDownCycles"),
    hold_level_ratio: number("holdLevel") / 100,
  };
}

function buildPayload() {
  const samplePlan = captureSampleCounts();
  const channels = {};
  channelNames.forEach((name) => {
    channels[name] = {
      enabled: $(`ch${name}`).checked,
      range: $(`chRange${name}`).value,
      coupling: "DC",
      analog_offset_v: 0,
    };
  });
  return {
    simulate: $("simulate").checked,
    sample_rate_hz: number("sampleRate") * 1e6,
    pre_trigger_samples: samplePlan.pre,
    post_trigger_samples: samplePlan.post,
    channels,
    trigger: {
      enabled: $("triggerEnabled").checked,
      source: $("triggerSource").value,
      threshold_v: number("triggerLevel"),
      direction: "rising",
      auto_trigger_ms: number("triggerTimeout"),
      delay_samples: 0,
    },
    awg: buildAwgPayload(),
    capture_timeout_s: Math.max(10, number("triggerTimeout") / 1000 + 3),
  };
}

function buildSweepPayload() {
  const receiverChannels = syncSweepReceiverChannels();
  return {
    config: buildPayload(),
    sweep: {
      mode: $("sweepMode").value,
      frequency_start_hz: number("frequencyStart") * 1e3,
      frequency_stop_hz: number("frequencyStop") * 1e3,
      frequency_step_hz: number("frequencyStep") * 1e3,
      cycles_start: number("cyclesStart"),
      cycles_stop: number("cyclesStop"),
      cycles_step: number("cyclesStep"),
      repeats: number("sweepRepeats"),
      interval_s: number("sweepInterval"),
      metric_band_low_hz: number("metricBandLow") * 1e3,
      metric_band_high_hz: number("metricBandHigh") * 1e3,
      direct_search_start_us: number("directSearchStart"),
      direct_search_end_us: number("directSearchEnd"),
      reflection_delay_min_us: number("reflectionDelayMin"),
      reflection_delay_max_us: number("reflectionDelayMax"),
      packet_window_scale: number("packetWindowScale"),
      tail_guard_after_cycles: number("tailGuardAfter"),
      tail_guard_before_cycles: number("tailGuardBefore"),
      noise_start_us: number("noiseStart"),
      noise_end_us: number("noiseEnd"),
      settling_threshold_ratio: number("settlingThreshold") / 100,
      settling_hold_cycles: number("settlingHoldCycles"),
      recommendation_max_duration_us: number("recommendationMaxDuration"),
      recommendation_min_strength_db: number("recommendationMinStrength"),
      recommendation_strong_min_strength_db: number("recommendationStrongMinStrength"),
      recommendation_min_reflection_snr_db: number("recommendationMinReflectionSnr"),
      receiver_channels: receiverChannels,
      transmitter_channel: $("sweepTransmitterChannel").value,
    },
  };
}

function buildLcrPayload() {
  const voltageChannel = $("lcrVoltageChannel").value;
  const currentChannel = $("lcrCurrentChannel").value;
  const triggerSignal = $("lcrTriggerSignal").value;
  const triggerChannel = triggerSignal === "current" ? currentChannel : voltageChannel;
  const channels = {};
  channelNames.forEach((name) => {
    let range = "2V";
    if (name === voltageChannel) range = $("lcrVoltageRange").value;
    if (name === currentChannel) range = $("lcrCurrentRange").value;
    channels[name] = {
      enabled: [voltageChannel, currentChannel].includes(name),
      range,
      coupling: "DC",
      analog_offset_v: 0,
    };
  });
  const frequencyHz = number("lcrFrequency") * 1000;
  const burstCycles = Math.round(number("lcrBurstCycles"));
  return {
    config: {
      simulate: $("lcrSimulate").checked,
      sample_rate_hz: number("lcrSampleRate") * 1e6,
      pre_trigger_samples: 100,
      post_trigger_samples: 1000,
      channels,
      trigger: {
        enabled: true,
        source: triggerChannel,
        threshold_v: number("lcrTriggerLevel"),
        direction: "rising",
        auto_trigger_ms: 2000,
        delay_samples: 0,
      },
      awg: {
        enabled: true,
        waveform: "lcr_tone",
        frequency_hz: frequencyHz,
        cycles: burstCycles,
        pk_to_pk_v: number("lcrVpp"),
        offset_v: 0,
        buffer_samples: 8192,
        trigger_source: "software",
        tone_ramp_cycles: number("lcrRampCycles"),
      },
      capture_timeout_s: 10,
    },
    lcr: {
      mode: $("lcrMode").value,
      frequency_hz: frequencyHz,
      frequency_start_hz: number("lcrFrequencyStart") * 1000,
      frequency_stop_hz: number("lcrFrequencyStop") * 1000,
      frequency_step_hz: number("lcrFrequencyStep") * 1000,
      points_per_decade: Math.round(number("lcrPointsPerDecade")),
      repeats: Math.round(number("lcrRepeats")),
      interval_s: number("lcrInterval"),
      excitation_vpp: number("lcrVpp"),
      burst_cycles: burstCycles,
      ramp_cycles: number("lcrRampCycles"),
      analysis_cycles: Math.round(number("lcrAnalysisCycles")),
      analysis_guard_cycles: number("lcrGuardCycles"),
      voltage_channel: voltageChannel,
      current_channel: currentChannel,
      trigger_signal: triggerSignal,
      feedback_resistance_ohm: number("lcrFeedbackResistance"),
      voltage_gain: number("lcrVoltageGain"),
      current_gain: number("lcrCurrentGain"),
      current_polarity: Number($("lcrCurrentPolarity").value),
      impedance_scale: number("lcrImpedanceScale"),
      phase_correction_deg: number("lcrPhaseCorrection"),
      series_resistance_ohm: number("lcrSeriesR"),
      series_reactance_ohm: number("lcrSeriesX"),
      parallel_conductance_s: number("lcrParallelG"),
      parallel_susceptance_s: number("lcrParallelB"),
      calibration_enabled: Boolean($("lcrCalibrationFile").value),
      calibration_file: $("lcrCalibrationFile").value || null,
    },
  };
}

async function refreshLcrCalibrations(preferredPath = null) {
  const select = $("lcrCalibrationFile");
  let savedPath = "";
  try {
    savedPath = JSON.parse(localStorage.getItem("pico4824a-settings") || "{}")
      .lcrCalibrationFile || "";
  } catch (_) {
    savedPath = "";
  }
  const previous = preferredPath || select.value || savedPath;
  try {
    const result = await api("/api/lcr/calibrations");
    select.replaceChildren(new Option("不使用校准", ""));
    (result.calibrations || []).forEach((profile) => {
      const start = Number(profile.frequency_start_hz) / 1000;
      const stop = Number(profile.frequency_stop_hz) / 1000;
      const formatKhz = (value) => Number(value).toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
      const range = start === stop
        ? `${formatKhz(start)} kHz`
        : `${formatKhz(start)}–${formatKhz(stop)} kHz`;
      const label = `${profile.standard_resistance_ohm} Ω · ${range} · ${profile.point_count}点 · ${profile.created_at}`;
      select.add(new Option(label, profile.path));
    });
    if ([...select.options].some((option) => option.value === previous)) {
      select.value = previous;
    }
    if (preferredPath && select.value === preferredPath) persistSettings();
    const selected = select.selectedOptions[0];
    $("lcrCalibrationStatus").textContent = select.value
      ? `将使用：${selected.textContent}`
      : (result.calibrations || []).length
        ? `已发现 ${(result.calibrations || []).length} 个校准文件，当前未启用`
        : "尚无校准文件；请先接入精准电阻并执行校准";
  } catch (error) {
    $("lcrCalibrationStatus").textContent = `读取校准列表失败：${error.message}`;
  }
}

function updateLcrControls() {
  const mode = $("lcrMode").value;
  $("lcrSingleControls").hidden = mode !== "single";
  $("lcrSweepControls").hidden = mode === "single";
  $("lcrLinearStepLabel").hidden = mode !== "linear";
  $("lcrLogPointsLabel").hidden = mode !== "log";
  let points = 1;
  if (mode === "linear") {
    points = axisCount(number("lcrFrequencyStart"), number("lcrFrequencyStop"), number("lcrFrequencyStep"));
  } else if (mode === "log") {
    const ratio = number("lcrFrequencyStop") / Math.max(number("lcrFrequencyStart"), 1e-30);
    points = ratio >= 1 ? Math.max(2, Math.ceil(Math.log10(ratio) * number("lcrPointsPerDecade")) + 1) : 0;
  }
  const runs = points * Math.max(0, Math.round(number("lcrRepeats")));
  $("lcrEstimate").textContent = points ? `${points} 频点 · ${runs} 次采集` : "参数无效";
}

function axisCount(start, stop, step) {
  if (!(step > 0) || stop < start) return 0;
  const count = Math.floor((stop - start) / step + 1e-9) + 1;
  const last = start + (count - 1) * step;
  return count + (last < stop - Math.max(1e-9, Math.abs(stop) * 1e-12) ? 1 : 0);
}

function updateSweepControls() {
  const mode = $("sweepMode").value;
  $("frequencySweepControls").hidden = mode === "cycles";
  $("cycleSweepControls").hidden = mode === "frequency";
  const frequencies = mode === "cycles" ? 1 : axisCount(number("frequencyStart"), number("frequencyStop"), number("frequencyStep"));
  const cycles = mode === "frequency" ? 1 : axisCount(number("cyclesStart"), number("cyclesStop"), number("cyclesStep"));
  const points = frequencies * cycles;
  const runs = points * Math.max(0, number("sweepRepeats"));
  const intervalSeconds = Math.max(0, runs - 1) * Math.max(0, number("sweepInterval"));
  const frequencyMin = mode === "cycles" ? number("awgFrequency") : number("frequencyStart");
  const frequencyMax = mode === "cycles" ? number("awgFrequency") : number("frequencyStop");
  const cyclesMin = mode === "frequency" ? number("awgCycles") : number("cyclesStart");
  const cyclesMax = mode === "frequency" ? number("awgCycles") : number("cyclesStop");
  const minDuration = frequencyMax > 0 ? cyclesMin / frequencyMax * 1000 : NaN;
  const maxDuration = frequencyMin > 0 ? cyclesMax / frequencyMin * 1000 : NaN;
  $("sweepEstimate").textContent = points && runs
    ? `${points} 点 · ${runs} 次 · N/f ${minDuration.toFixed(1)}–${maxDuration.toFixed(1)} μs · 间隔至少 ${intervalSeconds.toFixed(1)} s`
    : "参数无效";
}

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
  return data;
}

function updateAwgControls() {
  const waveform = $("waveform").value;
  $("cancelControls").hidden = waveform !== "hann_cancel";
  $("rampControls").hidden = waveform !== "hann_ramp_hold";
  scheduleAwgPreview();
}

function scheduleAwgPreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(updateAwgPreview, 120);
}

async function updateAwgPreview() {
  const requestId = ++previewRequest;
  try {
    const preview = await api("/api/awg-preview", {
      method: "POST",
      body: JSON.stringify(buildAwgPayload()),
    });
    if (requestId !== previewRequest) return;
    drawAwgPreview(preview);
  } catch (error) {
    if (requestId !== previewRequest) return;
    $("awgPreviewMeta").textContent = error.message;
  }
}

function drawAwgPreview(preview) {
  const canvas = $("awgPreviewCanvas");
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const width = rect.width;
  const height = rect.height;
  const left = 42, right = width - 10, top = 10, bottom = height - 24;
  const values = preview.volts;
  const times = preview.time_s;
  const minimum = Math.min(...values, number("awgOffset") - number("awgVpp") / 2);
  const maximum = Math.max(...values, number("awgOffset") + number("awgVpp") / 2);
  const span = Math.max(maximum - minimum, 1e-9);
  const yMin = minimum - span * 0.08;
  const yMax = maximum + span * 0.08;
  const timeMax = preview.duration_s;

  ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = "rgba(120, 144, 156, .18)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const x = left + (right - left) * i / 4;
    const y = top + (bottom - top) * i / 4;
    ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
  }
  const zeroY = bottom - (0 - yMin) / (yMax - yMin) * (bottom - top);
  if (zeroY >= top && zeroY <= bottom) {
    ctx.strokeStyle = "rgba(237, 245, 247, .28)";
    ctx.beginPath(); ctx.moveTo(left, zeroY); ctx.lineTo(right, zeroY); ctx.stroke();
  }

  ctx.strokeStyle = "#35d0ba";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  values.forEach((value, index) => {
    const x = left + times[index] / Math.max(timeMax, 1e-30) * (right - left);
    const y = bottom - (value - yMin) / (yMax - yMin) * (bottom - top);
    if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();

  ctx.fillStyle = "#78909c";
  ctx.font = "10px Consolas";
  ctx.fillText(`${yMax.toPrecision(3)} V`, 2, top + 8);
  ctx.fillText(`${yMin.toPrecision(3)} V`, 2, bottom);
  ctx.fillText("0", left - 3, height - 7);
  ctx.textAlign = "right";
  ctx.fillText(`${(timeMax * 1e6).toFixed(2)} μs`, right, height - 7);
  ctx.textAlign = "left";
  $("awgPreviewMeta").textContent = `${preview.buffer_samples.toLocaleString("zh-CN")} 点 · ${(timeMax * 1e6).toFixed(2)} μs`;
}

function setEvent(message, state = "idle") {
  $("messageText").textContent = message;
  $("stateText").textContent = state === "running" ? "任务运行中" : state === "error" ? "运行异常" : state === "complete" ? "任务完成" : state === "stopped" ? "任务已停止" : "系统就绪";
  $("statusDot").className = `status-dot ${state}`;
  $("eventTime").textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

async function startCapture() {
  try {
    $("captureBtn").disabled = true;
    $("sweepBtn").disabled = true;
    $("lcrStartBtn").disabled = true;
    $("lcrCalibrateBtn").disabled = true;
    $("stopBtn").disabled = true;
    $("sweepStopBtn").disabled = true;
    latestResult = null;
    $("emptyState").style.display = "flex";
    const reply = await api("/api/capture", { method: "POST", body: JSON.stringify(buildPayload()) });
    $("stopBtn").disabled = false;
    setEvent(`采集任务 #${reply.capture_id} 已启动，正在等待触发。`, "running");
    polling = setInterval(pollStatus, 350);
  } catch (error) {
    $("captureBtn").disabled = false;
    $("sweepBtn").disabled = false;
    $("lcrStartBtn").disabled = false;
    $("lcrCalibrateBtn").disabled = false;
    $("stopBtn").disabled = true;
    setEvent(error.message, "error");
  }
}

async function startSweep() {
  try {
    $("captureBtn").disabled = true;
    $("sweepBtn").disabled = true;
    $("lcrStartBtn").disabled = true;
    $("lcrCalibrateBtn").disabled = true;
    $("stopBtn").disabled = true;
    $("sweepStopBtn").disabled = true;
    latestSweep = null;
    $("sweepResultPanel").hidden = true;
    $("sweepProgressBar").style.width = "0%";
    const reply = await api("/api/sweep/start", { method: "POST", body: JSON.stringify(buildSweepPayload()) });
    $("sweepStopBtn").disabled = false;
    setEvent(`扫描任务 #${reply.task_id} 已启动。`, "running");
    $("sweepProgressText").textContent = "正在打开设备并准备第一个参数点";
    clearInterval(polling);
    polling = setInterval(pollStatus, 350);
  } catch (error) {
    $("captureBtn").disabled = false;
    $("sweepBtn").disabled = false;
    $("lcrStartBtn").disabled = false;
    $("lcrCalibrateBtn").disabled = false;
    $("sweepStopBtn").disabled = true;
    setEvent(error.message, "error");
  }
}

async function startLcr() {
  try {
    $("captureBtn").disabled = true;
    $("sweepBtn").disabled = true;
    $("lcrStartBtn").disabled = true;
    $("lcrCalibrateBtn").disabled = true;
    $("lcrStopBtn").disabled = true;
    latestLcr = null;
    latestLcrWave = null;
    $("lcrProgressBar").style.width = "0%";
    const reply = await api("/api/lcr/start", {
      method: "POST",
      body: JSON.stringify(buildLcrPayload()),
    });
    $("lcrStopBtn").disabled = false;
    setEvent(`LCR 任务 #${reply.task_id} 已启动。`, "running");
    $("lcrProgressText").textContent = "正在准备第一个频点";
    clearInterval(polling);
    polling = setInterval(pollStatus, 350);
  } catch (error) {
    $("captureBtn").disabled = false;
    $("sweepBtn").disabled = false;
    $("lcrStartBtn").disabled = false;
    $("lcrCalibrateBtn").disabled = false;
    $("lcrStopBtn").disabled = true;
    setEvent(error.message, "error");
  }
}

async function startLcrCalibration() {
  try {
    const payload = buildLcrPayload();
    payload.standard_resistance_ohm = number("lcrCalibrationResistance");
    payload.lcr.calibration_enabled = false;
    payload.lcr.calibration_file = null;
    $("captureBtn").disabled = true;
    $("sweepBtn").disabled = true;
    $("lcrStartBtn").disabled = true;
    $("lcrCalibrateBtn").disabled = true;
    $("lcrStopBtn").disabled = true;
    latestLcr = null;
    latestLcrWave = null;
    $("lcrProgressBar").style.width = "0%";
    const reply = await api("/api/lcr/calibrate", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    $("lcrStopBtn").disabled = false;
    setEvent(`精准电阻校准任务 #${reply.task_id} 已启动。`, "running");
    $("lcrProgressText").textContent = "正在测量精准电阻的第一个频点";
    clearInterval(polling);
    polling = setInterval(pollStatus, 350);
  } catch (error) {
    $("captureBtn").disabled = false;
    $("sweepBtn").disabled = false;
    $("lcrStartBtn").disabled = false;
    $("lcrCalibrateBtn").disabled = false;
    $("lcrStopBtn").disabled = true;
    setEvent(error.message, "error");
  }
}

async function pollStatus() {
  try {
    const status = await api("/api/status");
    setEvent(status.message, status.state);
    const running = status.state === "running";
    $("stopBtn").disabled = !(running && status.task_kind === "capture");
    $("sweepStopBtn").disabled = !(running && status.task_kind === "sweep");
    $("lcrStopBtn").disabled = !(
      running && ["lcr", "lcr_calibration"].includes(status.task_kind)
    );
    if (status.task_kind === "sweep" && status.progress) {
      const progress = status.progress;
      const percent = progress.total_runs ? progress.run_index / progress.total_runs * 100 : 0;
      $("sweepProgressBar").style.width = `${Math.min(100, percent)}%`;
      $("sweepProgressText").textContent = progress.run_index
        ? `${progress.run_index}/${progress.total_runs} 次 · ${Number(progress.frequency_hz / 1000).toFixed(3).replace(/\.0+$/, "")} kHz · ${progress.cycles} 周期 · 重复 ${progress.repeat}/${progress.repeats}`
        : `共 ${progress.total_points} 个参数点，${progress.total_runs} 次采集`;
    }
    if (["lcr", "lcr_calibration"].includes(status.task_kind) && status.progress) {
      const progress = status.progress;
      const percent = progress.total_runs ? progress.run_index / progress.total_runs * 100 : 0;
      $("lcrProgressBar").style.width = `${Math.min(100, percent)}%`;
      $("lcrProgressText").textContent = progress.run_index
        ? `${progress.run_index}/${progress.total_runs} 次 · ${(Number(progress.frequency_hz) / 1000).toFixed(3).replace(/\.000$/, "")} kHz · 重复 ${progress.repeat}/${progress.repeats}`
        : `共 ${progress.total_points} 个频点，${progress.total_runs} 次采集`;
    }
    if (["complete", "stopped"].includes(status.state)) {
      clearInterval(polling);
      polling = null;
      $("captureBtn").disabled = false;
      $("sweepBtn").disabled = false;
      $("stopBtn").disabled = true;
      $("sweepStopBtn").disabled = true;
      $("lcrStartBtn").disabled = false;
      $("lcrCalibrateBtn").disabled = false;
      $("lcrStopBtn").disabled = true;
      if (status.task_kind === "sweep" && status.has_sweep_result) {
        latestSweep = await api("/api/sweep/result");
        updateSweepResult(latestSweep);
        $("sweepProgressBar").style.width = status.state === "complete" ? "100%" : $("sweepProgressBar").style.width;
      } else if (status.task_kind === "capture" && status.state === "complete") {
        latestResult = await api("/api/result/display", {
          method: "POST",
          body: JSON.stringify({ filter: measureFilterPayload(), max_points: 6000 }),
        });
        updateResult(latestResult);
        $("saveNpz").disabled = false;
        $("saveCsv").disabled = false;
      } else if (["lcr", "lcr_calibration"].includes(status.task_kind) && status.has_lcr_result) {
        latestLcr = await api("/api/lcr/result");
        if (latestLcr.run_rows.length) {
          latestLcrWave = await api("/api/result/display", {
            method: "POST",
            body: JSON.stringify({ filter: { enabled: false }, max_points: 6000 }),
          });
        }
        updateLcrResult();
        if (latestLcr.calibration_file) {
          await refreshLcrCalibrations(latestLcr.calibration_file);
        }
        if (status.state === "complete") $("lcrProgressBar").style.width = "100%";
      }
    } else if (status.state === "error") {
      clearInterval(polling);
      polling = null;
      $("captureBtn").disabled = false;
      $("sweepBtn").disabled = false;
      $("stopBtn").disabled = true;
      $("sweepStopBtn").disabled = true;
      $("lcrStartBtn").disabled = false;
      $("lcrCalibrateBtn").disabled = false;
      $("lcrStopBtn").disabled = true;
    }
  } catch (error) {
    setEvent(error.message, "error");
  }
}

async function stopCapture() {
  try {
    $("stopBtn").disabled = true;
    $("sweepStopBtn").disabled = true;
    $("lcrStopBtn").disabled = true;
    await api("/api/stop", { method: "POST", body: "{}" });
    setEvent("已发送停止命令，已完成的数据会保留。", "running");
  } catch (error) { setEvent(error.message, "error"); }
}

async function save(format) {
  try {
    const reply = await api("/api/save", { method: "POST", body: JSON.stringify({ format }) });
    setEvent(`数据已保存：${reply.path}`, "complete");
  } catch (error) { setEvent(error.message, "error"); }
}

function formatTime(value) {
  const abs = Math.abs(value);
  if (abs < 1e-6) return `${(value * 1e9).toFixed(1)} ns`;
  if (abs < 1e-3) return `${(value * 1e6).toFixed(2)} μs`;
  return `${(value * 1e3).toFixed(2)} ms`;
}

function measureFilterPayload() {
  return {
    enabled: $("measureFilterEnabled").checked,
    low_hz: number("measureBandLow") * 1000,
    high_hz: number("measureBandHigh") * 1000,
    transition_hz: number("measureTransition") * 1000,
  };
}

function updateMeasureFilterStatus(result = latestResult) {
  const status = $("measureFilterStatus");
  if (!$("measureFilterEnabled").checked) {
    status.textContent = "未启用；当前显示原始波形";
    status.style.color = "";
    return;
  }
  const filter = result?.summary?.display_filter || measureFilterPayload();
  status.textContent = `已启用零相位 FFT 带通：${Number(filter.low_hz / 1000).toFixed(3)}–${Number(filter.high_hz / 1000).toFixed(3)} kHz，过渡带 ${Number(filter.transition_hz / 1000).toFixed(3)} kHz`;
  status.style.color = "#35d0ba";
}

function scheduleMeasureFilterRefresh() {
  updateMeasureFilterStatus();
  clearTimeout(measureFilterTimer);
  if (!latestResult) return;
  measureFilterTimer = setTimeout(refreshMeasureDisplay, 180);
}

async function refreshMeasureDisplay() {
  const requestId = ++measureFilterRequest;
  try {
    const result = await api("/api/result/display", {
      method: "POST",
      body: JSON.stringify({ filter: measureFilterPayload(), max_points: 6000 }),
    });
    if (requestId !== measureFilterRequest) return;
    latestResult = result;
    updateResult(result, false);
  } catch (error) {
    if (requestId !== measureFilterRequest) return;
    $("measureFilterStatus").textContent = error.message;
    $("measureFilterStatus").style.color = "#ff6678";
  }
}

function resetScopeView(redraw = true) {
  if (!latestResult?.time_s?.length) return;
  scopeTimeStart = latestResult.time_s[0];
  scopeTimeEnd = latestResult.time_s.at(-1);
  scopeAmplitudeScale = 1;
  if (redraw) drawScope();
}

function updateResult(result, resetView = true) {
  const s = result.summary;
  $("sampleMetric").textContent = s.samples.toLocaleString("zh-CN");
  $("rateMetric").textContent = (s.actual_sample_rate_hz / 1e6).toPrecision(5).replace(/0+$/, "").replace(/\.$/, "");
  $("intervalMetric").textContent = (s.sample_interval_s * 1e9).toPrecision(5).replace(/0+$/, "").replace(/\.$/, "");
  $("overflowMetric").textContent = s.overflow_channels.length ? s.overflow_channels.join(", ") : "正常";
  $("overflowMetric").style.color = s.overflow_channels.length ? "#ff6678" : "#35d0ba";
  $("modeText").textContent = s.simulated ? "仿真数据 · 未访问硬件" : "PicoSDK 实机采集";
  $("emptyState").style.display = "none";
  updateMeasureFilterStatus(result);
  if (resetView || scopeTimeStart === null || scopeTimeEnd === null) resetScopeView(false);
  buildLegend(result.channels);
  drawScope();
}

function buildLegend(channels) {
  const names = Object.keys(channels);
  [...hiddenScopeChannels].forEach((name) => {
    if (!names.includes(name)) hiddenScopeChannels.delete(name);
  });
  $("legend").innerHTML = names.map((name) => {
    const index = channelNames.indexOf(name);
    return `<button type="button" data-scope-channel="${name}" class="${hiddenScopeChannels.has(name) ? "muted" : ""}"><i style="background:${colors[index]}"></i>CH ${name}</button>`;
  }).join("");
  $("legend").querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      const name = button.dataset.scopeChannel;
      if (hiddenScopeChannels.has(name)) {
        hiddenScopeChannels.delete(name);
      } else if (names.length - hiddenScopeChannels.size > 1) {
        hiddenScopeChannels.add(name);
      }
      buildLegend(channels);
      drawScope();
    });
  });
}

function updateSweepResult(result) {
  const rows = result.summary_rows || [];
  const resultReceivers = result.receiver_channels?.length
    ? result.receiver_channels
    : channelNames.filter((name) =>
      (result.run_rows || []).some((row) => `${name}_tail_to_direct_db` in row));
  $("sweepResultPanel").hidden = false;
  const maxDuration = number("recommendationMaxDuration");
  const minStrength = number("recommendationMinStrength");
  const strongMinStrength = number("recommendationStrongMinStrength");
  const minReflectionSnr = number("recommendationMinReflectionSnr");
  const minimumBy = (items, key) => items.length ? items.reduce((best, row) => Number(row[key]) < Number(best[key]) ? row : best) : null;
  const maximumBy = (items, key) => items.length ? items.reduce((best, row) => Number(row[key]) > Number(best[key]) ? row : best) : null;
  const durationEligible = rows.filter((row) => Number(row.nominal_duration_us) <= maxDuration);
  const balancedEligible = durationEligible.filter((row) => Number(row.strength_db_from_max) >= minStrength && Number(row.reflection_snr_db) >= minReflectionSnr);
  const strongEligible = durationEligible.filter((row) => Number(row.strength_db_from_max) >= strongMinStrength && Number(row.reflection_snr_db) >= minReflectionSnr);
  const bestAll = minimumBy(rows, "tail_to_direct_db");
  const bestDuration = minimumBy(durationEligible, "tail_to_direct_db");
  const balancedRecommended = maximumBy(balancedEligible, "quality_db");
  const recommended = balancedRecommended || bestDuration || bestAll;
  const strongRecommended = minimumBy(strongEligible, "tail_to_direct_db");
  const pointLabel = (row) => row ? `${(row.frequency_hz / 1000).toFixed(3).replace(/\.000$/, "")} kHz / ${row.cycles} 周期` : "无符合点";
  const card = (title, row, detail) => `<article><span>${title}</span><strong>${pointLabel(row)}</strong><small>${row ? `N/f ${Number(row.nominal_duration_us).toFixed(2)} μs · 拖尾 ${Number(row.tail_to_direct_db).toFixed(2)} dB` : detail}</small></article>`;
  $("evaluationSummaryCards").innerHTML = [
    card("全网格拖尾最低", bestAll, "—"),
    card(`N/f≤${Number(maxDuration).toFixed(0)} μs`, bestDuration, "没有符合时长约束的点"),
    card("平衡质量最高", balancedRecommended, "没有符合平衡阈值的点"),
    card("强信号拖尾最低", strongRecommended, "没有符合强信号阈值的点"),
  ].join("");
  $("sweepPath").textContent = recommended
    ? `${balancedRecommended ? "平衡建议" : "时长约束下的拖尾建议"}：${pointLabel(recommended)}，N/f=${Number(recommended.nominal_duration_us).toFixed(3)} μs，拖尾/直达=${Number(recommended.tail_to_direct_db).toFixed(3)} dB，质量=${Number(recommended.quality_db).toFixed(3)} dB。评价通道 ${resultReceivers.join("/")}；数据：${result.directory}`
    : `原始 NPZ、逐次数据和汇总 CSV：${result.directory}`;
  $("sweepTableBody").innerHTML = rows.map((row) => {
    const best = recommended && row.frequency_hz === recommended.frequency_hz && row.cycles === recommended.cycles ? "best" : "";
    return `<tr class="${best}"><td>${(row.frequency_hz / 1000).toFixed(3).replace(/\.000$/, "")}</td><td>${row.cycles}</td><td>${Number(row.nominal_duration_us).toFixed(3)}</td><td>${row.repeats_completed}</td><td>${Number(row.tail_to_direct_db).toFixed(2)}</td><td>${Number(row.reflection_to_direct_db).toFixed(2)}</td><td>${Number(row.quality_db).toFixed(2)}</td><td>${Number(row.strength_db_from_max).toFixed(2)}</td><td>${Number(row.reflection_snr_db).toFixed(2)}</td></tr>`;
  }).join("");
  $("sweepRunsTableBody").innerHTML = (result.run_rows || []).map((row) => {
    const fileName = String(row.npz_file).split(/[\\/]/).pop();
    const receivers = String(row.receiver_channels || resultReceivers.join("/")).split("/").filter(Boolean);
    const channelTail = receivers.map((name) => {
      const value = Number(row[`${name}_tail_to_direct_db`]);
      return `${name}:${Number.isFinite(value) ? value.toFixed(2) : "—"}`;
    }).join(" / ");
    return `<tr data-run-index="${row.run_index}"><td>${row.run_index}</td><td>${(row.frequency_hz / 1000).toFixed(3).replace(/\.000$/, "")}</td><td>${row.cycles}</td><td>${Number(row.nominal_duration_us).toFixed(3)}</td><td>${row.repeat}</td><td>${Number(row.tail_to_direct_db).toFixed(2)}</td><td>${Number(row.reflection_to_direct_db).toFixed(2)}</td><td>${Number(row.quality_db).toFixed(2)}</td><td>${receivers.join("/")}</td><td>${channelTail}</td><td title="${row.npz_file}">${fileName}</td><td><button data-view-run="${row.run_index}">查看波形</button></td></tr>`;
  }).join("");
  latestSweepRun = null;
  $("sweepRunTitle").textContent = "点击下表中的“查看波形”";
  $("sweepRunFile").textContent = `共 ${result.run_rows.length} 次独立采集，每次都有原始 NPZ`;
  $("sweepRunEmpty").style.display = "grid";
  $("sweepRunLegend").innerHTML = "";
  showSweepView("summary");
  drawSweepResult();
}

function showSweepView(view) {
  const isSummary = view === "summary";
  $("sweepSummaryView").hidden = !isSummary;
  $("sweepRunsView").hidden = isSummary;
  $("sweepMetricLabel").hidden = !isSummary;
  $("summaryViewBtn").classList.toggle("active", isSummary);
  $("runsViewBtn").classList.toggle("active", !isSummary);
  if (isSummary) drawSweepResult(); else drawSweepRun();
}

async function loadSweepRun(runIndex) {
  try {
    $("sweepRunTitle").textContent = `正在读取第 ${runIndex} 次采集…`;
    const result = await api(`/api/sweep/run?run_index=${encodeURIComponent(runIndex)}`);
    latestSweepRun = result;
    const row = result.run;
    $("sweepRunTitle").textContent = `第 ${row.run_index} 次 · ${(row.frequency_hz / 1000).toFixed(3).replace(/\.000$/, "")} kHz · ${row.cycles} 周期 · 重复 ${row.repeat}`;
    $("sweepRunFile").textContent = `${result.file} · 原始 ${result.summary.samples.toLocaleString("zh-CN")} 点/通道 · 当前每隔 ${result.summary.display_step} 点显示`;
    $("sweepRunEmpty").style.display = "none";
    document.querySelectorAll("#sweepRunsTableBody tr").forEach((tr) => tr.classList.toggle("selected", Number(tr.dataset.runIndex) === Number(runIndex)));
    $("sweepRunLegend").innerHTML = Object.keys(result.channels).map((name) => {
      const colorIndex = channelNames.indexOf(name);
      return `<span><i style="background:${colors[Math.max(0, colorIndex)]}"></i>CH ${name}</span>`;
    }).join("");
    drawSweepRun();
  } catch (error) {
    $("sweepRunTitle").textContent = error.message;
    setEvent(error.message, "error");
  }
}

function drawSweepRun() {
  if (!latestSweepRun || $("sweepRunsView").hidden) return;
  const canvas = $("sweepRunCanvas");
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const width = rect.width, height = rect.height;
  const times = latestSweepRun.time_s;
  const names = Object.keys(latestSweepRun.channels);
  const lane = height / Math.max(names.length, 1);
  const tMin = times[0], tMax = times[times.length - 1];
  ctx.clearRect(0, 0, width, height);

  const run = latestSweepRun.run;
  const windows = [
    [Number(run.direct_start_us) * 1e-6, Number(run.direct_end_us) * 1e-6, "rgba(53,208,186,.08)", `直达波 · ${Number(run.nominal_duration_us).toFixed(1)} μs`],
    [Number(run.tail_start_us) * 1e-6, Number(run.tail_end_us) * 1e-6, "rgba(255,182,77,.08)", "拖尾评价窗"],
    [Number(run.reflection_start_us) * 1e-6, Number(run.reflection_end_us) * 1e-6, "rgba(184,140,255,.08)", "端面反射"],
  ];
  windows.forEach(([start, end, fill, label]) => {
    const x1 = (start - tMin) / (tMax - tMin) * width;
    const x2 = (end - tMin) / (tMax - tMin) * width;
    ctx.fillStyle = fill;
    ctx.fillRect(x1, 0, x2 - x1, height);
    ctx.fillStyle = "#6d858f";
    ctx.font = "9px Microsoft YaHei UI";
    ctx.fillText(label, x1 + 5, 12);
  });
  const triggerX = (0 - tMin) / (tMax - tMin) * width;
  ctx.strokeStyle = "rgba(255,111,150,.55)";
  ctx.setLineDash([4, 5]); ctx.beginPath(); ctx.moveTo(triggerX, 0); ctx.lineTo(triggerX, height); ctx.stroke(); ctx.setLineDash([]);

  names.forEach((name, channelIndex) => {
    const values = latestSweepRun.channels[name];
    const middle = lane * (channelIndex + .5);
    const peak = Math.max(...values.map(Math.abs), 1e-12);
    ctx.strokeStyle = "rgba(111,139,150,.19)"; ctx.beginPath(); ctx.moveTo(0, middle); ctx.lineTo(width, middle); ctx.stroke();
    const colorIndex = Math.max(0, channelNames.indexOf(name));
    ctx.strokeStyle = colors[colorIndex]; ctx.lineWidth = 1.15; ctx.beginPath();
    values.forEach((value, index) => {
      const x = index / Math.max(values.length - 1, 1) * width;
      const y = middle - value / peak * lane * .38;
      if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.fillStyle = colors[colorIndex]; ctx.font = "10px Consolas"; ctx.fillText(`CH ${name}`, 8, channelIndex * lane + 26);
    ctx.fillStyle = "#607a84"; ctx.textAlign = "right"; ctx.fillText(`±${peak.toPrecision(3)} V`, width - 8, channelIndex * lane + 26); ctx.textAlign = "left";
  });
  ctx.fillStyle = "#78909c"; ctx.font = "10px Consolas";
  ctx.fillText(formatTime(tMin), 5, height - 6);
  ctx.textAlign = "right"; ctx.fillText(formatTime(tMax), width - 5, height - 6); ctx.textAlign = "left";
}

function drawSweepResult() {
  if (!latestSweep || !latestSweep.summary_rows.length) return;
  const canvas = $("sweepCanvas");
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const width = rect.width, height = rect.height;
  const rows = latestSweep.summary_rows;
  const metric = $("sweepMetric").value;
  const frequencies = [...new Set(rows.map((row) => row.frequency_hz))].sort((a, b) => a - b);
  const cycles = [...new Set(rows.map((row) => row.cycles))].sort((a, b) => a - b);
  const values = rows.map((row) => Number(row[metric]));
  const minimum = Math.min(...values), maximum = Math.max(...values);
  const span = Math.max(maximum - minimum, Math.abs(maximum) * 1e-6, 1e-12);
  const higherIsBetter = new Set(["quality_db", "strength_db_from_max", "reflection_snr_db"]).has(metric);
  const left = 65, right = width - 24, top = 24, bottom = height - 47;
  ctx.clearRect(0, 0, width, height);
  ctx.font = "11px Consolas";

  if (frequencies.length > 1 && cycles.length > 1) {
    const cellWidth = (right - left) / frequencies.length;
    const cellHeight = (bottom - top) / cycles.length;
    rows.forEach((row) => {
      const xIndex = frequencies.indexOf(row.frequency_hz);
      const yIndex = cycles.indexOf(row.cycles);
      const ratio = (Number(row[metric]) - minimum) / span;
      const badness = higherIsBetter ? 1 - ratio : ratio;
      const hue = 168 - badness * 135;
      ctx.fillStyle = `hsl(${hue} 62% 45%)`;
      const x = left + xIndex * cellWidth;
      const y = top + (cycles.length - 1 - yIndex) * cellHeight;
      ctx.fillRect(x + 1, y + 1, cellWidth - 2, cellHeight - 2);
      if (cellWidth > 58 && cellHeight > 30) {
        ctx.fillStyle = "rgba(5,16,20,.82)";
        ctx.textAlign = "center";
        ctx.fillText(Number(row[metric]).toFixed(metric.endsWith("_v") ? 5 : 2), x + cellWidth / 2, y + cellHeight / 2 + 4);
      }
    });
    ctx.fillStyle = "#78909c";
    ctx.textAlign = "center";
    frequencies.forEach((value, index) => ctx.fillText((value / 1000).toFixed(3).replace(/\.000$/, ""), left + (index + .5) * cellWidth, bottom + 20));
    ctx.textAlign = "right";
    cycles.forEach((value, index) => ctx.fillText(String(value), left - 10, top + (cycles.length - index - .5) * cellHeight + 4));
    ctx.textAlign = "center";
    ctx.fillText("频率 (kHz)", (left + right) / 2, height - 8);
  } else {
    const useFrequency = frequencies.length > 1;
    const ordered = [...rows].sort((a, b) => useFrequency ? a.frequency_hz - b.frequency_hz : a.cycles - b.cycles);
    const xValues = ordered.map((row) => useFrequency ? row.frequency_hz / 1000 : row.cycles);
    const xMin = Math.min(...xValues), xMax = Math.max(...xValues);
    const yMin = minimum - span * .12, yMax = maximum + span * .12;
    ctx.strokeStyle = "rgba(120,144,156,.22)";
    for (let index = 0; index <= 4; index += 1) {
      const y = top + (bottom - top) * index / 4;
      ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
      ctx.fillStyle = "#78909c"; ctx.textAlign = "right";
      ctx.fillText((yMax - (yMax - yMin) * index / 4).toFixed(metric.endsWith("_v") ? 5 : 2), left - 8, y + 4);
    }
    ctx.strokeStyle = "#35d0ba"; ctx.lineWidth = 2; ctx.beginPath();
    ordered.forEach((row, index) => {
      const xValue = xValues[index];
      const x = left + (xValue - xMin) / Math.max(xMax - xMin, 1) * (right - left);
      const y = bottom - (Number(row[metric]) - yMin) / (yMax - yMin) * (bottom - top);
      if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ordered.forEach((row, index) => {
      const xValue = xValues[index];
      const x = left + (xValue - xMin) / Math.max(xMax - xMin, 1) * (right - left);
      const y = bottom - (Number(row[metric]) - yMin) / (yMax - yMin) * (bottom - top);
      ctx.fillStyle = "#ffb64d"; ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = "#78909c"; ctx.textAlign = "center"; ctx.fillText(String(xValue), x, bottom + 20);
    });
    ctx.fillText(useFrequency ? "频率 (kHz)" : "周期数", (left + right) / 2, height - 8);
  }
}

function drawScope() {
  if (!latestResult) return;
  const canvas = $("scopeCanvas");
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const width = rect.width, height = rect.height;
  const names = Object.keys(latestResult.channels).filter((name) => !hiddenScopeChannels.has(name));
  if (!names.length) return;
  const lane = height / names.length;
  const times = latestResult.time_s;
  const fullMin = times[0], fullMax = times[times.length - 1];
  const tMin = Math.max(fullMin, scopeTimeStart ?? fullMin);
  const tMax = Math.min(fullMax, scopeTimeEnd ?? fullMax);
  scopeTimeStart = tMin;
  scopeTimeEnd = tMax;
  $("timeStart").textContent = formatTime(tMin);
  $("timeEnd").textContent = formatTime(tMax);
  ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = "rgba(111, 139, 150, .10)";
  ctx.lineWidth = 1;
  for (let grid = 1; grid < 5; grid += 1) {
    const x = width * grid / 5;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke();
  }
  let first = 0;
  while (first < times.length - 1 && times[first] < tMin) first += 1;
  let last = times.length - 1;
  while (last > first && times[last] > tMax) last -= 1;
  first = Math.max(0, first - 1);
  last = Math.min(times.length - 1, last + 1);
  const triggerX = (0 - tMin) / (tMax - tMin) * width;
  if (triggerX >= 0 && triggerX <= width) {
    ctx.strokeStyle = "rgba(255, 111, 150, .55)";
    ctx.setLineDash([4, 5]); ctx.beginPath(); ctx.moveTo(triggerX, 0); ctx.lineTo(triggerX, height); ctx.stroke(); ctx.setLineDash([]);
  }
  names.forEach((name, channelIndex) => {
    const values = latestResult.channels[name];
    const colorIndex = Math.max(0, channelNames.indexOf(name));
    const middle = lane * (channelIndex + .5);
    let peak = 1e-12;
    for (let i = first; i <= last; i += 1) peak = Math.max(peak, Math.abs(values[i]));
    ctx.strokeStyle = "rgba(111, 139, 150, .17)"; ctx.beginPath(); ctx.moveTo(0, middle); ctx.lineTo(width, middle); ctx.stroke();
    ctx.strokeStyle = colors[colorIndex]; ctx.lineWidth = 1.2; ctx.beginPath();
    for (let i = first; i <= last; i += 1) {
      const x = (times[i] - tMin) / Math.max(tMax - tMin, 1e-30) * width;
      const y = middle - values[i] / peak * lane * .38 * scopeAmplitudeScale;
      if (i === first) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.fillStyle = colors[colorIndex]; ctx.font = "10px Consolas"; ctx.fillText(`CH ${name}`, 8, channelIndex * lane + 14);
    ctx.fillStyle = "#607a84"; ctx.textAlign = "right"; ctx.fillText(`±${peak.toPrecision(3)} V · ×${scopeAmplitudeScale.toFixed(2)}`, width - 8, channelIndex * lane + 14); ctx.textAlign = "left";
  });
}

function installScopeCanvasInteraction() {
  const canvas = $("scopeCanvas");
  let drag = null;
  canvas.addEventListener("wheel", (event) => {
    if (!latestResult?.time_s?.length) return;
    event.preventDefault();
    if (event.shiftKey) {
      const factor = event.deltaY < 0 ? 1.2 : 1 / 1.2;
      scopeAmplitudeScale = Math.max(0.05, Math.min(100, scopeAmplitudeScale * factor));
      drawScope();
      return;
    }
    const fullMin = latestResult.time_s[0], fullMax = latestResult.time_s.at(-1);
    const start = scopeTimeStart ?? fullMin, end = scopeTimeEnd ?? fullMax;
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(rect.width, 1)));
    const anchor = start + (end - start) * ratio;
    const factor = event.deltaY > 0 ? 1.25 : 0.8;
    const fullSpan = fullMax - fullMin;
    const minimumSpan = Math.max(fullSpan / 10000, latestResult.summary.sample_interval_s * 20);
    const newSpan = Math.max(minimumSpan, Math.min(fullSpan, (end - start) * factor));
    let nextStart = anchor - newSpan * ratio;
    let nextEnd = nextStart + newSpan;
    if (nextStart < fullMin) { nextEnd += fullMin - nextStart; nextStart = fullMin; }
    if (nextEnd > fullMax) { nextStart -= nextEnd - fullMax; nextEnd = fullMax; }
    scopeTimeStart = nextStart;
    scopeTimeEnd = nextEnd;
    drawScope();
  }, { passive: false });
  canvas.addEventListener("mousedown", (event) => {
    if (!latestResult || event.button !== 0) return;
    drag = { x: event.clientX, start: scopeTimeStart, end: scopeTimeEnd };
    canvas.classList.add("dragging");
  });
  canvas.addEventListener("mousemove", (event) => {
    if (!drag || !latestResult) return;
    const rect = canvas.getBoundingClientRect();
    const fullMin = latestResult.time_s[0], fullMax = latestResult.time_s.at(-1);
    const span = drag.end - drag.start;
    const delta = -(event.clientX - drag.x) / Math.max(rect.width, 1) * span;
    let start = drag.start + delta, end = drag.end + delta;
    if (start < fullMin) { end += fullMin - start; start = fullMin; }
    if (end > fullMax) { start -= end - fullMax; end = fullMax; }
    scopeTimeStart = start;
    scopeTimeEnd = end;
    drawScope();
  });
  window.addEventListener("mouseup", () => {
    drag = null;
    canvas.classList.remove("dragging");
  });
  canvas.addEventListener("dblclick", () => resetScopeView());
}

function engineering(value, unit = "") {
  if (value === null || value === undefined || value === "") return "—";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "—";
  const prefixes = [
    [1e12, "T"], [1e9, "G"], [1e6, "M"], [1e3, "k"],
    [1, ""], [1e-3, "m"], [1e-6, "μ"], [1e-9, "n"], [1e-12, "p"],
  ];
  const absolute = Math.abs(numeric);
  const selected = prefixes.find(([scale]) => absolute >= scale) || prefixes.at(-1);
  return `${(numeric / selected[0]).toPrecision(5).replace(/\.0+$/, "")} ${selected[1]}${unit}`.trim();
}

const lcrUnitDefinitions = {
  frequency: [
    { scale: 1e6, label: "MHz" }, { scale: 1e3, label: "kHz" }, { scale: 1, label: "Hz" },
  ],
  resistance: [
    { scale: 1e6, label: "MΩ" }, { scale: 1e3, label: "kΩ" },
    { scale: 1, label: "Ω" }, { scale: 1e-3, label: "mΩ" },
  ],
  capacitance: [
    { scale: 1, label: "F" }, { scale: 1e-3, label: "mF" },
    { scale: 1e-6, label: "μF" }, { scale: 1e-9, label: "nF" },
    { scale: 1e-12, label: "pF" },
  ],
  inductance: [
    { scale: 1, label: "H" }, { scale: 1e-3, label: "mH" },
    { scale: 1e-6, label: "μH" }, { scale: 1e-9, label: "nH" },
  ],
};

const lcrUnitControlIds = {
  frequency: "lcrFrequencyUnit",
  resistance: "lcrResistanceUnit",
  capacitance: "lcrCapacitanceUnit",
  inductance: "lcrInductanceUnit",
};

function finiteNumbers(values) {
  return values
    .filter((value) => value !== null && value !== undefined && value !== "")
    .map(Number)
    .filter(Number.isFinite);
}

function lcrNumeric(value) {
  return value === null || value === undefined || value === "" ? Number.NaN : Number(value);
}

function resolveLcrUnit(family, values) {
  const definitions = lcrUnitDefinitions[family];
  const selected = $(lcrUnitControlIds[family]).value;
  if (selected !== "auto") {
    const scale = Number(selected);
    return definitions.find((item) => item.scale === scale) || definitions.at(-1);
  }
  const finite = finiteNumbers(values).map(Math.abs).filter((value) => value > 0);
  if (!finite.length) return definitions.find((item) => item.scale === 1) || definitions.at(-1);
  const reference = Math.max(...finite);
  return definitions.find((item) => reference >= item.scale) || definitions.at(-1);
}

function lcrScaled(value, unit, digits = 6) {
  const numeric = lcrNumeric(value);
  if (!Number.isFinite(numeric)) return "—";
  if (numeric === 0) return "0";
  return (numeric / unit.scale).toPrecision(digits);
}

function lcrAxisNumber(value) {
  if (!Number.isFinite(value)) return "—";
  const absolute = Math.abs(value);
  if (absolute === 0) return "0";
  if (absolute >= 1000 || absolute < 0.01) return value.toExponential(2);
  return Number(value.toPrecision(4)).toString();
}

function lcrUnitsForRows(rows) {
  const values = (keys) => rows.flatMap((row) => keys.map((key) => row[key]));
  return {
    frequency: resolveLcrUnit("frequency", values(["frequency_hz"])),
    resistance: resolveLcrUnit("resistance", values([
      "impedance_magnitude_ohm", "impedance_real_ohm", "impedance_imag_ohm", "parallel_resistance_ohm",
    ])),
    capacitance: resolveLcrUnit("capacitance", values(["series_capacitance_f", "parallel_capacitance_f"])),
    inductance: resolveLcrUnit("inductance", values(["series_inductance_h", "parallel_inductance_h"])),
  };
}

function updateLcrResult() {
  if (!latestLcr) return;
  const rows = latestLcr.summary_rows || [];
  const units = lcrUnitsForRows(rows);
  $("lcrResultPath").textContent = latestLcr.directory || "—";
  $("lcrFrequencyHeader").textContent = `频率/${units.frequency.label}`;
  $("lcrMagnitudeHeader").textContent = `|Z|/${units.resistance.label}`;
  $("lcrRHeader").textContent = `R/${units.resistance.label}`;
  $("lcrXHeader").textContent = `X/${units.resistance.label}`;
  $("lcrCsHeader").textContent = `Cs/${units.capacitance.label}`;
  $("lcrLsHeader").textContent = `Ls/${units.inductance.label}`;
  $("lcrRpHeader").textContent = `Rp/${units.resistance.label}`;
  $("lcrCpHeader").textContent = `Cp/${units.capacitance.label}`;
  $("lcrLpHeader").textContent = `Lp/${units.inductance.label}`;
  $("lcrTableBody").innerHTML = rows.map((row) => {
    const cell = (key, digits = 6) => row[key] !== null && row[key] !== undefined && Number.isFinite(Number(row[key])) ? Number(row[key]).toPrecision(digits) : "—";
    return `<tr><td>${lcrScaled(row.frequency_hz, units.frequency)}</td><td>${row.repeats_completed}</td><td>${lcrScaled(row.impedance_magnitude_ohm, units.resistance)}</td><td>${cell("phase_deg", 5)}</td><td>${lcrScaled(row.impedance_real_ohm, units.resistance)}</td><td>${lcrScaled(row.impedance_imag_ohm, units.resistance)}</td><td>${lcrScaled(row.series_capacitance_f, units.capacitance)}</td><td>${lcrScaled(row.series_inductance_h, units.inductance)}</td><td>${lcrScaled(row.parallel_resistance_ohm, units.resistance)}</td><td>${lcrScaled(row.parallel_capacitance_f, units.capacitance)}</td><td>${lcrScaled(row.parallel_inductance_h, units.inductance)}</td><td>${cell("quality_factor", 5)}</td><td>${cell("dissipation_factor", 5)}</td><td>${cell("voltage_snr_db", 5)}</td><td>${cell("current_snr_db", 5)}</td></tr>`;
  }).join("");
  if (rows.length) {
    const row = rows.at(-1);
    $("lcrMagnitudeMetric").textContent = lcrScaled(row.impedance_magnitude_ohm, units.resistance, 5);
    $("lcrMagnitudeMetricUnit").textContent = units.resistance.label;
    $("lcrPhaseMetric").textContent = row.phase_deg === null ? "—" : `${Number(row.phase_deg).toFixed(3)}°`;
    $("lcrRxMetric").textContent = `${lcrScaled(row.impedance_real_ohm, units.resistance, 5)} / ${lcrScaled(row.impedance_imag_ohm, units.resistance, 5)}`;
    $("lcrRxMetricUnit").textContent = units.resistance.label;
    if (Number(row.impedance_imag_ohm) < 0) {
      $("lcrComponentMetric").textContent = `${lcrScaled(row.series_capacitance_f, units.capacitance, 5)} ${units.capacitance.label}`;
      $("lcrComponentUnit").textContent = "串联等效电容 Cs";
    } else {
      $("lcrComponentMetric").textContent = `${lcrScaled(row.series_inductance_h, units.inductance, 5)} ${units.inductance.label}`;
      $("lcrComponentUnit").textContent = "串联等效电感 Ls";
    }
    $("lcrQMetric").textContent = `${engineering(row.quality_factor)} / ${engineering(row.dissipation_factor)}`;
    $("lcrViMetric").textContent = `${engineering(row.voltage_rms_v, "V")} / ${engineering(row.current_rms_a, "A")}`;
  }
  drawLcrParameterCharts();
  drawLcrBode();
  drawLcrNyquist();
  drawLcrWave();
}

function drawLcrFrequencyChart(canvasId, rows, series, yUnit, emptyLabel) {
  const canvas = $(canvasId);
  const { ctx, width, height } = canvasSetup(canvas);
  ctx.clearRect(0, 0, width, height);
  const frequencyUnit = resolveLcrUnit("frequency", rows.map((row) => row.frequency_hz));
  const axisMode = $("lcrFrequencyAxisScale").value;
  const frequencyRaw = rows.map((row) => Number(row.frequency_hz));
  const xValues = frequencyRaw.map((value) => axisMode === "log" ? Math.log10(value) : value);
  const allY = finiteNumbers(rows.flatMap((row) => series.map((item) => lcrNumeric(row[item.key]) / yUnit.scale)));
  if (!allY.length) {
    ctx.fillStyle = "#607781";
    ctx.font = "11px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(`当前频段没有有效${emptyLabel}数据`, width / 2, height / 2);
    ctx.textAlign = "left";
    return;
  }
  let xMin = Math.min(...xValues), xMax = Math.max(...xValues);
  let yMin = Math.min(...allY), yMax = Math.max(...allY);
  if (xMax <= xMin) {
    const margin = axisMode === "log" ? 0.05 : Math.max(Math.abs(xMin) * 0.05, 1);
    xMin -= margin; xMax += margin;
  }
  if (yMax <= yMin) {
    const margin = Math.max(Math.abs(yMin) * 0.08, 1e-12);
    yMin -= margin; yMax += margin;
  } else {
    const margin = (yMax - yMin) * 0.08;
    yMin -= margin; yMax += margin;
  }
  const left = 74, right = width - 18, top = 34, bottom = height - 38;
  drawAxes(ctx, width, height, left, top, right, bottom,
    (ratio) => {
      const position = xMin + (xMax - xMin) * ratio;
      const raw = axisMode === "log" ? 10 ** position : position;
      return lcrAxisNumber(raw / frequencyUnit.scale);
    },
    (ratio) => lcrAxisNumber(yMin + (yMax - yMin) * ratio));

  series.forEach((item, seriesIndex) => {
    ctx.strokeStyle = item.color;
    ctx.lineWidth = 1.7;
    ctx.beginPath();
    let connected = false;
    rows.forEach((row, index) => {
      const raw = lcrNumeric(row[item.key]);
      if (!Number.isFinite(raw)) {
        connected = false;
        return;
      }
      const scaled = raw / yUnit.scale;
      const x = left + (xValues[index] - xMin) / (xMax - xMin) * (right - left);
      const y = bottom - (scaled - yMin) / (yMax - yMin) * (bottom - top);
      if (!connected) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      connected = true;
    });
    ctx.stroke();
    rows.forEach((row, index) => {
      const raw = lcrNumeric(row[item.key]);
      if (!Number.isFinite(raw)) return;
      const scaled = raw / yUnit.scale;
      const x = left + (xValues[index] - xMin) / (xMax - xMin) * (right - left);
      const y = bottom - (scaled - yMin) / (yMax - yMin) * (bottom - top);
      ctx.fillStyle = item.color;
      ctx.beginPath(); ctx.arc(x, y, 2.5, 0, Math.PI * 2); ctx.fill();
    });
    ctx.fillStyle = item.color;
    ctx.font = "10px Consolas";
    ctx.fillText(item.label, left + seriesIndex * 62, 17);
  });
  ctx.fillStyle = "#708790";
  ctx.font = "10px Consolas";
  ctx.textAlign = "right";
  ctx.fillText(`频率 / ${frequencyUnit.label}（${axisMode === "log" ? "对数" : "线性"}）`, right, height - 7);
  ctx.textAlign = "left";
  ctx.fillText(yUnit.label ? `/${yUnit.label}` : emptyLabel, 8, top - 8);
}

function drawLcrParameterCharts() {
  if (!latestLcr?.summary_rows?.length || document.body.dataset.page !== "lcr") return;
  const rows = [...latestLcr.summary_rows].sort((a, b) => Number(a.frequency_hz) - Number(b.frequency_hz));
  const units = lcrUnitsForRows(rows);
  $("lcrImpedanceUnitLabel").textContent = units.resistance.label;
  $("lcrParallelResistanceUnitLabel").textContent = units.resistance.label;
  $("lcrCapacitanceUnitLabel").textContent = units.capacitance.label;
  $("lcrInductanceUnitLabel").textContent = units.inductance.label;
  drawLcrFrequencyChart("lcrImpedanceCanvas", rows, [
    { key: "impedance_magnitude_ohm", label: "|Z|", color: "#35d0ba" },
    { key: "impedance_real_ohm", label: "R", color: "#ffb64d" },
    { key: "impedance_imag_ohm", label: "X", color: "#b88cff" },
  ], units.resistance, "阻抗");
  drawLcrFrequencyChart("lcrParallelResistanceCanvas", rows, [
    { key: "parallel_resistance_ohm", label: "Rp", color: "#68a8ff" },
  ], units.resistance, "并联电阻");
  drawLcrFrequencyChart("lcrCapacitanceCanvas", rows, [
    { key: "series_capacitance_f", label: "Cs", color: "#35d0ba" },
    { key: "parallel_capacitance_f", label: "Cp", color: "#ffb64d" },
  ], units.capacitance, "电容");
  drawLcrFrequencyChart("lcrInductanceCanvas", rows, [
    { key: "series_inductance_h", label: "Ls", color: "#59d5ec" },
    { key: "parallel_inductance_h", label: "Lp", color: "#b88cff" },
  ], units.inductance, "电感");
  drawLcrFrequencyChart("lcrFactorCanvas", rows, [
    { key: "quality_factor", label: "Q", color: "#35d0ba" },
    { key: "dissipation_factor", label: "D", color: "#ffb64d" },
  ], { scale: 1, label: "" }, "Q / D");
  drawLcrFrequencyChart("lcrSnrCanvas", rows, [
    { key: "voltage_snr_db", label: "电压 SNR", color: "#68a8ff" },
    { key: "current_snr_db", label: "电流 SNR", color: "#ff6f82" },
  ], { scale: 1, label: "dB" }, "SNR");
}

function drawLcrBode() {
  if (!latestLcr?.summary_rows?.length || document.body.dataset.page !== "lcr") return;
  const rows = [...latestLcr.summary_rows].sort((a, b) => Number(a.frequency_hz) - Number(b.frequency_hz));
  const canvas = $("lcrBodeCanvas");
  const { ctx, width, height } = canvasSetup(canvas);
  const left = 68, right = width - 62, top = 18, bottom = height - 35;
  const units = lcrUnitsForRows(rows);
  const frequencies = rows.map((row) => Number(row.frequency_hz));
  const logF = frequencies.map((value) => Math.log10(value));
  const magnitudes = rows.map((row) => Math.max(Number(row.impedance_magnitude_ohm), 1e-30));
  const logZ = magnitudes.map((value) => Math.log10(value));
  const phases = rows.map((row) => Number(row.phase_deg));
  let xMin = Math.min(...logF), xMax = Math.max(...logF);
  let zMin = Math.min(...logZ), zMax = Math.max(...logZ);
  let pMin = Math.min(...phases), pMax = Math.max(...phases);
  if (xMax <= xMin) { xMin -= .05; xMax += .05; }
  if (zMax <= zMin) { zMin -= .05; zMax += .05; }
  if (pMax <= pMin) { pMin -= 1; pMax += 1; }
  ctx.clearRect(0, 0, width, height);
  drawAxes(ctx, width, height, left, top, right, bottom,
    (ratio) => lcrAxisNumber(10 ** (xMin + (xMax - xMin) * ratio) / units.frequency.scale),
    (ratio) => lcrAxisNumber(10 ** (zMin + (zMax - zMin) * ratio) / units.resistance.scale));
  ctx.fillStyle = "#b88cff"; ctx.font = "10px Consolas"; ctx.textAlign = "left";
  for (let index = 0; index <= 4; index += 1) {
    const y = bottom - (bottom - top) * index / 4;
    ctx.fillText(`${(pMin + (pMax - pMin) * index / 4).toFixed(1)}°`, right + 7, y + 4);
  }
  const plot = (values, min, max, color) => {
    ctx.strokeStyle = color; ctx.lineWidth = 1.7; ctx.beginPath();
    values.forEach((value, index) => {
      const x = left + (logF[index] - xMin) / (xMax - xMin) * (right - left);
      const y = bottom - (value - min) / (max - min) * (bottom - top);
      if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  };
  plot(logZ, zMin, zMax, "#35d0ba");
  plot(phases, pMin, pMax, "#b88cff");
  ctx.fillStyle = "#35d0ba"; ctx.fillText("|Z|", left + 6, top + 12);
  ctx.fillStyle = "#b88cff"; ctx.fillText("相位", left + 40, top + 12);
  ctx.fillStyle = "#708790"; ctx.textAlign = "right";
  ctx.fillText(`频率 / ${units.frequency.label}`, right, height - 7);
  ctx.textAlign = "left"; ctx.fillText(`|Z| / ${units.resistance.label}`, 6, top + 26);
  ctx.textAlign = "left";
}

function drawLcrNyquist() {
  if (!latestLcr?.summary_rows?.length || document.body.dataset.page !== "lcr") return;
  const rows = [...latestLcr.summary_rows].sort((a, b) => Number(a.frequency_hz) - Number(b.frequency_hz));
  const canvas = $("lcrNyquistCanvas");
  const { ctx, width, height } = canvasSetup(canvas);
  const units = lcrUnitsForRows(rows);
  const real = rows.map((row) => Number(row.impedance_real_ohm) / units.resistance.scale);
  const imag = rows.map((row) => Number(row.impedance_imag_ohm) / units.resistance.scale);
  let xMin = Math.min(...real), xMax = Math.max(...real), yMin = Math.min(...imag), yMax = Math.max(...imag);
  const xSpan = Math.max(xMax - xMin, Math.max(Math.abs(xMax), 1) * .05);
  const ySpan = Math.max(yMax - yMin, Math.max(Math.abs(yMax), 1) * .05);
  xMin -= xSpan * .12; xMax += xSpan * .12; yMin -= ySpan * .12; yMax += ySpan * .12;
  const left = 72, right = width - 18, top = 18, bottom = height - 35;
  ctx.clearRect(0, 0, width, height);
  drawAxes(ctx, width, height, left, top, right, bottom,
    (ratio) => engineering(xMin + (xMax - xMin) * ratio),
    (ratio) => engineering(yMin + (yMax - yMin) * ratio));
  ctx.strokeStyle = "#ffb64d"; ctx.lineWidth = 1.7; ctx.beginPath();
  rows.forEach((row, index) => {
    const x = left + (real[index] - xMin) / (xMax - xMin) * (right - left);
    const y = bottom - (imag[index] - yMin) / (yMax - yMin) * (bottom - top);
    if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
  rows.forEach((row, index) => {
    const x = left + (real[index] - xMin) / (xMax - xMin) * (right - left);
    const y = bottom - (imag[index] - yMin) / (yMax - yMin) * (bottom - top);
    ctx.fillStyle = "#35d0ba"; ctx.beginPath(); ctx.arc(x, y, 3.2, 0, Math.PI * 2); ctx.fill();
  });
  ctx.fillStyle = "#708790"; ctx.font = "10px Consolas";
  ctx.fillText(`R / ${units.resistance.label}`, right - 55, height - 8); ctx.fillText(`X / ${units.resistance.label}`, 8, top + 10);
}

function drawLcrWave() {
  if (!latestLcrWave?.time_s?.length || document.body.dataset.page !== "lcr") return;
  const canvas = $("lcrWaveCanvas");
  const { ctx, width, height } = canvasSetup(canvas);
  const names = [$("lcrVoltageChannel").value, $("lcrCurrentChannel").value];
  const labels = ["RF_OUT1 电压", "RF_OUT2 电流监测"];
  const times = latestLcrWave.time_s;
  const tMin = times[0], tMax = times.at(-1);
  const lane = height / 2;
  ctx.clearRect(0, 0, width, height);
  const lastRun = latestLcr?.run_rows?.at(-1);
  if (lastRun) {
    const x1 = (Number(lastRun.analysis_start_s) - tMin) / Math.max(tMax - tMin, 1e-30) * width;
    const x2 = (Number(lastRun.analysis_end_s) - tMin) / Math.max(tMax - tMin, 1e-30) * width;
    ctx.fillStyle = "rgba(53,208,186,.08)"; ctx.fillRect(x1, 0, x2 - x1, height);
  }
  names.forEach((name, laneIndex) => {
    const values = latestLcrWave.channels[name] || [];
    if (!values.length) return;
    const middle = lane * (laneIndex + .5);
    const peak = Math.max(...values.map(Math.abs), 1e-12);
    ctx.strokeStyle = "rgba(111,139,150,.2)"; ctx.beginPath(); ctx.moveTo(0, middle); ctx.lineTo(width, middle); ctx.stroke();
    ctx.strokeStyle = colors[channelNames.indexOf(name)]; ctx.lineWidth = 1.15; ctx.beginPath();
    values.forEach((value, index) => {
      const x = index / Math.max(values.length - 1, 1) * width;
      const y = middle - value / peak * lane * .38;
      if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.fillStyle = colors[channelNames.indexOf(name)]; ctx.font = "10px Consolas";
    ctx.fillText(`${labels[laneIndex]} · CH ${name} · ±${peak.toPrecision(4)} V`, 8, laneIndex * lane + 15);
  });
  ctx.fillStyle = "#708790"; ctx.font = "10px Consolas";
  ctx.fillText(formatTime(tMin), 6, height - 6); ctx.textAlign = "right"; ctx.fillText(formatTime(tMax), width - 6, height - 6); ctx.textAlign = "left";
}

let analysisResult = null;
let analysisSource = "";
let analysisFullRangeUs = null;
let archiveResult = null;
let archiveRunPreview = null;
let timeFrequencyResult = null;
let experimentalModeResult = null;
let analysisSelectedChannels = [];
let analysisRequestId = 0;
let analysisRefreshTimer = null;
let spectrumWindowCounter = 0;
const hiddenWaveSeries = new Set();
const hiddenSpectrumSeries = new Set();
const spectrumWindowColors = ["#35d0ba", "#ffb64d", "#68a8ff", "#ff6f82", "#b88cff", "#59d5ec", "#d6e86c", "#e990c8"];

function currentPage() {
  const part = location.pathname.replace(/^\/+|\/+$/g, "");
  if (part === "analysis") return "file-analysis";
  return ["measure", "sweep", "lcr", "file-analysis", "sweep-analysis"].includes(part)
    ? part
    : "measure";
}

function updateSweepBaseSummary() {
  const enabled = channelNames.filter((name) => $(`ch${name}`).checked).join("/");
  const samplePlan = captureSampleCounts();
  $("sweepBaseSummary").innerHTML = [
    ["采样率", `${number("sampleRate")} MS/s`, `${enabled || "无"} 通道`],
    ["记录长度", `${samplePlan.durationUs} μs`, `${samplePlan.total.toLocaleString("zh-CN")} 点 · 触发 ${samplePlan.triggerPercent.toFixed(1)}%`],
    ["基础激励", `${number("awgFrequency")} kHz`, `${number("awgCycles")} 周期`],
    ["输出", `${number("awgVpp")} Vpp`, $("waveform").selectedOptions[0]?.textContent || $("waveform").value],
  ].map(([label, value, detail]) => `<article><span>${label}</span><strong>${value}</strong><small>${detail}</small></article>`).join("");
}

function showPage(page, push = false) {
  document.body.dataset.page = page;
  document.querySelectorAll("[data-nav]").forEach((link) => {
    link.classList.toggle("active", link.dataset.nav === page);
  });
  if (push && location.pathname !== `/${page}`) history.pushState({ page }, "", `/${page}`);
  if (page === "sweep") {
    updateSweepBaseSummary();
    drawSweepResult();
    drawSweepRun();
  } else if (page === "measure") {
    drawScope();
    scheduleAwgPreview();
  } else if (page === "file-analysis") {
    drawAnalysisWaveform();
    drawSpectrum();
    drawTimeFrequency();
    drawExperimentalModes();
  } else if (page === "sweep-analysis") {
    drawArchiveHeatmap();
    drawArchiveRun();
  } else if (page === "lcr") {
    updateLcrControls();
    refreshLcrCalibrations();
    drawLcrParameterCharts();
    drawLcrBode();
    drawLcrNyquist();
    drawLcrWave();
  }
}

const persistedIds = [
  "simulate", "sampleRate", "inputRange", "captureDurationUs", "triggerPositionPercent",
  "triggerEnabled", "triggerSource", "triggerLevel", "triggerTimeout",
  "measureFilterEnabled", "measureBandLow", "measureBandHigh", "measureTransition",
  "awgEnabled", "waveform", "awgFrequency", "awgCycles", "awgVpp", "awgOffset",
  "awgBufferSamples", "cancelAmplitude", "cancelFrequency", "cancelCycles",
  "cancelPhase", "cancelDelayCycles", "rampUpCycles", "holdCycles", "rampDownCycles",
  "holdLevel", "sweepMode", "frequencyStart", "frequencyStop", "frequencyStep",
  "cyclesStart", "cyclesStop", "cyclesStep", "sweepRepeats", "sweepInterval",
  "metricBandLow", "metricBandHigh", "directSearchStart", "directSearchEnd",
  "reflectionDelayMin", "reflectionDelayMax", "packetWindowScale", "tailGuardAfter",
  "tailGuardBefore", "noiseStart", "noiseEnd", "settlingThreshold",
  "settlingHoldCycles", "recommendationMaxDuration", "recommendationMinStrength",
  "recommendationStrongMinStrength", "recommendationMinReflectionSnr",
  "sweepTransmitterChannel",
  "analysisPath", "analysisBandLow", "analysisBandHigh", "analysisTransition",
  "spectrumFreqMin", "spectrumFreqMax",
  "lcrSimulate", "lcrSampleRate", "lcrTriggerLevel", "lcrVoltageChannel",
  "lcrCurrentChannel", "lcrVoltageRange", "lcrCurrentRange", "lcrTriggerSignal",
  "lcrMode", "lcrFrequency", "lcrFrequencyStart",
  "lcrFrequencyStop", "lcrFrequencyStep", "lcrPointsPerDecade", "lcrVpp",
  "lcrBurstCycles", "lcrRampCycles", "lcrAnalysisCycles", "lcrGuardCycles",
  "lcrRepeats", "lcrInterval", "lcrFeedbackResistance", "lcrVoltageGain",
  "lcrCurrentGain", "lcrCurrentPolarity", "lcrImpedanceScale",
  "lcrPhaseCorrection", "lcrSeriesR", "lcrSeriesX", "lcrParallelG", "lcrParallelB",
  "lcrCalibrationResistance", "lcrCalibrationFile",
  "lcrFrequencyAxisScale", "lcrFrequencyUnit", "lcrResistanceUnit",
  "lcrCapacitanceUnit", "lcrInductanceUnit",
];

function restoreSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem("pico4824a-settings") || "{}");
    persistedIds.forEach((id) => {
      const element = $(id);
      if (!element || !(id in saved)) return;
      if (element.type === "checkbox") element.checked = Boolean(saved[id]);
      else element.value = saved[id];
    });
    channelNames.forEach((name) => {
      if (saved[`ch${name}`] !== undefined) $(`ch${name}`).checked = Boolean(saved[`ch${name}`]);
      if (saved[`chRange${name}`] !== undefined) $(`chRange${name}`).value = saved[`chRange${name}`];
    });
    channelNames.forEach((name) => {
      if (saved[`sweepRx${name}`] !== undefined) $(`sweepRx${name}`).checked = Boolean(saved[`sweepRx${name}`]);
    });
  } catch (_) {
    // Corrupt browser preferences should never block instrument control.
  }
}

function persistSettings() {
  const saved = {};
  persistedIds.forEach((id) => {
    const element = $(id);
    if (!element) return;
    saved[id] = element.type === "checkbox" ? element.checked : element.value;
  });
  channelNames.forEach((name) => {
    saved[`ch${name}`] = $(`ch${name}`).checked;
    saved[`chRange${name}`] = $(`chRange${name}`).value;
    saved[`sweepRx${name}`] = $(`sweepRx${name}`).checked;
  });
  localStorage.setItem("pico4824a-settings", JSON.stringify(saved));
}

function addSpectrumWindow(startUs = null, endUs = null, label = null) {
  if ($("spectrumWindows").children.length >= 8) {
    setEvent("最多支持8个同时比较的频谱时段。", "error");
    return;
  }
  const start = startUs ?? number("analysisTimeStart");
  const end = endUs ?? number("analysisTimeEnd");
  const index = spectrumWindowCounter++;
  const row = document.createElement("div");
  row.className = "spectrum-window-row";
  row.dataset.windowId = String(index);
  const color = spectrumWindowColors[index % spectrumWindowColors.length];
  row.innerHTML = `<i style="background:${color}"></i>
    <input class="spec-label" aria-label="时段名称" value="${label || `时段 ${index + 1}`}">
    <input class="spec-start" type="number" step="1" aria-label="起点微秒" value="${Number(start).toFixed(3)}">
    <input class="spec-end" type="number" step="1" aria-label="终点微秒" value="${Number(end).toFixed(3)}">
    <button class="spec-remove" title="删除时段">×</button>`;
  row.dataset.color = color;
  row.querySelector(".spec-remove").addEventListener("click", () => {
    row.remove();
    scheduleAnalysisRefresh();
  });
  row.querySelectorAll("input").forEach((input) => input.addEventListener("change", scheduleAnalysisRefresh));
  $("spectrumWindows").appendChild(row);
}

function spectrumWindowsPayload() {
  return [...$("spectrumWindows").children].map((row) => ({
    label: row.querySelector(".spec-label").value,
    start_us: Number(row.querySelector(".spec-start").value),
    end_us: Number(row.querySelector(".spec-end").value),
    color: row.dataset.color,
  }));
}

function buildAnalysisPayload(initial = false) {
  const payload = {
    path: analysisSource,
    channels: analysisSelectedChannels,
    filter: {
      enabled: $("analysisFilterEnabled").checked,
      low_hz: number("analysisBandLow") * 1000,
      high_hz: number("analysisBandHigh") * 1000,
      transition_hz: number("analysisTransition") * 1000,
    },
    show_raw: $("analysisShowRaw").checked,
    show_filtered: $("analysisShowFiltered").checked,
    spectrum: {
      source: $("spectrumSource").value,
      mode: $("spectrumMode").value,
      window_function: $("spectrumWindowFunction").value,
      frequency_min_hz: number("spectrumFreqMin") * 1000,
      frequency_max_hz: number("spectrumFreqMax") * 1000,
      windows: initial ? [] : spectrumWindowsPayload(),
    },
  };
  if (!initial) {
    payload.time_start_us = number("analysisTimeStart");
    payload.time_end_us = number("analysisTimeEnd");
  }
  return payload;
}

function buildAnalysisChannelButtons(names) {
  const available = new Set(names);
  analysisSelectedChannels = channelNames.filter((name) => available.has(name));
  $("analysisChannels").innerHTML = "";
  channelNames.forEach((name) => {
    const button = document.createElement("button");
    button.textContent = `CH ${name}`;
    button.dataset.channel = name;
    button.disabled = !available.has(name);
    button.title = available.has(name) ? `显示CH ${name}` : `当前文件没有CH ${name}`;
    button.className = available.has(name) ? "active" : "unavailable";
    button.addEventListener("click", () => {
      if (button.disabled) return;
      button.classList.toggle("active");
      analysisSelectedChannels = [...$("analysisChannels").querySelectorAll("button.active:not(:disabled)")].map((item) => item.dataset.channel);
      if (!analysisSelectedChannels.length) {
        button.classList.add("active");
        analysisSelectedChannels = [name];
      }
      scheduleAnalysisRefresh();
    });
    button.addEventListener("dblclick", () => {
      if (button.disabled) return;
      $("analysisChannels").querySelectorAll("button:not(:disabled)").forEach((item) => item.classList.toggle("active", item === button));
      analysisSelectedChannels = [name];
      scheduleAnalysisRefresh();
    });
    $("analysisChannels").appendChild(button);
  });
}

async function browseAnalysisPath() {
  try {
    const path = $("analysisPath").value.trim();
    const result = await api("/api/analysis/browse", {
      method: "POST",
      body: JSON.stringify({ path }),
    });
    const select = $("analysisFileList");
    select.innerHTML = "";
    result.files.forEach((file) => {
      const option = new Option(
        result.kind === "folder" ? file.replace(result.root, "").replace(/^[\\/]/, "") : file,
        file,
      );
      select.add(option);
    });
    $("analysisMeta").textContent = result.files.length
      ? `${result.kind === "folder" ? "目录" : "文件"} · ${result.files.length} 个可分析数据${result.truncated ? "（列表已截断）" : ""}`
      : "该目录中没有NPZ或CSV数据";
    if (result.files.length) {
      select.selectedIndex = 0;
      await loadSelectedAnalysisFile();
    }
  } catch (error) {
    $("analysisMeta").textContent = error.message;
    setEvent(error.message, "error");
  }
}

async function loadSelectedAnalysisFile() {
  const selected = $("analysisFileList").value;
  if (!selected) return;
  analysisSource = selected;
  hiddenWaveSeries.clear();
  hiddenSpectrumSeries.clear();
  try {
    const requestId = ++analysisRequestId;
    const result = await api("/api/analysis/process", {
      method: "POST",
      body: JSON.stringify(buildAnalysisPayload(true)),
    });
    if (requestId !== analysisRequestId) return;
    analysisResult = result;
    const metadata = result.metadata;
    analysisFullRangeUs = [metadata.time_start_s * 1e6, metadata.time_end_s * 1e6];
    $("analysisTimeStart").value = analysisFullRangeUs[0].toFixed(3);
    $("analysisTimeEnd").value = analysisFullRangeUs[1].toFixed(3);
    buildAnalysisChannelButtons(metadata.channels);
    const allChannelOptions = channelNames.map((name) =>
      `<option value="${name}" ${metadata.channels.includes(name) ? "" : "disabled"}>CH ${name}${metadata.channels.includes(name) ? "" : "（无数据）"}</option>`
    ).join("");
    $("timeFrequencyChannel").innerHTML = allChannelOptions;
    $("timeFrequencyChannel").value = metadata.channels.includes("A") ? "A" : metadata.channels[0];
    const pairOptions = allChannelOptions;
    $("modeReferenceChannel").innerHTML = pairOptions;
    $("modeComparisonChannel").innerHTML = pairOptions;
    $("modeReferenceChannel").value = metadata.channels.includes("A") ? "A" : metadata.channels[0];
    $("modeComparisonChannel").value = metadata.channels.includes("G")
      ? "G"
      : (metadata.channels.find((name) => name !== $("modeReferenceChannel").value) || metadata.channels[0]);
    $("spectrumWindows").innerHTML = "";
    spectrumWindowCounter = 0;
    addSpectrumWindow(analysisFullRangeUs[0], analysisFullRangeUs[1], "全记录");
    $("timeFrequencyStart").value = analysisFullRangeUs[0].toFixed(3);
    $("timeFrequencyEnd").value = analysisFullRangeUs[1].toFixed(3);
    $("analysisMeta").textContent = `${metadata.source} · ${metadata.samples.toLocaleString("zh-CN")} 点 · ${(metadata.sample_rate_hz / 1e6).toFixed(4)} MS/s · CH ${metadata.channels.join("/")}`;
    updateAnalysisResult(result);
    setEvent(`已加载：${metadata.source}`, "complete");
    await refreshAnalysis();
  } catch (error) {
    $("analysisMeta").textContent = error.message;
    setEvent(error.message, "error");
  }
}

function scheduleAnalysisRefresh() {
  if (!analysisSource) return;
  clearTimeout(analysisRefreshTimer);
  analysisRefreshTimer = setTimeout(refreshAnalysis, 180);
}

async function refreshAnalysis() {
  if (!analysisSource) return;
  const requestId = ++analysisRequestId;
  try {
    const result = await api("/api/analysis/process", {
      method: "POST",
      body: JSON.stringify(buildAnalysisPayload(false)),
    });
    if (requestId !== analysisRequestId) return;
    analysisResult = result;
    updateAnalysisResult(result);
  } catch (error) {
    if (requestId === analysisRequestId) setEvent(error.message, "error");
  }
}

function waveSeriesColor(key) {
  const [channel, kind] = key.split(":");
  const base = colors[Math.max(0, channelNames.indexOf(channel))];
  return { color: base, alpha: kind === "raw" ? 0.52 : 1, dashed: kind === "raw" };
}

function updateAnalysisResult(result) {
  $("analysisWaveEmpty").style.display = "none";
  $("spectrumEmpty").style.display = "none";
  const keys = Object.keys(result.waveform);
  $("analysisWaveLegend").innerHTML = keys.map((key) => {
    const style = waveSeriesColor(key);
    return `<button data-wave-series="${key}" class="${hiddenWaveSeries.has(key) ? "hidden-series" : ""}"><i style="background:${style.color};opacity:${style.alpha}"></i>${key.replace(":", " · ")}</button>`;
  }).join("");
  $("analysisWaveLegend").querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.waveSeries;
      if (hiddenWaveSeries.has(key)) hiddenWaveSeries.delete(key); else hiddenWaveSeries.add(key);
      button.classList.toggle("hidden-series");
      drawAnalysisWaveform();
    });
  });
  const legendItems = [];
  result.spectra.forEach((window) => Object.keys(window.channels).forEach((channel) => {
    const key = `${window.label}|${channel}`;
    legendItems.push({ key, label: `${window.label} · CH ${channel}`, color: window.color });
  }));
  $("spectrumLegend").innerHTML = legendItems.map((item) => `<button data-spectrum-series="${item.key}" class="${hiddenSpectrumSeries.has(item.key) ? "hidden-series" : ""}"><i style="background:${item.color}"></i>${item.label}</button>`).join("");
  $("spectrumLegend").querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.spectrumSeries;
      if (hiddenSpectrumSeries.has(key)) hiddenSpectrumSeries.delete(key); else hiddenSpectrumSeries.add(key);
      button.classList.toggle("hidden-series");
      drawSpectrum();
    });
  });
  const firstFrequency = result.spectra[0]?.frequency_hz || [];
  const resolution = firstFrequency.length > 1 ? firstFrequency[1] - firstFrequency[0] : NaN;
  $("spectrumResolution").textContent = Number.isFinite(resolution) ? `显示频率间隔约 ${(resolution / 1000).toFixed(3)} kHz` : "—";
  drawAnalysisWaveform();
  drawSpectrum();
}

function canvasSetup(canvas) {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  return { ctx, width: rect.width, height: rect.height, rect };
}

function drawAxes(ctx, width, height, left, top, right, bottom, xLabels, yLabels) {
  ctx.strokeStyle = "rgba(120,144,156,.19)";
  ctx.lineWidth = 1;
  ctx.font = "10px Consolas";
  for (let index = 0; index <= 5; index += 1) {
    const x = left + (right - left) * index / 5;
    ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke();
    ctx.fillStyle = "#69808a"; ctx.textAlign = "center";
    ctx.fillText(xLabels(index / 5), x, height - 9);
  }
  for (let index = 0; index <= 4; index += 1) {
    const y = top + (bottom - top) * index / 4;
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
    ctx.fillStyle = "#69808a"; ctx.textAlign = "right";
    ctx.fillText(yLabels(1 - index / 4), left - 7, y + 4);
  }
  ctx.textAlign = "left";
}

function drawAnalysisWaveform() {
  if (!analysisResult || document.body.dataset.page !== "file-analysis") return;
  const canvas = $("analysisWaveCanvas");
  const { ctx, width, height } = canvasSetup(canvas);
  const timesUs = analysisResult.time_s.map((value) => value * 1e6);
  if (!timesUs.length) return;
  const visibleKeys = Object.keys(analysisResult.waveform).filter((key) => !hiddenWaveSeries.has(key));
  const channels = [...new Set(visibleKeys.map((key) => key.split(":")[0]))];
  const stacked = $("analysisLayout").value === "stacked";
  const laneCount = stacked ? Math.max(1, channels.length) : 1;
  const left = 68, right = width - 16, top = 16, bottom = height - 30;
  const tMin = timesUs[0], tMax = timesUs[timesUs.length - 1];
  ctx.clearRect(0, 0, width, height);
  drawAxes(
    ctx, width, height, left, top, right, bottom,
    (ratio) => `${(tMin + (tMax - tMin) * ratio).toFixed(1)} μs`,
    () => "",
  );

  spectrumWindowsPayload().forEach((window) => {
    const x1 = left + (window.start_us - tMin) / Math.max(tMax - tMin, 1e-30) * (right - left);
    const x2 = left + (window.end_us - tMin) / Math.max(tMax - tMin, 1e-30) * (right - left);
    if (x2 >= left && x1 <= right) {
      ctx.fillStyle = `${window.color}12`;
      ctx.fillRect(Math.max(left, x1), top, Math.min(right, x2) - Math.max(left, x1), bottom - top);
    }
  });

  const manual = $("analysisAmplitudeMode").value === "manual";
  const manualMin = number("analysisAmpMin");
  const manualMax = number("analysisAmpMax");
  let overlayMin = manualMin, overlayMax = manualMax;
  if (!stacked && !manual) {
    const values = visibleKeys.flatMap((key) => analysisResult.waveform[key]);
    overlayMin = Math.min(...values);
    overlayMax = Math.max(...values);
    const overlaySpan = Math.max(
      overlayMax - overlayMin,
      Math.max(Math.abs(overlayMax), Math.abs(overlayMin)) * .02,
      1e-12,
    );
    overlayMin -= overlaySpan * .08;
    overlayMax += overlaySpan * .08;
  }
  channels.forEach((channel, channelIndex) => {
    const keys = visibleKeys.filter((key) => key.startsWith(`${channel}:`));
    if (!keys.length) return;
    const laneIndex = stacked ? channelIndex : 0;
    const laneTop = top + (bottom - top) * laneIndex / laneCount;
    const laneBottom = top + (bottom - top) * (laneIndex + 1) / laneCount;
    let yMin = manual ? manualMin : (!stacked ? overlayMin : Infinity);
    let yMax = manual ? manualMax : (!stacked ? overlayMax : -Infinity);
    if (!manual && stacked) {
      keys.forEach((key) => {
        analysisResult.waveform[key].forEach((value) => {
          yMin = Math.min(yMin, value); yMax = Math.max(yMax, value);
        });
      });
      const span = Math.max(yMax - yMin, Math.max(Math.abs(yMax), Math.abs(yMin)) * .02, 1e-12);
      yMin -= span * .08; yMax += span * .08;
    }
    if (yMax <= yMin) yMax = yMin + 1e-12;
    const zeroY = laneBottom - (0 - yMin) / (yMax - yMin) * (laneBottom - laneTop);
    if (zeroY >= laneTop && zeroY <= laneBottom) {
      ctx.strokeStyle = "rgba(180,202,210,.2)";
      ctx.beginPath(); ctx.moveTo(left, zeroY); ctx.lineTo(right, zeroY); ctx.stroke();
    }
    keys.forEach((key) => {
      const values = analysisResult.waveform[key];
      const style = waveSeriesColor(key);
      ctx.strokeStyle = style.color;
      ctx.globalAlpha = style.alpha;
      ctx.lineWidth = key.endsWith(":filtered") ? 1.35 : 1;
      ctx.setLineDash(style.dashed ? [4, 3] : []);
      ctx.beginPath();
      values.forEach((value, index) => {
        const x = left + index / Math.max(values.length - 1, 1) * (right - left);
        const y = laneBottom - (value - yMin) / (yMax - yMin) * (laneBottom - laneTop);
        if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.setLineDash([]);
    });
    if (stacked || channelIndex === 0) {
      ctx.fillStyle = stacked ? colors[Math.max(0, channelNames.indexOf(channel))] : "#8ca1aa";
      ctx.font = "10px Consolas";
      ctx.fillText(stacked ? `CH ${channel}` : "叠加", 7, laneTop + 15);
      ctx.fillStyle = "#607984";
      ctx.fillText(`${yMax.toPrecision(3)} V`, 7, laneTop + 29);
      ctx.fillText(`${yMin.toPrecision(3)} V`, 7, laneBottom - 5);
    }
  });
}

function drawSpectrum() {
  if (!analysisResult || document.body.dataset.page !== "file-analysis") return;
  const canvas = $("spectrumCanvas");
  const { ctx, width, height } = canvasSetup(canvas);
  const series = [];
  analysisResult.spectra.forEach((window, windowIndex) => {
    Object.entries(window.channels).forEach(([channel, values]) => {
      const key = `${window.label}|${channel}`;
      if (hiddenSpectrumSeries.has(key)) return;
      const transformed = values.map((value) => {
        if ($("spectrumScale").value === "linear") return value;
        return $("spectrumMode").value === "psd"
          ? 10 * Math.log10(Math.max(value, 1e-30))
          : 20 * Math.log10(Math.max(value, 1e-30));
      });
      series.push({
        key,
        channel,
        frequency: window.frequency_hz.map((value) => value / 1000),
        values: transformed,
        color: window.color,
        dash: channelNames.indexOf(channel) % 4,
        windowIndex,
      });
    });
  });
  ctx.clearRect(0, 0, width, height);
  if (!series.length) return;
  const allValues = series.flatMap((item) => item.values.filter(Number.isFinite));
  let yMin = Math.min(...allValues), yMax = Math.max(...allValues);
  const span = Math.max(yMax - yMin, 1e-12);
  yMin -= span * .08; yMax += span * .08;
  const xMin = Math.min(...series.flatMap((item) => item.frequency));
  const xMax = Math.max(...series.flatMap((item) => item.frequency));
  const left = 72, right = width - 16, top = 16, bottom = height - 31;
  drawAxes(
    ctx, width, height, left, top, right, bottom,
    (ratio) => `${(xMin + (xMax - xMin) * ratio).toFixed(1)}`,
    (ratio) => (yMin + (yMax - yMin) * ratio).toFixed($("spectrumScale").value === "db" ? 1 : 4),
  );
  ctx.fillStyle = "#6d858f"; ctx.font = "10px Consolas";
  ctx.fillText("kHz", right - 18, height - 9);
  series.forEach((item) => {
    const dashPatterns = [[], [6, 3], [2, 3], [8, 3, 2, 3]];
    ctx.strokeStyle = item.color;
    ctx.lineWidth = 1.25;
    ctx.setLineDash(dashPatterns[item.dash]);
    ctx.beginPath();
    item.values.forEach((value, index) => {
      const x = left + (item.frequency[index] - xMin) / Math.max(xMax - xMin, 1e-30) * (right - left);
      const y = bottom - (value - yMin) / (yMax - yMin) * (bottom - top);
      if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
  ctx.setLineDash([]);
}

async function exportAnalysis(format) {
  if (!analysisSource) {
    setEvent("请先加载一个数据文件。", "error");
    return;
  }
  try {
    const reply = await api("/api/analysis/export", {
      method: "POST",
      body: JSON.stringify({
        path: analysisSource,
        format,
        filter: {
          low_hz: number("analysisBandLow") * 1000,
          high_hz: number("analysisBandHigh") * 1000,
          transition_hz: number("analysisTransition") * 1000,
        },
      }),
    });
    setEvent(`派生数据已保存：${reply.path}`, "complete");
  } catch (error) {
    setEvent(error.message, "error");
  }
}

function resetAnalysisView() {
  if (!analysisFullRangeUs) return;
  $("analysisTimeStart").value = analysisFullRangeUs[0].toFixed(3);
  $("analysisTimeEnd").value = analysisFullRangeUs[1].toFixed(3);
  scheduleAnalysisRefresh();
}

function installAnalysisCanvasInteraction() {
  const canvas = $("analysisWaveCanvas");
  let drag = null;
  canvas.addEventListener("wheel", (event) => {
    if (!analysisFullRangeUs) return;
    event.preventDefault();
    if (event.shiftKey) {
      const values = Object.values(analysisResult?.waveform || {}).flat();
      const currentMin = $("analysisAmplitudeMode").value === "manual" ? number("analysisAmpMin") : Math.min(...values);
      const currentMax = $("analysisAmplitudeMode").value === "manual" ? number("analysisAmpMax") : Math.max(...values);
      const factor = event.deltaY > 0 ? 1.2 : 0.82;
      const center = (currentMin + currentMax) / 2;
      const half = Math.max((currentMax - currentMin) / 2 * factor, 1e-12);
      $("analysisAmplitudeMode").value = "manual";
      $("analysisAmpMin").disabled = false;
      $("analysisAmpMax").disabled = false;
      $("analysisAmpMin").value = (center - half).toPrecision(7);
      $("analysisAmpMax").value = (center + half).toPrecision(7);
      drawAnalysisWaveform();
      return;
    }
    const start = number("analysisTimeStart"), end = number("analysisTimeEnd");
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    const anchor = start + (end - start) * ratio;
    const factor = event.deltaY > 0 ? 1.25 : 0.8;
    const minimumSpan = Math.max((analysisFullRangeUs[1] - analysisFullRangeUs[0]) / 10000, 0.01);
    const newSpan = Math.max(minimumSpan, Math.min((end - start) * factor, analysisFullRangeUs[1] - analysisFullRangeUs[0]));
    let nextStart = anchor - newSpan * ratio;
    let nextEnd = nextStart + newSpan;
    if (nextStart < analysisFullRangeUs[0]) { nextEnd += analysisFullRangeUs[0] - nextStart; nextStart = analysisFullRangeUs[0]; }
    if (nextEnd > analysisFullRangeUs[1]) { nextStart -= nextEnd - analysisFullRangeUs[1]; nextEnd = analysisFullRangeUs[1]; }
    $("analysisTimeStart").value = nextStart.toFixed(3);
    $("analysisTimeEnd").value = nextEnd.toFixed(3);
    scheduleAnalysisRefresh();
  }, { passive: false });
  canvas.addEventListener("mousedown", (event) => {
    drag = { x: event.clientX, start: number("analysisTimeStart"), end: number("analysisTimeEnd") };
  });
  window.addEventListener("mouseup", () => {
    if (drag) scheduleAnalysisRefresh();
    drag = null;
  });
  canvas.addEventListener("mousemove", (event) => {
    if (!analysisResult) return;
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    if (drag && analysisFullRangeUs) {
      const delta = -(event.clientX - drag.x) / rect.width * (drag.end - drag.start);
      let start = drag.start + delta, end = drag.end + delta;
      if (start < analysisFullRangeUs[0]) { end += analysisFullRangeUs[0] - start; start = analysisFullRangeUs[0]; }
      if (end > analysisFullRangeUs[1]) { start -= end - analysisFullRangeUs[1]; end = analysisFullRangeUs[1]; }
      $("analysisTimeStart").value = start.toFixed(3);
      $("analysisTimeEnd").value = end.toFixed(3);
    }
    const times = analysisResult.time_s;
    const index = Math.min(times.length - 1, Math.max(0, Math.round(ratio * (times.length - 1))));
    const firstKey = Object.keys(analysisResult.waveform).find((key) => !hiddenWaveSeries.has(key));
    $("analysisCursorTime").textContent = `t = ${(times[index] * 1e6).toFixed(3)} μs`;
    $("analysisCursorValue").textContent = firstKey ? `${firstKey} = ${Number(analysisResult.waveform[firstKey][index]).toPrecision(5)} V` : "V = —";
  });
  canvas.addEventListener("dblclick", resetAnalysisView);
}

const archiveMetricLabels = {
  tail_to_direct_db: "拖尾 / 直达波 (dB)",
  reflection_to_direct_db: "端面反射 / 直达波 (dB)",
  quality_db: "综合质量 (dB)",
  receiver_tail_rms_v: "拖尾 RMS (V)",
  tail_to_direct_db_std: "拖尾/直达重复标准差 (dB)",
  reflection_to_direct_db_std: "反射/直达重复标准差 (dB)",
  quality_db_std: "质量重复标准差 (dB)",
  receiver_tail_rms_v_std: "拖尾RMS重复标准差 (V)",
  reflection_snr_db: "端面反射 SNR (dB)",
  strength_db_from_max: "相对信号强度 (dB)",
  settling_to_threshold_us: "稳定时间 (μs)",
  nominal_duration_us: "N/f 理论时长 (μs)",
};

async function loadSweepArchive(path) {
  try {
    const result = await api("/api/analysis/sweep-folder", {
      method: "POST",
      body: JSON.stringify({ path }),
    });
    archiveResult = result.is_sweep ? result : null;
    $("scanArchivePanel").hidden = !result.is_sweep;
    if (!result.is_sweep) {
      $("archiveLoadMeta").textContent = "该目录不是完整的参数扫描归档";
      return;
    }
    $("archivePath").value = result.directory;
    $("archiveLoadMeta").textContent = `已加载 · ${result.points} 参数点 · ${result.runs_completed} 次独立采集`;
    populateArchiveEvaluationControls(result);
    $("archiveSummary").textContent = `${result.directory} · ${result.frequencies_hz.length} 个频率 × ${result.cycles.length} 个周期`;
    $("archiveMetric").innerHTML = result.metrics.map((key) => `<option value="${key}">${archiveMetricLabels[key] || key}</option>`).join("");
    if (result.metrics.includes("tail_to_direct_db")) $("archiveMetric").value = "tail_to_direct_db";
    updateArchiveTable();
    updateArchiveRunsTable();
    updateArchiveConfigCards();
    updateArchiveRecommendations();
    archiveRunPreview = null;
    $("archiveRunEmpty").style.display = "grid";
    $("archiveRunLegend").innerHTML = "";
    drawArchiveHeatmap();
    setEvent(`参数扫描归档已加载：${result.directory}`, "complete");
  } catch (error) {
    archiveResult = null;
    $("scanArchivePanel").hidden = true;
    $("archiveLoadMeta").textContent = error.message;
    setEvent(`扫描归档读取失败：${error.message}`, "error");
  }
}

function populateArchiveEvaluationControls(result) {
  const sweep = result.config.sweep || {};
  const baseChannels = result.config.base_config?.channels || {};
  const enabled = channelNames.filter((name) => baseChannels[name]?.enabled !== false);
  const receiverChannels = (sweep.receiver_channels || []).map(String);
  const transmitter = String(sweep.transmitter_channel || "NONE").toUpperCase();
  $("archiveTransmitterChannel").value = channelNames.includes(transmitter) ? transmitter : "NONE";
  channelNames.forEach((name) => {
    const checkbox = $(`archiveRx${name}`);
    const available = enabled.includes(name) && name !== $("archiveTransmitterChannel").value;
    checkbox.disabled = !available;
    checkbox.checked = available && receiverChannels.includes(name);
    checkbox.closest("label").classList.toggle("disabled", !available);
  });
  const values = {
    archiveMetricBandLow: Number(sweep.metric_band_low_hz || 20000) / 1000,
    archiveMetricBandHigh: Number(sweep.metric_band_high_hz || 180000) / 1000,
    archiveDirectStart: sweep.direct_search_start_us ?? 90,
    archiveDirectEnd: sweep.direct_search_end_us ?? 440,
    archiveReflectionMin: sweep.reflection_delay_min_us ?? 420,
    archiveReflectionMax: sweep.reflection_delay_max_us ?? 570,
    archivePacketScale: sweep.packet_window_scale ?? 1,
    archiveTailAfter: sweep.tail_guard_after_cycles ?? 0,
    archiveTailBefore: sweep.tail_guard_before_cycles ?? 0,
    archiveNoiseStart: sweep.noise_start_us ?? -95,
    archiveNoiseEnd: sweep.noise_end_us ?? -20,
    archiveSettlingThreshold: Number(sweep.settling_threshold_ratio ?? 0.1) * 100,
    archiveSettlingHold: sweep.settling_hold_cycles ?? 2,
    archiveRecommendationDuration: sweep.recommendation_max_duration_us ?? 100,
    archiveRecommendationStrength: sweep.recommendation_min_strength_db ?? -12,
    archiveRecommendationStrong: sweep.recommendation_strong_min_strength_db ?? -6,
    archiveRecommendationSnr: sweep.recommendation_min_reflection_snr_db ?? 25,
  };
  Object.entries(values).forEach(([id, value]) => { $(id).value = value; });
  syncArchiveReceiverChannels();
}

function syncArchiveReceiverChannels() {
  const baseChannels = archiveResult?.config?.base_config?.channels || {};
  const enabled = channelNames.filter((name) => baseChannels[name]?.enabled !== false);
  const transmitter = $("archiveTransmitterChannel").value;
  channelNames.forEach((name) => {
    const checkbox = $(`archiveRx${name}`);
    const available = enabled.includes(name) && name !== transmitter;
    if (!available) checkbox.checked = false;
    checkbox.disabled = !available;
    checkbox.closest("label").classList.toggle("disabled", !available);
  });
  let selected = channelNames.filter((name) => $(`archiveRx${name}`).checked && !$(`archiveRx${name}`).disabled);
  if (!selected.length) {
    const fallback = enabled.find((name) => name !== transmitter);
    if (fallback) {
      $(`archiveRx${fallback}`).checked = true;
      selected = [fallback];
    }
  }
  $("archiveReceiverSummary").textContent = selected.length ? selected.join("/") : "无可用通道";
  return selected;
}

function archiveEvaluationPayload() {
  return {
    metric_band_low_hz: number("archiveMetricBandLow") * 1000,
    metric_band_high_hz: number("archiveMetricBandHigh") * 1000,
    direct_search_start_us: number("archiveDirectStart"),
    direct_search_end_us: number("archiveDirectEnd"),
    reflection_delay_min_us: number("archiveReflectionMin"),
    reflection_delay_max_us: number("archiveReflectionMax"),
    packet_window_scale: number("archivePacketScale"),
    tail_guard_after_cycles: number("archiveTailAfter"),
    tail_guard_before_cycles: number("archiveTailBefore"),
    noise_start_us: number("archiveNoiseStart"),
    noise_end_us: number("archiveNoiseEnd"),
    settling_threshold_ratio: number("archiveSettlingThreshold") / 100,
    settling_hold_cycles: number("archiveSettlingHold"),
    recommendation_max_duration_us: number("archiveRecommendationDuration"),
    recommendation_min_strength_db: number("archiveRecommendationStrength"),
    recommendation_strong_min_strength_db: number("archiveRecommendationStrong"),
    recommendation_min_reflection_snr_db: number("archiveRecommendationSnr"),
    receiver_channels: syncArchiveReceiverChannels(),
    transmitter_channel: $("archiveTransmitterChannel").value,
  };
}

async function reevaluateSweepArchive() {
  if (!archiveResult) {
    setEvent("请先加载参数扫描文件夹。", "error");
    return;
  }
  try {
    $("archiveReevaluateBtn").disabled = true;
    $("archiveLoadMeta").textContent = "正在重新评价全部原始采集…";
    const result = await api("/api/analysis/sweep-reevaluate", {
      method: "POST",
      body: JSON.stringify({
        path: archiveResult.directory,
        evaluation: archiveEvaluationPayload(),
      }),
    });
    archiveResult = result;
    $("archiveLoadMeta").textContent = `重新评价完成 · ${result.runs_completed} 次采集 · 原始文件未修改`;
    $("archiveMetric").innerHTML = result.metrics.map((key) => `<option value="${key}">${archiveMetricLabels[key] || key}</option>`).join("");
    if (result.metrics.includes("tail_to_direct_db")) $("archiveMetric").value = "tail_to_direct_db";
    updateArchiveTable();
    updateArchiveRunsTable();
    updateArchiveConfigCards();
    updateArchiveRecommendations();
    drawArchiveHeatmap();
    setEvent("参数扫描全部原始数据已按新参数重新评价。", "complete");
  } catch (error) {
    $("archiveLoadMeta").textContent = error.message;
    setEvent(error.message, "error");
  } finally {
    $("archiveReevaluateBtn").disabled = false;
  }
}

function updateArchiveConfigCards() {
  if (!archiveResult) return;
  const base = archiveResult.config.base_config || {};
  const sweep = archiveResult.config.sweep || {};
  const awg = base.awg || {};
  const totalSamples = Number(base.pre_trigger_samples || 0) + Number(base.post_trigger_samples || 0);
  const sampleRateHz = Number(base.sample_rate_hz || 0);
  const durationUs = sampleRateHz > 0 ? totalSamples / sampleRateHz * 1e6 : NaN;
  const triggerPercent = totalSamples > 0
    ? Number(base.pre_trigger_samples || 0) / totalSamples * 100
    : NaN;
  const samplingDetail = Number.isFinite(durationUs) && Number.isFinite(triggerPercent)
    ? `采集 ${durationUs.toFixed(3)} μs · 触发位置 ${triggerPercent.toFixed(2)}%`
    : "采集时间与触发位置未记录";
  const cards = [
    ["参数网格", `${archiveResult.points} 点`, `${archiveResult.runs_completed} 次原始采集`],
    ["采样率", sampleRateHz ? `${Number(sampleRateHz / 1e6).toFixed(3)} MS/s` : "—", samplingDetail],
    ["扫描模式", sweep.mode || "—", `${sweep.repeats || "—"} 次/点`],
    ["AWG", awg.waveform || "—", `${awg.pk_to_pk_v || "—"} Vpp`],
    ["评价频带", sweep.metric_band_low_hz ? `${Number(sweep.metric_band_low_hz / 1000).toFixed(1)}–${Number(sweep.metric_band_high_hz / 1000).toFixed(1)} kHz` : "—", "保存时参数"],
    ["接收通道", (sweep.receiver_channels || []).join("/") || "旧数据未记录", "按本次扫描配置评价"],
  ];
  $("archiveConfigCards").innerHTML = cards.map(([label, value, detail]) => `<article><span>${label}</span><strong>${value}</strong><small>${detail}</small></article>`).join("");
}

function updateArchiveRecommendations() {
  if (!archiveResult) return;
  const rows = archiveResult.summary || [];
  const minimumBy = (items, key) => items.length
    ? items.reduce((best, row) => Number(row[key]) < Number(best[key]) ? row : best)
    : null;
  const maximumBy = (items, key) => items.length
    ? items.reduce((best, row) => Number(row[key]) > Number(best[key]) ? row : best)
    : null;
  const duration = number("archiveRecommendationDuration");
  const minStrength = number("archiveRecommendationStrength");
  const strongStrength = number("archiveRecommendationStrong");
  const minSnr = number("archiveRecommendationSnr");
  const durationRows = rows.filter((row) => Number(row.nominal_duration_us) <= duration);
  const balancedRows = durationRows.filter((row) =>
    Number(row.strength_db_from_max) >= minStrength
    && Number(row.reflection_snr_db) >= minSnr);
  const strongRows = durationRows.filter((row) =>
    Number(row.strength_db_from_max) >= strongStrength
    && Number(row.reflection_snr_db) >= minSnr);
  const choices = [
    ["全局最低拖尾", minimumBy(rows, "tail_to_direct_db")],
    [`N/f≤${duration.toFixed(0)} μs`, minimumBy(durationRows, "tail_to_direct_db")],
    ["平衡质量最高", maximumBy(balancedRows, "quality_db")],
    ["强信号拖尾最低", minimumBy(strongRows, "tail_to_direct_db")],
  ];
  $("archiveRecommendationCards").innerHTML = choices.map(([label, row]) => `
    <article><span>${label}</span>
      <strong>${row ? `${(Number(row.frequency_hz) / 1000).toFixed(3).replace(/\.000$/, "")} kHz / ${row.cycles} 周期` : "无符合点"}</strong>
      <small>${row ? `N/f ${Number(row.nominal_duration_us).toFixed(2)} μs · 拖尾 ${Number(row.tail_to_direct_db).toFixed(2)} dB` : "请调整约束"}</small>
    </article>`).join("");
}

function firstRunForArchivePoint(frequency, cycles) {
  if (!archiveResult) return null;
  return archiveResult.runs.find((row) =>
    Number(row.frequency_hz) === Number(frequency) && Number(row.cycles) === Number(cycles)
  ) || null;
}

function loadArchiveRun(row) {
  const run = row?.absolute_npz_file
    ? row
    : firstRunForArchivePoint(row.frequency_hz, row.cycles);
  if (!run || !run.absolute_npz_file) {
    setEvent("该参数点没有可读取的原始NPZ。", "error");
    return;
  }
  const select = $("analysisFileList");
  let option = [...select.options].find((item) => item.value === run.absolute_npz_file);
  if (!option) {
    option = new Option(run.absolute_npz_file.split(/[\\/]/).pop(), run.absolute_npz_file);
    select.add(option);
  }
  select.value = run.absolute_npz_file;
  if (Number.isFinite(Number(run.direct_start_us))) {
    $("modeDirectStart").value = Number(run.direct_start_us).toFixed(3);
    $("modeDirectEnd").value = Number(run.direct_end_us).toFixed(3);
    $("modeSearchStart").value = Number(run.tail_start_us).toFixed(3);
    $("modeSearchEnd").value = Number(run.tail_end_us).toFixed(3);
    $("modeEndStart").value = Number(run.reflection_start_us).toFixed(3);
    $("modeEndEnd").value = Number(run.reflection_end_us).toFixed(3);
  }
  loadSelectedAnalysisFile();
  showPage("file-analysis", true);
}

function updateArchiveTable() {
  if (!archiveResult) return;
  $("archiveTableBody").innerHTML = archiveResult.summary.map((row, index) => `
    <tr data-archive-index="${index}">
      <td>${(Number(row.frequency_hz) / 1000).toFixed(3).replace(/\.000$/, "")}</td>
      <td>${row.cycles}</td>
      <td>${Number(row.nominal_duration_us || 0).toFixed(3)}</td>
      <td>${row.repeats_completed}</td>
      <td>${Number(row.tail_to_direct_db).toFixed(2)}</td>
      <td>${Number(row.reflection_to_direct_db).toFixed(2)}</td>
      <td>${Number(row.quality_db).toFixed(2)}</td>
      <td>${Number(row.strength_db_from_max).toFixed(2)}</td>
      <td>${Number(row.reflection_snr_db).toFixed(2)}</td>
      <td>${Number(row.settling_to_threshold_us).toFixed(2)}</td>
    </tr>`).join("");
}

function updateArchiveRunsTable() {
  if (!archiveResult) return;
  $("archiveRunsTableBody").innerHTML = archiveResult.runs.map((row, index) => {
    const fileName = String(row.npz_file || row.absolute_npz_file || "").split(/[\\/]/).pop();
    return `<tr data-archive-run="${index}">
      <td>${row.run_index}</td>
      <td>${(Number(row.frequency_hz) / 1000).toFixed(3).replace(/\.000$/, "")}</td>
      <td>${row.cycles}</td><td>${row.repeat}</td>
      <td>${Number(row.tail_to_direct_db).toFixed(2)}</td>
      <td>${Number(row.reflection_to_direct_db).toFixed(2)}</td>
      <td>${Number(row.quality_db).toFixed(2)}</td>
      <td>${row.receiver_channels || "—"}</td>
      <td title="${row.absolute_npz_file || ""}">${fileName}</td>
      <td><button data-archive-preview="${index}">查看波形</button> <button data-archive-analyze="${index}">单数据分析</button></td>
    </tr>`;
  }).join("");
}

async function loadArchiveRunPreview(rowIndex) {
  if (!archiveResult) return;
  const row = archiveResult.runs[rowIndex];
  if (!row?.absolute_npz_file) {
    setEvent("该次采集缺少可读取的原始NPZ。", "error");
    return;
  }
  try {
    archiveRunPreview = await api("/api/analysis/run-preview", {
      method: "POST",
      body: JSON.stringify({ path: row.absolute_npz_file, max_points: 5000 }),
    });
    $("archiveRunEmpty").style.display = "none";
    $("archiveRunLegend").innerHTML = Object.keys(archiveRunPreview.channels).map((name) =>
      `<span><i style="background:${colors[Math.max(0, channelNames.indexOf(name))]}"></i>CH ${name}</span>`
    ).join("");
    $("archiveRunsTableBody").querySelectorAll("tr").forEach((tr) =>
      tr.classList.toggle("selected", Number(tr.dataset.archiveRun) === Number(rowIndex)));
    drawArchiveRun();
  } catch (error) {
    setEvent(error.message, "error");
  }
}

function drawArchiveRun() {
  if (!archiveRunPreview || document.body.dataset.page !== "sweep-analysis") return;
  const canvas = $("archiveRunCanvas");
  const { ctx, width, height } = canvasSetup(canvas);
  const names = Object.keys(archiveRunPreview.channels);
  if (!names.length) return;
  const lane = height / names.length;
  ctx.clearRect(0, 0, width, height);
  names.forEach((name, channelIndex) => {
    const values = archiveRunPreview.channels[name];
    const peak = Math.max(...values.map(Math.abs), 1e-12);
    const middle = lane * (channelIndex + 0.5);
    const color = colors[Math.max(0, channelNames.indexOf(name))];
    ctx.strokeStyle = "rgba(111,139,150,.18)";
    ctx.beginPath(); ctx.moveTo(0, middle); ctx.lineTo(width, middle); ctx.stroke();
    ctx.strokeStyle = color; ctx.lineWidth = 1.1; ctx.beginPath();
    values.forEach((value, index) => {
      const x = index / Math.max(values.length - 1, 1) * width;
      const y = middle - value / peak * lane * 0.38;
      if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.fillStyle = color; ctx.font = "10px Consolas";
    ctx.fillText(`CH ${name}`, 8, channelIndex * lane + 14);
  });
}

function archiveColor(ratio, higherIsBetter) {
  const good = higherIsBetter ? ratio : 1 - ratio;
  const hue = 18 + 150 * Math.max(0, Math.min(1, good));
  return `hsl(${hue} 62% 45%)`;
}

function drawArchiveHeatmap() {
  if (!archiveResult || document.body.dataset.page !== "sweep-analysis") return;
  const canvas = $("archiveHeatmapCanvas");
  const { ctx, width, height } = canvasSetup(canvas);
  const metric = $("archiveMetric").value;
  const values = archiveResult.summary.map((row) => Number(row[metric])).filter(Number.isFinite);
  if (!values.length) return;
  const minimum = Math.min(...values), maximum = Math.max(...values);
  const span = Math.max(maximum - minimum, 1e-12);
  const frequencies = archiveResult.frequencies_hz;
  const cycles = archiveResult.cycles;
  const left = 58, right = width - 16, top = 18, bottom = height - 38;
  const cellWidth = (right - left) / frequencies.length;
  const cellHeight = (bottom - top) / cycles.length;
  const higherIsBetter = ["quality_db", "reflection_snr_db", "strength_db_from_max"].includes(metric);
  ctx.clearRect(0, 0, width, height);
  archiveResult.summary.forEach((row) => {
    const xIndex = frequencies.indexOf(Number(row.frequency_hz));
    const yIndex = cycles.indexOf(Number(row.cycles));
    const value = Number(row[metric]);
    const ratio = (value - minimum) / span;
    const x = left + xIndex * cellWidth;
    const y = top + (cycles.length - 1 - yIndex) * cellHeight;
    ctx.fillStyle = archiveColor(ratio, higherIsBetter);
    ctx.fillRect(x + 1, y + 1, Math.max(1, cellWidth - 2), Math.max(1, cellHeight - 2));
    if (cellWidth >= 38 && cellHeight >= 24) {
      ctx.fillStyle = "rgba(5,16,20,.84)";
      ctx.font = "9px Consolas";
      ctx.textAlign = "center";
      ctx.fillText(value.toFixed(metric.endsWith("_v") ? 5 : 2), x + cellWidth / 2, y + cellHeight / 2 + 3);
    }
  });
  ctx.fillStyle = "#718892"; ctx.font = "9px Consolas"; ctx.textAlign = "center";
  frequencies.forEach((value, index) => {
    if (frequencies.length <= 16 || index % Math.ceil(frequencies.length / 12) === 0) {
      ctx.fillText((value / 1000).toFixed(1), left + (index + .5) * cellWidth, bottom + 16);
    }
  });
  ctx.textAlign = "right";
  cycles.forEach((value, index) => ctx.fillText(String(value), left - 8, top + (cycles.length - index - .5) * cellHeight + 3));
  ctx.textAlign = "center"; ctx.fillText("频率 / kHz", (left + right) / 2, height - 5);
  canvas._archiveGeometry = { left, right, top, bottom, cellWidth, cellHeight };
}

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

async function calculateTimeFrequency() {
  if (!analysisSource) {
    setEvent("请先加载一个波形文件。", "error");
    return;
  }
  try {
    $("timeFrequencyMeta").textContent = "正在计算…";
    const result = await api("/api/analysis/time-frequency", {
      method: "POST",
      body: JSON.stringify({
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
      }),
    });
    timeFrequencyResult = result;
    $("timeFrequencyEmpty").style.display = "none";
    $("timeFrequencyTitle").textContent = `${result.method.toUpperCase()}时频图 · CH ${$("timeFrequencyChannel").value}`;
    $("tfFloorLabel").textContent = `${result.floor_db} dB`;
    const details = result.details;
    $("timeFrequencyMeta").textContent = result.method === "stft"
      ? `窗长 ${details.window_us.toFixed(2)} μs · 频率分辨能力约 ${Number(details.window_resolution_hz / 1000).toFixed(3)} kHz · FFT间隔 ${Number(details.frequency_bin_hz / 1000).toFixed(3)} kHz · Δt ${Number(details.time_step_us).toFixed(3)} μs`
      : result.method === "wpd"
        ? `${details.wavelet} · ${details.bands} 频带 · 带宽 ${Number(details.band_width_hz / 1000).toFixed(3)} kHz`
        : `${details.cwt_bins} 频率点 · Morlet ω0=${details.morlet_omega0}`;
    drawTimeFrequency();
  } catch (error) {
    $("timeFrequencyMeta").textContent = error.message;
    setEvent(error.message, "error");
  }
}

function tfColor(value, floor) {
  const ratio = Math.max(0, Math.min(1, (value - floor) / -floor));
  const stops = [
    [7, 16, 23], [22, 77, 115], [53, 208, 186], [255, 182, 77], [255, 241, 199],
  ];
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
  ctx.imageSmoothingEnabled = true;
  ctx.drawImage(image, left, top, right - left, bottom - top);
  ctx.strokeStyle = "#32454f"; ctx.strokeRect(left, top, right - left, bottom - top);
  drawAxes(
    ctx, width, height, left, top, right, bottom,
    (ratio) => `${(result.time_us[0] + (result.time_us.at(-1) - result.time_us[0]) * ratio).toFixed(1)} μs`,
    (ratio) => `${((result.frequency_hz[0] + (result.frequency_hz.at(-1) - result.frequency_hz[0]) * ratio) / 1000).toFixed(1)}`,
  );
  ctx.fillStyle = "#718892"; ctx.font = "10px Consolas";
  ctx.fillText("kHz", 8, top + 10);
}

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
  try {
    $("modeAnalysisMeta").textContent = "正在计算实验指标…";
    const result = await api("/api/analysis/experimental-modes", {
      method: "POST",
      body: JSON.stringify({
        path: analysisSource,
        reference_channel: referenceChannel,
        comparison_channel: comparisonChannel,
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
      }),
    });
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
  } catch (error) {
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
  [["common_v", "#35d0ba", []], ["differential_v", "#ffb64d", [5, 3]]].forEach(([key, color, dash]) => {
    ctx.strokeStyle = color; ctx.lineWidth = 1.2; ctx.setLineDash(dash); ctx.beginPath();
    result[key].forEach((value, index) => {
      const x = left + index / Math.max(result[key].length - 1, 1) * (right - left);
      const y = bottom - (value + maximum) / (2 * maximum) * (bottom - top);
      if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
  ctx.setLineDash([]); ctx.fillStyle = "#35d0ba"; ctx.fillText("共模", left + 6, top + 13);
  ctx.fillStyle = "#ffb64d"; ctx.fillText("差模", left + 48, top + 13);
  ctx.fillStyle = "#718892"; ctx.fillText("mV", 8, top + 10);

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
  ctx.strokeStyle = "#59d5ec"; ctx.lineWidth = 1.25; ctx.beginPath();
  symmetryValues.forEach((value, index) => {
    const x = sLeft + index / Math.max(symmetryValues.length - 1, 1) * (sRight - sLeft);
    const y = sBottom - (value - symmetryMin) / Math.max(symmetryMax - symmetryMin, 1e-12) * (sBottom - sTop);
    if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.fillStyle = "#718892";
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
  ctx.strokeStyle = "#b88cff"; ctx.lineWidth = 1.25; ctx.beginPath();
  correlation.forEach((value, index) => {
    const x = mLeft + index / Math.max(correlation.length - 1, 1) * (mRight - mLeft);
    const y = mBottom - (value + 1) / 2 * (mBottom - mTop);
    if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
  result.candidates.forEach((candidate) => {
    const x = mLeft + (candidate.time_us - matchTimes[0]) / Math.max(matchTimes.at(-1) - matchTimes[0], 1e-12) * (mRight - mLeft);
    ctx.strokeStyle = "rgba(255,182,77,.62)"; ctx.beginPath(); ctx.moveTo(x, mTop); ctx.lineTo(x, mBottom); ctx.stroke();
  });
  ctx.fillStyle = "#718892"; ctx.fillText("相关系数", 7, mTop + 10);
}

buildChannels();
restoreSettings();
updateMeasureFilterStatus();
showPage(currentPage());
$("spectrumWindows").innerHTML = "";
addSpectrumWindow(-100, 900, "全记录");
installScopeCanvasInteraction();
installAnalysisCanvasInteraction();
document.querySelectorAll("[data-nav], [data-route-link]").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    showPage(link.dataset.nav || link.dataset.routeLink, true);
  });
});
window.addEventListener("popstate", () => showPage(currentPage()));
$("analysisBrowseBtn").addEventListener("click", browseAnalysisPath);
$("analysisFileList").addEventListener("change", loadSelectedAnalysisFile);
$("analysisApplyBtn").addEventListener("click", refreshAnalysis);
$("analysisResetViewBtn").addEventListener("click", resetAnalysisView);
$("addSpectrumWindowBtn").addEventListener("click", () => {
  const start = number("analysisTimeStart"), end = number("analysisTimeEnd");
  const count = $("spectrumWindows").children.length;
  const width = (end - start) / Math.max(count + 1, 1);
  addSpectrumWindow(start + count * width, Math.min(end, start + (count + 1) * width));
  scheduleAnalysisRefresh();
});
$("analysisAmplitudeMode").addEventListener("change", () => {
  const manual = $("analysisAmplitudeMode").value === "manual";
  $("analysisAmpMin").disabled = !manual;
  $("analysisAmpMax").disabled = !manual;
  drawAnalysisWaveform();
});
$("analysisLayout").addEventListener("change", drawAnalysisWaveform);
$("analysisAmpMin").addEventListener("input", drawAnalysisWaveform);
$("analysisAmpMax").addEventListener("input", drawAnalysisWaveform);
$("analysisFilterEnabled").addEventListener("change", () => {
  if ($("analysisFilterEnabled").checked) $("analysisShowFiltered").checked = true;
  scheduleAnalysisRefresh();
});
[
  "analysisShowRaw", "analysisShowFiltered", "spectrumSource", "spectrumMode",
  "spectrumWindowFunction", "spectrumFreqMin", "spectrumFreqMax",
].forEach((id) => $(id).addEventListener("change", scheduleAnalysisRefresh));
$("spectrumScale").addEventListener("change", drawSpectrum);
$("exportFilteredNpzBtn").addEventListener("click", () => exportAnalysis("npz"));
$("exportFilteredCsvBtn").addEventListener("click", () => exportAnalysis("csv"));
$("archiveLoadBtn").addEventListener("click", () => loadSweepArchive($("archivePath").value.trim()));
$("archiveReevaluateBtn").addEventListener("click", reevaluateSweepArchive);
$("archiveMetric").addEventListener("change", drawArchiveHeatmap);
$("archiveRunsTableBody").addEventListener("click", (event) => {
  const preview = event.target.closest("button[data-archive-preview]");
  const analyze = event.target.closest("button[data-archive-analyze]");
  if (preview) loadArchiveRunPreview(Number(preview.dataset.archivePreview));
  if (analyze && archiveResult) {
    const run = archiveResult.runs[Number(analyze.dataset.archiveAnalyze)];
    loadArchiveRun(run);
  }
});
$("archiveHeatmapCanvas").addEventListener("click", (event) => {
  if (!archiveResult || !event.currentTarget._archiveGeometry) return;
  const geometry = event.currentTarget._archiveGeometry;
  const rect = event.currentTarget.getBoundingClientRect();
  const x = event.clientX - rect.left, y = event.clientY - rect.top;
  if (x < geometry.left || x > geometry.right || y < geometry.top || y > geometry.bottom) return;
  const xIndex = Math.min(archiveResult.frequencies_hz.length - 1, Math.floor((x - geometry.left) / geometry.cellWidth));
  const visualY = Math.min(archiveResult.cycles.length - 1, Math.floor((y - geometry.top) / geometry.cellHeight));
  const cycleIndex = archiveResult.cycles.length - 1 - visualY;
  const row = archiveResult.summary.find((item) =>
    Number(item.frequency_hz) === Number(archiveResult.frequencies_hz[xIndex])
    && Number(item.cycles) === Number(archiveResult.cycles[cycleIndex])
  );
  if (row) {
    const runIndex = archiveResult.runs.findIndex((item) =>
      Number(item.frequency_hz) === Number(row.frequency_hz)
      && Number(item.cycles) === Number(row.cycles));
    if (runIndex >= 0) loadArchiveRunPreview(runIndex);
  }
});
$("timeFrequencyMethod").addEventListener("change", updateTimeFrequencyControls);
$("calculateTimeFrequencyBtn").addEventListener("click", calculateTimeFrequency);
$("calculateModesBtn").addEventListener("click", calculateExperimentalModes);
$("captureBtn").addEventListener("click", startCapture);
$("sweepBtn").addEventListener("click", startSweep);
$("lcrStartBtn").addEventListener("click", startLcr);
$("lcrCalibrateBtn").addEventListener("click", startLcrCalibration);
$("stopBtn").addEventListener("click", stopCapture);
$("sweepStopBtn").addEventListener("click", stopCapture);
$("lcrStopBtn").addEventListener("click", stopCapture);
$("saveNpz").addEventListener("click", () => save("npz"));
$("saveCsv").addEventListener("click", () => save("csv"));
$("waveform").addEventListener("change", updateAwgControls);
$("sweepMode").addEventListener("change", updateSweepControls);
$("lcrMode").addEventListener("change", updateLcrControls);
$("lcrCalibrationFile").addEventListener("change", () => {
  const selected = $("lcrCalibrationFile").selectedOptions[0];
  $("lcrCalibrationStatus").textContent = $("lcrCalibrationFile").value
    ? `将使用：${selected.textContent}`
    : "当前测量不使用精准电阻校准";
});
["lcrFrequencyAxisScale", "lcrFrequencyUnit", "lcrResistanceUnit", "lcrCapacitanceUnit", "lcrInductanceUnit"].forEach((id) =>
  $(id).addEventListener("change", () => {
    if (latestLcr) updateLcrResult();
  }));
$("sweepMetric").addEventListener("change", drawSweepResult);
$("summaryViewBtn").addEventListener("click", () => showSweepView("summary"));
$("runsViewBtn").addEventListener("click", () => showSweepView("runs"));
$("sweepRunsTableBody").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-view-run]");
  if (button) loadSweepRun(Number(button.dataset.viewRun));
});
[
  "awgEnabled", "awgFrequency", "awgCycles", "awgVpp", "awgOffset", "awgBufferSamples",
  "cancelAmplitude", "cancelFrequency", "cancelCycles", "cancelPhase", "cancelDelayCycles",
  "rampUpCycles", "holdCycles", "rampDownCycles", "holdLevel",
].forEach((id) => $(id).addEventListener("input", scheduleAwgPreview));
$("measureFilterEnabled").addEventListener("change", scheduleMeasureFilterRefresh);
["measureBandLow", "measureBandHigh", "measureTransition"].forEach((id) =>
  $(id).addEventListener("input", scheduleMeasureFilterRefresh));
[
  "frequencyStart", "frequencyStop", "frequencyStep", "cyclesStart", "cyclesStop", "cyclesStep",
  "sweepRepeats", "sweepInterval", "awgFrequency", "awgCycles",
].forEach((id) => $(id).addEventListener("input", updateSweepControls));
[
  "lcrFrequency", "lcrFrequencyStart", "lcrFrequencyStop", "lcrFrequencyStep",
  "lcrPointsPerDecade", "lcrRepeats", "lcrBurstCycles", "lcrRampCycles",
  "lcrAnalysisCycles", "lcrGuardCycles",
].forEach((id) => $(id).addEventListener("input", updateLcrControls));
["sampleRate", "captureDurationUs", "triggerPositionPercent"].forEach((id) =>
  $(id).addEventListener("input", () => {
    updateCapturePlanSummary();
    updateSweepBaseSummary();
  }));
[
  "recommendationMaxDuration", "recommendationMinStrength",
  "recommendationStrongMinStrength", "recommendationMinReflectionSnr",
].forEach((id) => $(id).addEventListener("input", () => {
  if (latestSweep) {
    updateSweepResult(latestSweep);
    drawSweepResult();
  }
}));
[
  "archiveRecommendationDuration", "archiveRecommendationStrength",
  "archiveRecommendationStrong", "archiveRecommendationSnr",
].forEach((id) => $(id).addEventListener("input", updateArchiveRecommendations));
window.addEventListener("resize", () => {
  drawScope();
  drawSweepResult();
  drawSweepRun();
  drawAnalysisWaveform();
  drawSpectrum();
  drawArchiveHeatmap();
  drawArchiveRun();
  drawTimeFrequency();
  drawExperimentalModes();
  drawLcrParameterCharts();
  drawLcrBode();
  drawLcrNyquist();
  drawLcrWave();
  scheduleAwgPreview();
});
document.addEventListener("change", (event) => {
  if (event.target.matches("input,select")) {
    if (
      event.target.id.startsWith("ch")
      || event.target.id.startsWith("sweepRx")
      || event.target.id === "sweepTransmitterChannel"
    ) {
      syncSweepReceiverChannels();
    }
    if (
      event.target.id.startsWith("archiveRx")
      || event.target.id === "archiveTransmitterChannel"
    ) {
      syncArchiveReceiverChannels();
    }
    persistSettings();
    updateSweepBaseSummary();
  }
});
$("inputRange").addEventListener("change", () => {
  channelNames.forEach((name) => {
    $(`chRange${name}`).value = $("inputRange").value;
  });
  persistSettings();
});
updateAwgControls();
updateCapturePlanSummary();
updateSweepControls();
updateLcrControls();
updateSweepBaseSummary();
syncSweepReceiverChannels();
updateTimeFrequencyControls();
setEvent("系统就绪，可以开始配置。", "idle");
