const channelNames = [..."ABCDEFGH"];
const colors = ["#2f73ff", "#59a9ff", "#7eb6e8", "#9bc9ff", "#3d5fca", "#75a2d4", "#a7cce8", "#c8d8eb"];
const inputRanges = ["10mV", "20mV", "50mV", "100mV", "200mV", "500mV", "1V", "2V", "5V", "10V", "20V", "50V"];
const $ = (id) => document.getElementById(id);
const setDisabled = (id, value) => { const element = $(id); if (element) element.disabled = value; };
const setWidth = (id, value) => { const element = $(id); if (element) element.style.width = value; };
let latestResult = null;
let latestSweep = null;
let latestSweepRun = null;
let latestLcr = null;
let latestLcrWave = null;
let latestBigLcr = null;
let latestBigLcrWave = null;
let latestLinearity = null;
let latestLinearityWave = null;
let latestLinearityMeasurement = null;
let latestLinearityMeasurementWave = null;
const lcrFolderStates = { big_lcr: null, linearity: null, small_lcr: null };
let polling = null;
let previewTimer = null;
let previewRequest = 0;
let measureFilterTimer = null;
let measureFilterRequest = 0;
let scopeTimeStart = null;
let scopeTimeEnd = null;
let scopeAmplitudeScale = 1;
let activeTripAlarmId = null;
let tripAlarmMuted = false;
let tripAlarmAudioContext = null;
let tripAlarmSoundTimer = null;
let tripAlarmSoundActive = false;
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
  ["lcrVoltageChannel", "lcrCurrentChannel", "bigLcrVoltageChannel", "bigLcrCurrentChannel", "linearityReceiverChannel"].forEach((id) => {
    $(id).innerHTML = channelNames.map((name) => `<option value="${name}">CH ${name}</option>`).join("");
  });
  $("lcrVoltageChannel").value = "A";
  $("lcrCurrentChannel").value = "B";
  $("bigLcrVoltageChannel").value = "A";
  $("bigLcrCurrentChannel").value = "B";
  $("linearityReceiverChannel").value = "C";
  ["lcrVoltageRange", "lcrCurrentRange", "bigLcrVoltageRange", "bigLcrCurrentRange", "linearityReceiverRange"].forEach((id) => {
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

function buildBigLcrPayload() {
  const voltageChannel = $("bigLcrVoltageChannel").value;
  const currentChannel = $("bigLcrCurrentChannel").value;
  const triggerSignal = $("bigLcrTriggerSignal").value;
  const triggerChannel = triggerSignal === "current" ? currentChannel : voltageChannel;
  const channels = {};
  channelNames.forEach((name) => {
    let range = "2V";
    if (name === voltageChannel) range = $("bigLcrVoltageRange").value;
    if (name === currentChannel) range = $("bigLcrCurrentRange").value;
    channels[name] = {
      enabled: [voltageChannel, currentChannel].includes(name),
      range,
      coupling: "DC",
      analog_offset_v: 0,
    };
  });
  const frequencyHz = number("bigLcrFrequency") * 1000;
  const burstCycles = Math.round(number("bigLcrBurstCycles"));
  return {
    safety_acknowledged: $("bigLcrSafetyAck").checked,
    config: {
      simulate: $("bigLcrSimulate").checked,
      sample_rate_hz: number("bigLcrSampleRate") * 1e6,
      pre_trigger_samples: 100,
      post_trigger_samples: 1000,
      channels,
      trigger: {
        enabled: true,
        source: triggerChannel,
        threshold_v: number("bigLcrTriggerLevel"),
        direction: "rising",
        auto_trigger_ms: 2000,
        delay_samples: 0,
      },
      awg: {
        enabled: true,
        waveform: "lcr_tone",
        frequency_hz: frequencyHz,
        cycles: burstCycles,
        pk_to_pk_v: number("bigLcrAwgVpp"),
        offset_v: 0,
        buffer_samples: 8192,
        trigger_source: "software",
        tone_ramp_cycles: number("bigLcrRampCycles"),
      },
      capture_timeout_s: 10,
    },
    big_lcr: {
      mode: ["frequency", "grid"].includes($("bigLcrScanMode").value)
        ? $("bigLcrMode").value : "single",
      scan_mode: $("bigLcrScanMode").value,
      frequency_hz: frequencyHz,
      frequency_start_hz: number("bigLcrFrequencyStart") * 1000,
      frequency_stop_hz: number("bigLcrFrequencyStop") * 1000,
      frequency_step_hz: number("bigLcrFrequencyStep") * 1000,
      points_per_decade: Math.round(number("bigLcrPointsPerDecade")),
      repeats: Math.round(number("bigLcrRepeats")),
      interval_s: number("bigLcrInterval"),
      awg_drive_vpp: number("bigLcrAwgVpp"),
      awg_vpp_start: number("bigLcrVppStart"),
      awg_vpp_stop: number("bigLcrVppStop"),
      awg_vpp_step: number("bigLcrVppStep"),
      burst_cycles: burstCycles,
      ramp_cycles: number("bigLcrRampCycles"),
      analysis_cycles: Math.round(number("bigLcrAnalysisCycles")),
      analysis_guard_cycles: number("bigLcrGuardCycles"),
      voltage_channel: voltageChannel,
      current_channel: currentChannel,
      trigger_signal: triggerSignal,
      voltage_monitor_scale_v_per_v: number("bigLcrVoltageScale"),
      current_monitor_scale_a_per_v: number("bigLcrCurrentScale"),
      current_monitor_polarity: Number($("bigLcrCurrentPolarity").value),
      voltage_monitor_offset_v: number("bigLcrVoltageOffset"),
      current_monitor_offset_v: number("bigLcrCurrentOffset"),
      ata_voltage_gain: number("bigLcrAtaGain"),
      auto_monitor_range: $("bigLcrAutoRange").checked,
      auto_range_max_retries: 2,
      trip_detection_enabled: $("bigLcrTripDetection").checked,
      trip_drop_ratio: number("bigLcrTripDrop") / 100,
      trip_hold_cycles: number("bigLcrTripHold"),
      min_monitor_rms_v: number("bigLcrMinMonitorRms") / 1000,
      max_drive_vpp: number("bigLcrMaxDrive"),
      alert_voltage_vpp_v: number("bigLcrAlertVoltageVpp"),
      alert_current_peak_a: number("bigLcrAlertCurrentPeak"),
      max_frequency_hz: number("bigLcrMaxFrequency") * 1000,
    },
  };
}

function buildLinearityPayload() {
  const voltageChannel = $("bigLcrVoltageChannel").value;
  const currentChannel = $("bigLcrCurrentChannel").value;
  const receiverChannel = $("linearityReceiverChannel").value;
  if (new Set([voltageChannel, currentChannel, receiverChannel]).size !== 3) {
    throw new Error("Voltage Monitor、Current Monitor 和 Receiver 必须选择三个不同通道");
  }
  const sampleRate = number("linearitySampleRate") * 1e6;
  const duration = number("linearityCaptureDuration") * 1e-6;
  const preSamples = Math.round(sampleRate * duration * number("linearityTriggerPosition") / 100);
  const totalSamples = Math.max(2, Math.round(sampleRate * duration));
  const triggerChannel = $("linearityTriggerSignal").value === "current" ? currentChannel : voltageChannel;
  const frequencyHz = number("linearityFrequencyKHz") * 1000;
  const cycles = Math.round(number("linearityCycles"));
  const rampCycles = number("linearityRampCycles");
  const channels = {};
  channelNames.forEach((name) => {
    const range = name === voltageChannel ? $("bigLcrVoltageRange").value
      : name === currentChannel ? $("bigLcrCurrentRange").value
        : name === receiverChannel ? $("linearityReceiverRange").value : "2V";
    channels[name] = { enabled: [voltageChannel, currentChannel, receiverChannel].includes(name), range, coupling: "DC", analog_offset_v: 0 };
  });
  return {
    safety_acknowledged: $("bigLcrSafetyAck").checked,
    config: {
      simulate: $("bigLcrSimulate").checked,
      sample_rate_hz: sampleRate,
      pre_trigger_samples: preSamples,
      post_trigger_samples: Math.max(1, totalSamples - preSamples),
      channels,
      trigger: { enabled: true, source: triggerChannel, threshold_v: number("linearityTriggerLevel"), direction: "rising", auto_trigger_ms: 2000, delay_samples: 0 },
      awg: { enabled: true, waveform: "lcr_tone", frequency_hz: frequencyHz, cycles, pk_to_pk_v: number("linearityVppStart"), offset_v: 0, buffer_samples: 8192, trigger_source: "software", tone_ramp_cycles: rampCycles },
      capture_timeout_s: 10,
    },
    linearity: {
      frequency_hz: frequencyHz, cycles, ramp_cycles: rampCycles,
      vpp_start: number("linearityVppStart"), vpp_stop: number("linearityVppStop"), vpp_step: number("linearityVppStep"),
      direction: $("linearityDirection").value, repeats: Math.round(number("linearityRepeats")), interval_s: number("linearityInterval"),
      sample_rate_hz: sampleRate, capture_duration_us: number("linearityCaptureDuration"),
      trigger_position_percent: number("linearityTriggerPosition"), trigger_signal: $("linearityTriggerSignal").value,
      trigger_level_v: number("linearityTriggerLevel"), voltage_channel: voltageChannel, voltage_range: $("bigLcrVoltageRange").value,
      current_channel: currentChannel, current_range: $("bigLcrCurrentRange").value,
      receiver_channel: receiverChannel, receiver_range: $("linearityReceiverRange").value,
      trip_detection_enabled: $("bigLcrTripDetection").checked,
      trip_drop_ratio: number("bigLcrTripDrop") / 100,
      trip_hold_cycles: number("bigLcrTripHold"),
      minimum_monitor_rms_v: number("bigLcrMinMonitorRms") / 1000,
      threshold_d_wave_pct: number("linearityMeasureLimitDwave"),
      threshold_receiver_thd_pct: number("linearityMeasureLimitRxThd"),
      threshold_current_thd_pct: number("linearityMeasureLimitIThd"),
      threshold_compression_db: number("linearityMeasureLimitCompression"),
      threshold_kme_deviation_pct: number("linearityMeasureLimitKme"),
      threshold_correlation: number("linearityMeasureLimitCorrelation"),
      voltage_scale_v_per_v: number("bigLcrVoltageScale"), voltage_offset_v: number("bigLcrVoltageOffset"),
      current_scale_a_per_v: number("bigLcrCurrentScale"), current_offset_v: number("bigLcrCurrentOffset"),
      current_polarity: Number($("bigLcrCurrentPolarity").value), ata_voltage_gain: number("bigLcrAtaGain") || 1,
      noise_start_us: number("linearityNoiseStart"), noise_end_us: number("linearityNoiseEnd"),
      direct_start_us: number("linearityDirectStart"), direct_end_us: number("linearityDirectEnd"),
      echo_start_us: number("linearityEchoStart"), echo_end_us: number("linearityEchoEnd"),
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
  if (!$("lcrMode")) return;
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

function updateBigLcrControls() {
  if (!$("bigLcrMode")) return;
  const mode = $("bigLcrMode").value;
  const scanMode = $("bigLcrScanMode")?.value || "single";
  const frequencyAxisEnabled = ["frequency", "grid"].includes(scanMode);
  const voltageAxisEnabled = ["voltage", "grid"].includes(scanMode);
  const frequencySweepEnabled = frequencyAxisEnabled && mode !== "single";
  $("bigLcrSingleControls").hidden = frequencySweepEnabled;
  $("bigLcrSweepControls").hidden = !frequencySweepEnabled;
  $("bigLcrLinearStepLabel").hidden = mode !== "linear";
  $("bigLcrLogPointsLabel").hidden = mode !== "log";
  $("bigLcrFixedVppControl").hidden = voltageAxisEnabled;
  $("bigLcrVppSweepControls").hidden = !voltageAxisEnabled;
  let frequencyPoints = 1;
  if (frequencySweepEnabled && mode === "linear") {
    frequencyPoints = axisCount(number("bigLcrFrequencyStart"), number("bigLcrFrequencyStop"), number("bigLcrFrequencyStep"));
  } else if (frequencySweepEnabled && mode === "log") {
    const ratio = number("bigLcrFrequencyStop") / Math.max(number("bigLcrFrequencyStart"), 1e-30);
    frequencyPoints = ratio >= 1 ? Math.max(2, Math.ceil(Math.log10(ratio) * number("bigLcrPointsPerDecade")) + 1) : 0;
  }
  const voltagePoints = voltageAxisEnabled
    ? axisCount(number("bigLcrVppStart"), number("bigLcrVppStop"), number("bigLcrVppStep")) : 1;
  const points = frequencyPoints * voltagePoints;
  const runs = points * Math.max(0, Math.round(number("bigLcrRepeats")));
  const estimate = scanMode === "grid"
    ? `${frequencyPoints} 频点 × ${voltagePoints} 个 Vpp · ${points} 个组合点 · ${runs} 次采集`
    : scanMode === "frequency" ? `${frequencyPoints} 频点 · ${runs} 次采集`
      : scanMode === "voltage" ? `${voltagePoints} 个 Vpp · ${runs} 次采集`
        : `${points} 个测量点 · ${runs} 次采集`;
  $("bigLcrEstimate").textContent = points
    ? `${estimate}${$("bigLcrAutoRange")?.checked ? ` · 最多 ${runs * 3} 次激励（含重采）` : ""}`
    : "参数无效";
  updateBigSafetyStatus();
}

function updateBigSafetyStatus(lastRow = null) {
  const status = $("bigLcrSafetyStatus");
  const button = $("bigLcrStartBtn");
  if (!status || !button) return;
  const acknowledged = $("bigLcrSafetyAck").checked;
  const scanMode = $("bigLcrScanMode")?.value || "single";
  const voltageAxisEnabled = ["voltage", "grid"].includes(scanMode);
  const drive = voltageAxisEnabled ? Number($("bigLcrVppStop").value) : Number($("bigLcrAwgVpp").value);
  const driveStart = voltageAxisEnabled ? Number($("bigLcrVppStart").value) : Number($("bigLcrAwgVpp").value);
  const driveStep = voltageAxisEnabled ? Number($("bigLcrVppStep").value) : 1;
  const maxDrive = Number($("bigLcrMaxDrive").value);
  const frequencySweepEnabled = ["frequency", "grid"].includes(scanMode) && $("bigLcrMode")?.value !== "single";
  const frequencyStart = frequencySweepEnabled ? Number($("bigLcrFrequencyStart").value) : Number($("bigLcrFrequency").value);
  const frequency = frequencySweepEnabled ? Number($("bigLcrFrequencyStop").value) : Number($("bigLcrFrequency").value);
  const maxFrequency = Number($("bigLcrMaxFrequency").value);
  const gain = Number($("bigLcrAtaGain").value);
  const alertVoltage = Number($("bigLcrAlertVoltageVpp").value);
  const estimatedOutputVpp = drive * gain;
  const validGain = gain > 0 && gain <= 60;
  const alertCurrent = Number($("bigLcrAlertCurrentPeak").value);
  const validAlertThresholds = alertVoltage > 0 && alertCurrent > 0;
  const validDriveAxis = driveStart > 0 && drive <= maxDrive && driveStart <= drive && driveStep > 0;
  const validFrequencyAxis = frequencyStart > 0 && frequency <= maxFrequency && frequencyStart <= frequency;
  let frequencyPoints = 1;
  if (frequencySweepEnabled && $("bigLcrMode").value === "linear") {
    frequencyPoints = axisCount(Number($("bigLcrFrequencyStart").value), frequency, Number($("bigLcrFrequencyStep").value));
  } else if (frequencySweepEnabled && $("bigLcrMode").value === "log") {
    const ratio = frequency / Math.max(frequencyStart, 1e-30);
    frequencyPoints = ratio >= 1
      ? Math.max(2, Math.ceil(Math.log10(ratio) * number("bigLcrPointsPerDecade")) + 1) : 0;
  }
  const voltagePoints = voltageAxisEnabled
    ? axisCount(driveStart, drive, driveStep) : 1;
  const repeats = Math.round(number("bigLcrRepeats"));
  const totalRuns = frequencyPoints * voltagePoints * repeats;
  const maxShots = totalRuns * ($("bigLcrAutoRange")?.checked ? 3 : 1);
  const validRunCount = frequencyPoints > 0 && voltagePoints > 0
    && repeats >= 1 && repeats <= 100 && maxShots <= 10_000;
  status.className = "big-safety-status";
  if (!acknowledged) {
    status.classList.add("warn");
    status.textContent = "未确认安全参数；开始按钮将保持锁定";
  } else if (!validGain) {
    status.classList.add("trip");
    status.textContent = "请填写 ATA 面板增益（>0 且 ≤60 倍）。";
  } else if (!validAlertThresholds) {
    status.classList.add("trip");
    status.textContent = "电压 Vpp 与电流 Apeak 提醒值均须大于 0。";
  } else if (!validDriveAxis || !validFrequencyAxis) {
    status.classList.add("trip");
    status.textContent = "输入激励或频率超过安全限值，请先调整参数";
  } else if (!validRunCount) {
    status.classList.add("warn");
    status.textContent = "扫描点数无效或考虑自动量程重采后最多超过 10000 次激励";
  } else if (lastRow?.safety_state) {
    const state = String(lastRow.safety_state).toLowerCase();
    status.classList.add(["trip", "suspect_trip", "monitor_fault", "adc_overflow", "analysis_error"].includes(state) ? "trip" : state === "warn" ? "warn" : "ok");
    status.textContent = `最近一次：${lastRow.safety_state} · ${lastRow.safety_message || ""}`;
  } else if (estimatedOutputVpp > alertVoltage) {
    status.classList.add("warn");
    status.textContent = `估算输出 ${estimatedOutputVpp.toPrecision(4)} Vpp，超过程序提醒值；仍可开始。`;
  } else if (estimatedOutputVpp > 200) {
    status.classList.add("warn");
    status.textContent = `估算输出 ${estimatedOutputVpp.toPrecision(4)} Vpp，超过 ATA-2021B 官网标称 200 Vpp；仍可开始。`;
  } else {
    status.classList.add("ok");
    status.textContent = `估算输出 ${estimatedOutputVpp.toPrecision(4)} Vpp；采集后按 Monitor 实测值提示。`;
  }
  button.disabled = !acknowledged || !validGain || !validAlertThresholds || !validDriveAxis || !validFrequencyAxis || !validRunCount;
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

function primeTripAlarmAudio() {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) return false;
  try {
    if (!tripAlarmAudioContext) tripAlarmAudioContext = new AudioContextClass();
    if (tripAlarmAudioContext.state === "suspended") tripAlarmAudioContext.resume().catch(() => {});
    return true;
  } catch (_) { return false; }
}

function playTripAlarmPattern() {
  const context = tripAlarmAudioContext;
  if (!context || context.state !== "running" || !tripAlarmSoundActive || tripAlarmMuted) return;
  const start = context.currentTime + 0.015;
  [0, 0.23].forEach((offset, index) => {
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    const at = start + offset;
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(index ? 660 : 880, at);
    gain.gain.setValueAtTime(0.0001, at);
    gain.gain.exponentialRampToValueAtTime(0.12, at + 0.018);
    gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.16);
    oscillator.connect(gain); gain.connect(context.destination);
    oscillator.start(at); oscillator.stop(at + 0.17);
  });
}

function startTripAlarmSound() {
  if (tripAlarmMuted || !primeTripAlarmAudio()) return;
  tripAlarmSoundActive = true;
  const ready = tripAlarmAudioContext.state === "running"
    ? Promise.resolve()
    : tripAlarmAudioContext.resume().catch(() => {});
  ready.then(() => {
    if (!tripAlarmSoundActive || tripAlarmMuted) return;
    playTripAlarmPattern();
    clearInterval(tripAlarmSoundTimer);
    tripAlarmSoundTimer = setInterval(playTripAlarmPattern, 1150);
  });
}

function stopTripAlarmSound() {
  tripAlarmSoundActive = false;
  clearInterval(tripAlarmSoundTimer);
  tripAlarmSoundTimer = null;
}

function updateTripAlarm(status) {
  const modal = $("tripAlarmModal");
  if (!modal) return;
  const alarm = status.trip_alarm;
  if (status.state !== "paused" || !alarm) {
    modal.hidden = true;
    stopTripAlarmSound();
    activeTripAlarmId = null;
    return;
  }
  modal.hidden = false;
  const newAlarm = alarm.alarm_id !== activeTripAlarmId;
  if (newAlarm) {
    activeTripAlarmId = alarm.alarm_id;
    tripAlarmMuted = false;
    startTripAlarmSound();
    $("tripContinueBtn")?.focus({ preventScroll: true });
  }
  const taskName = alarm.task_kind === "big_lcr" ? "大信号 LCR" : "大信号线性度 / 波形畸变";
  const run = Number(alarm.run_index), total = Number(alarm.total_runs);
  const point = Number(alarm.point_index), points = Number(alarm.total_points);
  const finalRun = Number.isFinite(run) && Number.isFinite(total) && total > 0 && run >= total;
  const progress = Number.isFinite(run) && Number.isFinite(total)
    ? `${run}/${total} 次 · 点 ${point}/${points} · 重复 ${alarm.repeat}/${alarm.repeats}` : "—";
  const frequency = Number(alarm.frequency_hz);
  const vpp = Number(alarm.awg_vpp);
  const ratio = Number(alarm.monitor_min_ratio);
  $("tripAlarmReason").textContent = alarm.reason || "检测到 Monitor 平顶区持续掉幅，请检查 ATA-2021B。";
  $("tripAlarmTask").textContent = taskName;
  $("tripAlarmProgress").textContent = progress;
  $("tripAlarmFrequency").textContent = Number.isFinite(frequency) ? `${(frequency / 1000).toFixed(3)} kHz` : "—";
  $("tripAlarmVpp").textContent = Number.isFinite(vpp) ? `${vpp.toFixed(3)} Vpp` : "—";
  $("tripAlarmChannels").textContent = alarm.voltage_channel && alarm.current_channel
    ? `Voltage ${alarm.voltage_channel} · Current ${alarm.current_channel}` : "—";
  $("tripAlarmDrop").textContent = Number.isFinite(ratio) ? `${(ratio * 100).toFixed(1)}% 剩余` : "已持续低于设定阈值";
  $("tripAlarmInstruction").textContent = finalRun
    ? "请先检查 ATA-2021B 保护状态并完成合闸/复位。当前已是本次任务的最后一次采集；确认设备已恢复且无需继续更多点后，点击按钮结束任务。"
    : "请先检查 ATA-2021B 保护状态并完成合闸/复位。点击“已解决，继续扫描”表示你已确认设备恢复且可以安全继续；系统会从下一次采集继续。";
  $("tripContinueBtn").textContent = finalRun ? "已解决，结束任务" : "已解决，继续扫描";
  const muteButton = $("tripMuteBtn");
  if (muteButton) {
    muteButton.textContent = tripAlarmMuted ? "恢复声音" : "停止声音";
    muteButton.setAttribute("aria-pressed", String(tripAlarmMuted));
  }
}

async function continueAfterTrip() {
  const button = $("tripContinueBtn");
  if (button) button.disabled = true;
  try {
    await api("/api/trip/resolve", { method: "POST", body: "{}" });
    stopTripAlarmSound();
    setEvent("已确认疑似跳闸问题解决，扫描从下一测量点继续。", "running");
  } catch (error) {
    setEvent(error.message, "error");
  } finally {
    if (button) button.disabled = false;
  }
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

  ctx.strokeStyle = "#2f73ff";
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
  $("stateText").textContent = state === "running" ? "任务运行中" : state === "paused" ? "扫描已暂停" : state === "error" ? "运行异常" : state === "complete" ? "任务完成" : state === "stopped" ? "任务已停止" : "系统就绪";
  $("statusDot").className = `status-dot ${state}`;
  if (document.body.dataset.page !== "file-analysis") {
    const label = state === "running" ? "任务运行中 · RUNNING"
      : state === "paused" ? "等待处置 · PAUSED"
        : state === "error" ? "运行异常 · ERROR"
          : state === "complete" ? "任务完成 · COMPLETE"
            : state === "stopped" ? "任务停止 · STOPPED"
              : "系统就绪 · READY";
    $("analysisViewState").textContent = label;
  }
  $("eventTime").textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

async function startCapture() {
  try {
    ["captureBtn", "sweepBtn", "lcrStartBtn", "lcrCalibrateBtn", "bigLcrStartBtn", "linearityStartBtn", "stopBtn", "sweepStopBtn", "lcrStopBtn", "bigLcrStopBtn", "linearityStopBtn"].forEach((id) => setDisabled(id, true));
    latestResult = null;
    $("emptyState").style.display = "flex";
    const reply = await api("/api/capture", { method: "POST", body: JSON.stringify(buildPayload()) });
    setDisabled("stopBtn", false);
    setEvent(`采集任务 #${reply.capture_id} 已启动，正在等待触发。`, "running");
    polling = setInterval(pollStatus, 350);
  } catch (error) {
    ["captureBtn", "sweepBtn", "lcrStartBtn", "lcrCalibrateBtn", "bigLcrStartBtn", "linearityStartBtn"].forEach((id) => setDisabled(id, false));
    setDisabled("stopBtn", true);
    setEvent(error.message, "error");
  }
}

async function startSweep() {
  try {
    ["captureBtn", "sweepBtn", "lcrStartBtn", "lcrCalibrateBtn", "bigLcrStartBtn", "linearityStartBtn", "stopBtn", "sweepStopBtn", "lcrStopBtn", "bigLcrStopBtn", "linearityStopBtn"].forEach((id) => setDisabled(id, true));
    latestSweep = null;
    $("sweepResultPanel").hidden = true;
    $("sweepIdleStage").hidden = false;
    $("sweepIdleStage").dataset.state = "running";
    $("sweepIdleEyebrow").textContent = "PARAMETER SWEEP · RUNNING";
    $("sweepIdleTitle").textContent = "扫描正在建立数据";
    $("sweepIdleDescription").textContent = "AWG、硬件触发与块采集按参数网格顺序执行；首个有效参数点完成后开始呈现结果。";
    $("sweepProgressBar").style.width = "0%";
    const reply = await api("/api/sweep/start", { method: "POST", body: JSON.stringify(buildSweepPayload()) });
    setDisabled("sweepStopBtn", false);
    setEvent(`扫描任务 #${reply.task_id} 已启动。`, "running");
    $("sweepProgressText").textContent = "正在打开设备并准备第一个参数点";
    clearInterval(polling);
    polling = setInterval(pollStatus, 350);
  } catch (error) {
    ["captureBtn", "sweepBtn", "lcrStartBtn", "lcrCalibrateBtn", "bigLcrStartBtn", "linearityStartBtn"].forEach((id) => setDisabled(id, false));
    setDisabled("sweepStopBtn", true);
    $("sweepIdleStage").dataset.state = "idle";
    $("sweepIdleEyebrow").textContent = "PARAMETER SWEEP · IDLE";
    $("sweepIdleTitle").textContent = "扫描数据舞台";
    $("sweepIdleDescription").textContent = "配置左侧参数网格并启动扫描。首个有效参数点完成后，这里将呈现评价结果与逐次采集波形。";
    setEvent(error.message, "error");
  }
}

async function startLcr() {
  try {
    ["captureBtn", "sweepBtn", "lcrStartBtn", "lcrCalibrateBtn", "bigLcrStartBtn", "linearityStartBtn", "lcrStopBtn", "bigLcrStopBtn", "linearityStopBtn", "stopBtn", "sweepStopBtn"].forEach((id) => setDisabled(id, true));
    latestLcr = null;
    latestLcrWave = null;
    $("lcrProgressBar").style.width = "0%";
    const reply = await api("/api/lcr/start", {
      method: "POST",
      body: JSON.stringify(buildLcrPayload()),
    });
    setDisabled("lcrStopBtn", false);
    setEvent(`LCR 任务 #${reply.task_id} 已启动。`, "running");
    $("lcrProgressText").textContent = "正在准备第一个频点";
    clearInterval(polling);
    polling = setInterval(pollStatus, 350);
  } catch (error) {
    ["captureBtn", "sweepBtn", "lcrStartBtn", "lcrCalibrateBtn", "bigLcrStartBtn", "linearityStartBtn"].forEach((id) => setDisabled(id, false));
    setDisabled("lcrStopBtn", true);
    setEvent(error.message, "error");
  }
}

async function startLcrCalibration() {
  try {
    const payload = buildLcrPayload();
    payload.standard_resistance_ohm = number("lcrCalibrationResistance");
    payload.lcr.calibration_enabled = false;
    payload.lcr.calibration_file = null;
    ["captureBtn", "sweepBtn", "lcrStartBtn", "lcrCalibrateBtn", "bigLcrStartBtn", "linearityStartBtn", "lcrStopBtn", "bigLcrStopBtn", "linearityStopBtn", "stopBtn", "sweepStopBtn"].forEach((id) => setDisabled(id, true));
    latestLcr = null;
    latestLcrWave = null;
    $("lcrProgressBar").style.width = "0%";
    const reply = await api("/api/lcr/calibrate", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    setDisabled("lcrStopBtn", false);
    setEvent(`精准电阻校准任务 #${reply.task_id} 已启动。`, "running");
    $("lcrProgressText").textContent = "正在测量精准电阻的第一个频点";
    clearInterval(polling);
    polling = setInterval(pollStatus, 350);
  } catch (error) {
    ["captureBtn", "sweepBtn", "lcrStartBtn", "lcrCalibrateBtn", "bigLcrStartBtn", "linearityStartBtn"].forEach((id) => setDisabled(id, false));
    setDisabled("lcrStopBtn", true);
    setEvent(error.message, "error");
  }
}

async function startBigLcr() {
  try {
    primeTripAlarmAudio();
    if (!( $("bigLcrSafetyAck")?.checked )) {
      updateBigSafetyStatus();
      throw new Error("请先确认 ATA-2021B 安全参数");
    }
    ["captureBtn", "sweepBtn", "lcrStartBtn", "lcrCalibrateBtn", "bigLcrStartBtn", "linearityStartBtn", "lcrStopBtn", "bigLcrStopBtn", "linearityStopBtn", "stopBtn", "sweepStopBtn"].forEach((id) => setDisabled(id, true));
    latestBigLcr = null; latestBigLcrWave = null;
    setWidth("bigLcrProgressBar", "0%");
    const reply = await api("/api/big-lcr/start", {
      method: "POST",
      body: JSON.stringify(buildBigLcrPayload()),
    });
    setDisabled("bigLcrStopBtn", false);
    setEvent(`ATA-2021B 大信号 LCR 任务 #${reply.task_id} 已启动。`, "running");
    $("bigLcrProgressText").textContent = "正在准备第一个频点";
    clearInterval(polling);
    polling = setInterval(pollStatus, 350);
  } catch (error) {
    ["captureBtn", "sweepBtn", "lcrStartBtn", "lcrCalibrateBtn", "bigLcrStartBtn", "linearityStartBtn"].forEach((id) => setDisabled(id, false));
    setDisabled("bigLcrStopBtn", true);
    updateBigSafetyStatus();
    setEvent(error.message, "error");
  }
}

async function startLinearityTest() {
  try {
    primeTripAlarmAudio();
    if (!$("bigLcrSafetyAck").checked) throw new Error("请先确认 ATA-2021B 安全参数");
    const payload = buildLinearityPayload();
    ["captureBtn", "sweepBtn", "lcrStartBtn", "lcrCalibrateBtn", "bigLcrStartBtn", "linearityStartBtn", "stopBtn", "sweepStopBtn", "lcrStopBtn", "bigLcrStopBtn", "linearityStopBtn"].forEach((id) => setDisabled(id, true));
    latestLinearityMeasurement = null;
    setWidth("linearityProgressBar", "0%");
    const reply = await api("/api/lcr-linearity/start", { method: "POST", body: JSON.stringify(payload) });
    setDisabled("linearityStopBtn", false);
    $("linearityProgressText").textContent = `线性度任务 #${reply.task_id} 已启动`;
    setEvent("大信号线性度与波形畸变测试已启动。", "running");
    clearInterval(polling); polling = setInterval(pollStatus, 350);
  } catch (error) {
    ["captureBtn", "sweepBtn", "lcrStartBtn", "lcrCalibrateBtn", "bigLcrStartBtn", "linearityStartBtn"].forEach((id) => setDisabled(id, false));
    setDisabled("linearityStopBtn", true);
    setEvent(error.message, "error");
  }
}

function renderLinearityMeasurement() {
  const data = latestLinearityMeasurement;
  if (!data || !$("linMeasurementTableBody")) return;
  const rows = data.run_rows || [];
  const last = rows.at(-1) || {};
  $("linMeasurementPath").textContent = data.directory || "—";
  $("linMeasurementDriveMetric").textContent = `${Number(last.awg_vpp || 0).toFixed(3)} Vpp / ${lcrNumeric(last.current_peak_a)?.toPrecision(4) || "—"} A`;
  $("linMeasurementReceiverMetric").textContent = `${lcrNumeric(last.receiver_peak_v)?.toPrecision(4) || "—"} V / ${lcrNumeric(last.K_ME_v_per_a)?.toPrecision(4) || "—"} V/A`;
  $("linMeasurementThdMetric").textContent = `${lcrNumeric(last.current_thd_pct)?.toPrecision(3) || "—"}% / ${lcrNumeric(last.receiver_thd_pct)?.toPrecision(3) || "—"}%`;
  $("linMeasurementShapeMetric").textContent = `${lcrNumeric(last.D_wave_pct)?.toPrecision(3) || "—"}% / ${lcrNumeric(last.correlation)?.toPrecision(4) || "—"}`;
  $("linMeasurementCompressionMetric").textContent = `${lcrNumeric(last.compression_db)?.toPrecision(4) || "—"} dB`;
  $("linMeasurementEchoMetric").textContent = last.echo_available
    ? `${cell(last.echo_peak_v, 4)} V · D_echo ${cell(last.D_echo_pct, 4)}%`
    : "回波窗未配置或采样不足";
  const state = last.status === "OK" ? (last.engineering_state || "有效") : `${last.status || "—"}${last.clipped ? " · clipped" : ""}${last.overflow ? " · overflow" : ""}`;
  $("linMeasurementStateMetric").textContent = state;
  $("linMeasurementStateMetric").style.color = last.valid ? "#2f73ff" : "#c98435";
  $("linMeasurementSafetyMetric").textContent = last.safety_state === "WARN" ? (last.safety_message || "WARN") : "OK · 未超过程序提醒阈值";
  $("linMeasurementSafetyMetric").style.color = last.safety_state === "WARN" ? "#c98435" : "#2f73ff";
  const pointSummary = new Map((data.summary_rows || []).map((item) => [`${item.direction}:${Number(item.awg_vpp)}`, item]));
  $("linMeasurementTableBody").innerHTML = rows.map((row) => {
    const summary = pointSummary.get(`${row.direction}:${Number(row.awg_vpp)}`);
    return `<tr><td>${row.direction || "—"}</td><td>${cell(row.awg_vpp, 4)}</td><td>${cell(row.current_peak_a, 5)}</td><td>${cell(row.voltage_peak_v, 5)}</td><td>${cell(row.receiver_peak_v, 5)}</td><td>${cell(row.current_thd_pct, 4)}</td><td>${cell(row.receiver_thd_pct, 4)}</td><td>${cell(row.K_ME_v_per_a, 5)}</td><td>${cell(row.D_wave_pct, 4)}</td><td>${cell(row.correlation, 5)}</td><td>${cell(row.compression_db, 4)}</td><td>${cell(summary?.sweep_difference_receiver_pct, 4)}</td><td>${monitorWindowLabel(row)}</td><td>${row.monitor_state || "—"}${row.monitor_valid ? " · valid" : row.monitor_valid === false ? " · invalid" : ""}${row.suspected_trip ? " · 疑似跳闸" : ""}</td><td>${row.status || "—"}${row.engineering_state ? ` · ${row.engineering_state}` : ""}</td><td>${row.safety_state || "OK"}${row.safety_state === "WARN" ? ` · ${row.safety_message || ""}` : ""}</td></tr>`;
  }).join("");
  const selector = $("linMeasurementRunSelect");
  const previous = selector.value;
  selector.replaceChildren(...rows.map((row) => new Option(`${row.direction} · ${Number(row.awg_vpp).toFixed(3)} Vpp · #${row.repeat}${row.valid ? "" : ` · ${row.status}`}`, String(row.run_index))));
  if (rows.some((row) => String(row.run_index) === previous)) selector.value = previous;
  else if (rows.length) selector.value = String(last.run_index);
  drawLinearityMeasurementCharts();
  loadLinearityMeasurementWave();
}

function cell(value, digits = 5) {
  const numberValue = lcrNumeric(value);
  return Number.isFinite(numberValue) ? numberValue.toPrecision(digits) : "—";
}

function monitorWindowLabel(row) {
  const start = lcrNumeric(row.monitor_analysis_start_us);
  const end = lcrNumeric(row.monitor_analysis_end_us);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return "—";
  return `${start.toFixed(2)}–${end.toFixed(2)} μs · ${row.monitor_analysis_cycles ?? "—"} 周期`;
}

function drawLinearityScatter(canvasId, rows, xKey, series, unit, emptyText = "尚无有效数据") {
  const canvas = $(canvasId);
  if (!canvas) return;
  const { ctx, width, height } = canvasSetup(canvas);
  ctx.clearRect(0, 0, width, height);
  const points = rows.flatMap((row) => series.map((item) => [lcrNumeric(row[xKey]), lcrNumeric(row[item.key])]))
    .filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y));
  if (!points.length) {
    ctx.fillStyle = "#78909c"; ctx.font = "11px sans-serif"; ctx.textAlign = "center";
    ctx.fillText(emptyText, width / 2, height / 2); ctx.textAlign = "left"; return;
  }
  let xMin = Math.min(...points.map((p) => p[0])), xMax = Math.max(...points.map((p) => p[0]));
  let yMin = Math.min(...points.map((p) => p[1])), yMax = Math.max(...points.map((p) => p[1]));
  if (xMin === xMax) { const p = Math.max(Math.abs(xMin) * .08, .01); xMin -= p; xMax += p; }
  if (yMin === yMax) { const p = Math.max(Math.abs(yMin) * .08, 1e-9); yMin -= p; yMax += p; }
  else { const p = (yMax - yMin) * .08; yMin -= p; yMax += p; }
  const left = 66, right = width - 18, top = 34, bottom = height - 38;
  drawAxes(ctx, width, height, left, top, right, bottom,
    (r) => (xMin + r * (xMax - xMin)).toPrecision(3),
    (r) => (yMin + r * (yMax - yMin)).toPrecision(3), 65);
  series.forEach((item, index) => {
    ["up", "down"].forEach((direction) => {
      const group = rows.filter((row) => (row.direction || "up") === direction)
        .map((row) => ({ x: lcrNumeric(row[xKey]), y: lcrNumeric(row[item.key]) }))
        .filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y)).sort((a, b) => a.x - b.x);
      if (!group.length) return;
      ctx.strokeStyle = item.color || colors[index]; ctx.fillStyle = item.color || colors[index]; ctx.lineWidth = 1.6;
      ctx.setLineDash(direction === "down" ? [5, 3] : []); ctx.beginPath();
      group.forEach((p, pointIndex) => {
        const x = left + (p.x - xMin) / (xMax - xMin) * (right - left);
        const y = bottom - (p.y - yMin) / (yMax - yMin) * (bottom - top);
        if (pointIndex) ctx.lineTo(x, y); else ctx.moveTo(x, y);
        ctx.fillRect(x - 2, y - 2, 4, 4);
      }); ctx.stroke(); ctx.setLineDash([]);
    });
    ctx.fillStyle = item.color || colors[index]; ctx.font = "10px sans-serif"; ctx.fillText(item.label, left + index * 130, 16);
  });
  ctx.fillStyle = "#78909c"; ctx.textAlign = "right"; ctx.fillText("I_peak / A", right, 16); ctx.textAlign = "left"; ctx.fillText(unit, 7, top - 8);
}

function drawLinearityMeasurementCharts() {
  if (!latestLinearityMeasurement) return;
  const rows = latestLinearityMeasurement.run_rows || [];
  const valid = rows.filter((row) => row.valid);
  drawLinearityScatter("linMeasurementAmplitudeCanvas", valid, "current_peak_a", [{ key: "receiver_peak_v", label: "Receiver peak", color: "#2f73ff" }], "V");
  drawLinearityScatter("linMeasurementKmeCanvas", valid, "current_peak_a", [{ key: "K_ME_v_per_a", label: "K_ME", color: "#59a9ff" }], "V/A");
  drawLinearityScatter("linMeasurementThdCanvas", valid, "current_peak_a", [{ key: "current_thd_pct", label: "THD_I", color: "#c98435" }, { key: "receiver_thd_pct", label: "THD_RX", color: "#2f73ff" }], "%");
  drawLinearityScatter("linMeasurementShapeCanvas", valid, "current_peak_a", [{ key: "D_wave_pct", label: "D_wave", color: "#7eb6e8" }], "%");
  drawLinearityScatter("linMeasurementCorrCanvas", valid, "current_peak_a", [{ key: "correlation", label: "Correlation", color: "#59a9ff" }], "r");
  drawLinearityScatter("linMeasurementCompressionCanvas", valid, "current_peak_a", [{ key: "compression_db", label: "Compression", color: "#c98435" }], "dB");
  const direction = (latestLinearityMeasurement.summary_rows || []).filter((row) => row.direction === "up" && row.sweep_difference_receiver_pct != null)
    .map((row) => ({ direction: "up", current_peak_a: row.current_peak_a_mean, sweep_difference_receiver_pct: row.sweep_difference_receiver_pct }));
  drawLinearityScatter("linMeasurementDirectionCanvas", direction, "current_peak_a", [{ key: "sweep_difference_receiver_pct", label: "正反扫差异", color: "#c98435" }], "%");
}

async function loadLinearityMeasurementWave() {
  if (!latestLinearityMeasurement || !$("linMeasurementRunSelect")) return;
  const runIndex = $("linMeasurementRunSelect").value;
  if (!runIndex) return;
  try {
    latestLinearityMeasurementWave = await api("/api/lcr-analysis/run-preview", { method: "POST",
      body: JSON.stringify({ mode: "linearity", directory: latestLinearityMeasurement.directory, run_index: Number(runIndex) }) });
    drawLinearityMeasurementWave();
  } catch (error) { setEvent(`线性度波形预览失败：${error.message}`, "error"); }
}

function drawLinearityMeasurementWave() {
  const wave = latestLinearityMeasurementWave;
  const canvas = $("linMeasurementWaveCanvas");
  if (!wave || !canvas || !wave.time_s?.length) return;
  const { ctx, width, height } = canvasSetup(canvas); ctx.clearRect(0, 0, width, height);
  const t = wave.time_s, left = 70, right = width - 18, top = 30, bottom = height - 24;
  const tMin = t[0], tMax = t.at(-1), entries = Object.entries(wave.channels || {}).slice(0, 3);
  const reference = wave.reference_receiver_v || [], refTime = wave.reference_time_s || [];
  const aligned = wave.current_receiver_aligned_v || [], alignedTime = wave.current_receiver_aligned_time_s || [];
  const hasOverlay = reference.length && aligned.length && refTime.length === reference.length && alignedTime.length === aligned.length;
  const overlayHeight = hasOverlay ? Math.min(86, (bottom - top) * 0.30) : 0;
  const rawBottom = bottom - overlayHeight, band = (rawBottom - top) / Math.max(entries.length, 1);
  entries.forEach((entry, index) => {
    const [name, values] = entry; if (!Array.isArray(values) || !values.length) return;
    const yMin = Math.min(...values), yMax = Math.max(...values);
    const span = Math.max(yMax - yMin, 1e-9);
    const y0 = top + index * band;
    ctx.strokeStyle = "rgba(120,144,156,.16)"; ctx.beginPath(); ctx.moveTo(left, y0 + band); ctx.lineTo(right, y0 + band); ctx.stroke();
    ctx.strokeStyle = colors[index]; ctx.lineWidth = 1.2; ctx.beginPath();
    values.forEach((value, i) => { const x = left + (t[i] - tMin) / Math.max(tMax - tMin, 1e-15) * (right - left); const y = y0 + band - 8 - (value - yMin) / span * Math.max(band - 20, 1); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }); ctx.stroke();
    ctx.fillStyle = colors[index]; ctx.font = "10px sans-serif"; ctx.fillText(`${name} raw V · [${yMin.toPrecision(3)}, ${yMax.toPrecision(3)}]`, 5, y0 + 12);
  });
  if (hasOverlay) {
    const overlayTop = rawBottom + 6, overlayBottom = bottom - 8;
    const xMin = Math.min(refTime[0], alignedTime[0]), xMax = Math.max(refTime.at(-1), alignedTime.at(-1));
    const yMin = Math.min(...reference, ...aligned), yMax = Math.max(...reference, ...aligned), ySpan = Math.max(yMax - yMin, 1e-12);
    ctx.strokeStyle = "rgba(120,144,156,.25)"; ctx.beginPath(); ctx.moveTo(left, overlayTop); ctx.lineTo(right, overlayTop); ctx.stroke();
    [[refTime, reference, "#f0f4f5", [5, 3]], [alignedTime, aligned, "#7eb6e8", []]].forEach(([times, values, color, dash]) => {
      ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.setLineDash(dash); ctx.beginPath();
      values.forEach((value, i) => { const x = left + (times[i] - xMin) / Math.max(xMax - xMin, 1e-15) * (right - left); const y = overlayBottom - (value - yMin) / ySpan * (overlayBottom - overlayTop); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); });
      ctx.stroke(); ctx.setLineDash([]);
    });
    ctx.fillStyle = "#f0f4f5"; ctx.font = "10px sans-serif"; ctx.fillText("Reference Receiver", left, overlayTop + 11);
    ctx.fillStyle = "#7eb6e8"; ctx.fillText("Current Receiver · aligned", left + 118, overlayTop + 11);
    ctx.fillStyle = "#78909c"; ctx.textAlign = "left"; ctx.fillText(`${(xMin * 1e6).toPrecision(4)} μs · direct-wave zoom`, left, height - 5); ctx.textAlign = "right"; ctx.fillText(`${(xMax * 1e6).toPrecision(4)} μs`, right, height - 5); ctx.textAlign = "left";
  }
  if (!hasOverlay) { ctx.fillStyle = "#78909c"; ctx.textAlign = "right"; ctx.fillText(`${(tMax * 1e6).toPrecision(4)} μs`, right, height - 6); ctx.textAlign = "left"; }
}

async function pollStatus() {
  try {
    const status = await api("/api/status");
    setEvent(status.message, status.state);
    updateTripAlarm(status);
    const running = ["running", "paused"].includes(status.state);
    if (running && !polling) polling = setInterval(pollStatus, 350);
    setDisabled("stopBtn", !(running && status.task_kind === "capture"));
    setDisabled("sweepStopBtn", !(running && status.task_kind === "sweep"));
    setDisabled("lcrStopBtn", !(running && ["lcr", "lcr_calibration"].includes(status.task_kind)));
    setDisabled("bigLcrStopBtn", !(running && status.task_kind === "big_lcr"));
    setDisabled("linearityStopBtn", !(running && status.task_kind === "lcr_linearity"));
    if (status.task_kind === "sweep" && status.progress) {
      const progress = status.progress;
      const percent = progress.total_runs ? progress.run_index / progress.total_runs * 100 : 0;
      setWidth("sweepProgressBar", `${Math.min(100, percent)}%`);
      if ($("sweepProgressText")) $("sweepProgressText").textContent = progress.run_index
        ? `${progress.run_index}/${progress.total_runs} 次 · ${Number(progress.frequency_hz / 1000).toFixed(3).replace(/\.0+$/, "")} kHz · ${progress.cycles} 周期 · 重复 ${progress.repeat}/${progress.repeats}`
        : `共 ${progress.total_points} 个参数点，${progress.total_runs} 次采集`;
    }
    if (["lcr", "lcr_calibration"].includes(status.task_kind) && status.progress) {
      const progress = status.progress;
      const percent = progress.total_runs ? progress.run_index / progress.total_runs * 100 : 0;
      setWidth("lcrProgressBar", `${Math.min(100, percent)}%`);
      if ($("lcrProgressText")) $("lcrProgressText").textContent = progress.run_index
        ? `${progress.run_index}/${progress.total_runs} 次 · ${(Number(progress.frequency_hz) / 1000).toFixed(3).replace(/\.000$/, "")} kHz · 重复 ${progress.repeat}/${progress.repeats}`
        : `共 ${progress.total_points} 个频点，${progress.total_runs} 次采集`;
    }
    if (status.task_kind === "big_lcr" && status.progress) {
      const progress = status.progress;
      const percent = progress.total_runs ? progress.run_index / progress.total_runs * 100 : 0;
      setWidth("bigLcrProgressBar", `${Math.min(100, percent)}%`);
      if ($("bigLcrProgressText")) $("bigLcrProgressText").textContent = progress.run_index
        ? `${progress.run_index}/${progress.total_runs} 次 · ${(Number(progress.frequency_hz) / 1000).toFixed(3).replace(/\.000$/, "")} kHz · ${Number(progress.awg_drive_vpp).toFixed(3)} Vpp · 重复 ${progress.repeat}/${progress.repeats}${progress.safety_state ? ` · ${progress.safety_state}` : ""}`
        : `共 ${progress.total_points} 个频率/Vpp 组合点，${progress.total_runs} 次采集`;
    }
    if (status.task_kind === "lcr_linearity" && status.progress) {
      const progress = status.progress;
      const percent = progress.total_runs ? progress.run_index / progress.total_runs * 100 : 0;
      setWidth("linearityProgressBar", `${Math.min(100, percent)}%`);
      if ($("linearityProgressText")) $("linearityProgressText").textContent = progress.run_index
        ? `${progress.run_index}/${progress.total_runs} 次 · ${(Number(progress.awg_vpp)).toFixed(3)} Vpp · ${progress.direction} 扫 · 重复 ${progress.repeat}/${progress.repeats}`
        : `共 ${progress.total_runs} 次三通道同步采集`;
    }
    if (["complete", "stopped"].includes(status.state)) {
      clearInterval(polling);
      polling = null;
      ["captureBtn", "sweepBtn", "lcrStartBtn", "lcrCalibrateBtn", "bigLcrStartBtn", "linearityStartBtn"].forEach((id) => setDisabled(id, false));
      ["stopBtn", "sweepStopBtn", "lcrStopBtn", "bigLcrStopBtn", "linearityStopBtn"].forEach((id) => setDisabled(id, true));
      if (status.task_kind === "sweep" && status.has_sweep_result) {
        latestSweep = await api("/api/sweep/result");
        updateSweepResult(latestSweep);
        if (status.state === "complete") setWidth("sweepProgressBar", "100%");
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
        if (status.state === "complete") setWidth("lcrProgressBar", "100%");
      } else if (status.task_kind === "big_lcr" && status.has_big_lcr_result) {
        latestBigLcr = await api("/api/big-lcr/result");
        if (latestBigLcr.safety_tripped && $("bigLcrSafetyAck")) $("bigLcrSafetyAck").checked = false;
        if (latestBigLcr.run_rows.length) {
          latestBigLcrWave = await api("/api/result/display", {
            method: "POST",
            body: JSON.stringify({ filter: { enabled: false }, max_points: 6000 }),
          });
        }
        updateBigLcrResult();
        if (status.state === "complete") setWidth("bigLcrProgressBar", "100%");
      } else if (status.task_kind === "lcr_linearity" && status.has_lcr_linearity_result) {
        latestLinearityMeasurement = await api("/api/lcr-linearity/result");
        renderLinearityMeasurement();
        if (status.state === "complete") setWidth("linearityProgressBar", "100%");
      }
    } else if (status.state === "error") {
      clearInterval(polling);
      polling = null;
      ["captureBtn", "sweepBtn", "lcrStartBtn", "lcrCalibrateBtn", "bigLcrStartBtn", "linearityStartBtn"].forEach((id) => setDisabled(id, false));
      ["stopBtn", "sweepStopBtn", "lcrStopBtn", "bigLcrStopBtn", "linearityStopBtn"].forEach((id) => setDisabled(id, true));
    }
    if (!running) updateBigSafetyStatus(latestBigLcr?.run_rows?.at(-1) || null);
  } catch (error) {
    setEvent(error.message, "error");
  }
}

async function stopCapture() {
  try {
    ["stopBtn", "sweepStopBtn", "lcrStopBtn", "bigLcrStopBtn", "linearityStopBtn", "tripStopBtn"].forEach((id) => setDisabled(id, true));
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
  status.style.color = "#2f73ff";
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
    $("measureFilterStatus").style.color = "#d94b4b";
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
  $("overflowMetric").style.color = s.overflow_channels.length ? "#d94b4b" : "#2f73ff";
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
  $("sweepIdleStage").hidden = true;
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
    [Number(run.direct_start_us) * 1e-6, Number(run.direct_end_us) * 1e-6, "rgba(47,115,255,.08)", `直达波 · ${Number(run.nominal_duration_us).toFixed(1)} μs`],
    [Number(run.tail_start_us) * 1e-6, Number(run.tail_end_us) * 1e-6, "rgba(255,182,77,.08)", "拖尾评价窗"],
    [Number(run.reflection_start_us) * 1e-6, Number(run.reflection_end_us) * 1e-6, "rgba(201,132,53,.10)", "端面反射"],
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
      ctx.fillStyle = archiveColor(ratio, higherIsBetter);
      const x = left + xIndex * cellWidth;
      const y = top + (cycles.length - 1 - yIndex) * cellHeight;
      ctx.fillRect(x + 1, y + 1, cellWidth - 2, cellHeight - 2);
      if (cellWidth > 58 && cellHeight > 30) {
        ctx.fillStyle = "rgba(239,244,250,.92)";
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
    ctx.strokeStyle = "#2f73ff"; ctx.lineWidth = 2; ctx.beginPath();
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
      ctx.fillStyle = "#c98435"; ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2); ctx.fill();
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

function updateBigLcrResult() {
  if (!latestBigLcr || !$("bigLcrTableBody")) return;
  const rows = [...(latestBigLcr.summary_rows || [])].sort((a, b) =>
    Number(a.frequency_hz) - Number(b.frequency_hz) || Number(a.awg_drive_vpp) - Number(b.awg_drive_vpp));
  const cell = (value, digits = 6) => Number.isFinite(lcrNumeric(value)) ? Number(value).toPrecision(digits) : "—";
  $("bigLcrResultPath").textContent = latestBigLcr.directory || "—";
  $("bigLcrTableBody").innerHTML = rows.map((row) => `<tr><td>${cell(Number(row.frequency_hz) / 1000, 6)}</td><td>${cell(row.awg_drive_vpp, 5)}</td><td>${row.valid_repeats ?? row.repeats_completed ?? "—"} / ${row.repeats_completed ?? "—"}</td><td>${cell(row.impedance_magnitude_ohm)}</td><td>${cell(row.phase_deg, 5)}</td><td>${cell(row.series_resistance_ohm)}</td><td>${cell(row.series_reactance_ohm)}</td><td>${cell(row.effective_inductance_h)}</td><td>${cell(row.quality_factor, 5)}</td><td>${cell(row.voltage_rms_v)}</td><td>${cell(row.current_rms_a)}</td><td>${cell(row.voltage_vpp_v)}</td><td>${cell(row.current_peak_a)}</td><td>${cell(row.voltage_snr_db, 5)}</td><td>${cell(row.current_snr_db, 5)}</td><td>${row.safety_state || "—"}</td></tr>`).join("");
  const last = latestBigLcr.run_rows?.at(-1) || rows.at(-1);
  if (last) {
    $("bigLcrLastPoint").textContent = `当前指标点：${(Number(last.frequency_hz) / 1000).toPrecision(6)} kHz · AWG ${Number(last.awg_drive_vpp).toPrecision(5)} Vpp · Pico 电压 ±${last.voltage_range || "?"} / 电流 ±${last.current_range || "?"} · ${last.auto_range_attempts || 1} 次采集`;
    $("bigLcrMagnitudeMetric").textContent = engineering(last.impedance_magnitude_ohm, "Ω");
    $("bigLcrPhaseMetric").textContent = Number.isFinite(Number(last.phase_deg)) ? `${Number(last.phase_deg).toFixed(3)}°` : "—";
    $("bigLcrRxMetric").textContent = `${engineering(last.series_resistance_ohm, "Ω")} / ${engineering(last.series_reactance_ohm, "Ω")}`;
    $("bigLcrLeffMetric").textContent = engineering(last.effective_inductance_h, "H");
    $("bigLcrQMetric").textContent = engineering(last.quality_factor);
    $("bigLcrViMetric").textContent = `${engineering(last.voltage_rms_v, "V")} / ${engineering(last.current_rms_a, "A")}`;
    $("bigLcrSafetyMetric").textContent = `${last.safety_state || "—"}${last.safety_message ? ` · ${last.safety_message}` : ""}`;
    $("bigLcrVoltageSnrMetric").textContent = engineering(last.voltage_snr_db, "dB");
    $("bigLcrCurrentSnrMetric").textContent = engineering(last.current_snr_db, "dB");
    updateBigSafetyStatus(last);
  }
  drawBigLcrCharts(); drawBigLcrWaves();
}

function linearityAnalysisSettings() {
  return {
    harmonic_order: Math.round(number("linearityHarmonicOrder")),
    alignment_max_shift_us: number("linearityAlignShift"),
    low_current_reference_count: Math.round(number("linearityReferenceCount")),
    low_current_reference_max_a: number("linearityReferenceMaxCurrent"),
    force_origin: $("linearityForceOrigin").checked,
    noise_start_us: number("linearityAnalysisNoiseStart"), noise_end_us: number("linearityAnalysisNoiseEnd"),
    direct_start_us: number("linearityAnalysisDirectStart"), direct_end_us: number("linearityAnalysisDirectEnd"),
    echo_start_us: number("linearityAnalysisEchoStart"), echo_end_us: number("linearityAnalysisEchoEnd"),
    threshold_d_wave_pct: number("linearityLimitDwave"),
    threshold_receiver_thd_pct: number("linearityLimitRxThd"),
    threshold_current_thd_pct: number("linearityLimitIThd"),
    threshold_compression_db: number("linearityLimitCompression"),
    threshold_kme_deviation_pct: number("linearityLimitKme"),
    threshold_correlation: number("linearityLimitCorrelation"),
  };
}

async function analyzeBigLinearity(reanalyze = false) {
  const button = reanalyze ? $("linearityReanalyzeBtn") : $("linearityAnalyzeBtn");
  if (button) button.disabled = true;
  $("linearityStatus").textContent = reanalyze ? "正在读取 NPZ 并按当前设置重新计算…" : "正在加载已保存的线性度结果…";
  try {
    latestLinearity = await api("/api/lcr-analysis/load", {
      method: "POST",
      body: JSON.stringify({ mode: "linearity", directory: $("linearityDirectory").value.trim(), reanalyze,
        settings: linearityAnalysisSettings() }),
    });
    latestLinearityWave = null;
    $("linearityDirectory").value = latestLinearity.directory;
    const frequencies = [...new Set((latestLinearity.run_rows || []).map((row) => Number(row.frequency_hz)).filter(Number.isFinite))].sort((a, b) => a - b);
    const select = $("linearityFrequency");
    const previous = select.value;
    select.replaceChildren(...frequencies.map((frequency) => new Option(`${(frequency / 1000).toPrecision(6)} kHz`, String(frequency))));
    if (frequencies.some((frequency) => String(frequency) === previous)) select.value = previous;
    else if (frequencies.length) select.value = String(frequencies[0]);
    updateLinearityView();
    $("linearityStatus").textContent = `${reanalyze ? "重新分析完成" : "已加载"}：${latestLinearity.valid_runs}/${latestLinearity.total_runs} 次有效；${(latestLinearity.warnings || []).length} 条诊断`;
    if (latestLinearity.warnings?.length) setEvent(latestLinearity.warnings[0], "warning");
  } catch (error) {
    $("linearityStatus").textContent = `分析失败：${error.message}`;
    setEvent(error.message, "error");
  } finally { if (button) button.disabled = false; }
}

async function loadLcrFolder(mode, reanalyze = false) {
  const ids = { big_lcr: ["bigFolderDirectory", "bigFolderStatus"], small_lcr: ["smallFolderDirectory", "smallFolderStatus"] };
  if (mode === "linearity") return analyzeBigLinearity(reanalyze);
  const [pathId, statusId] = ids[mode];
  const buttonId = mode === "big_lcr" ? (reanalyze ? "bigFolderReanalyzeBtn" : "bigFolderLoadBtn")
    : (reanalyze ? "smallFolderReanalyzeBtn" : "smallFolderLoadBtn");
  $(buttonId).disabled = true;
  $(statusId).textContent = reanalyze ? "正在重新计算原始 NPZ…" : "正在读取已保存结果…";
  try {
    const data = await api("/api/lcr-analysis/load", { method: "POST", body: JSON.stringify({ mode, directory: $(pathId).value.trim(), reanalyze }) });
    lcrFolderStates[mode] = data;
    $(pathId).value = data.directory;
    renderLcrFolder(mode);
    $(statusId).textContent = `${reanalyze ? "raw 重算完成" : "加载完成"}：${data.run_rows.length} 次记录，${data.summary_rows.length} 个参数点`;
  } catch (error) {
    $(statusId).textContent = `分析失败：${error.message}`; setEvent(error.message, "error");
  } finally { $(buttonId).disabled = false; }
}

function renderLcrFolder(mode) {
  const data = lcrFolderStates[mode]; if (!data) return;
  const big = mode === "big_lcr", prefix = big ? "bigFolder" : "smallFolder";
  $(`${prefix}ResultPath`).textContent = data.directory;
  $(`${prefix}Count`).textContent = `${data.valid_runs ?? data.run_rows.length} / ${data.total_runs ?? data.run_rows.length}`;
  $(`${prefix}PointCount`).textContent = String(data.summary_rows.length);
  if (!big) {
    const lcrConfig = data.config?.lcr || {};
    $("smallFolderCalibration").textContent = lcrConfig.calibration_enabled ? "精准电阻复数校准" : "未使用校准";
  }
  const rows = data.summary_rows || [];
  if (big) {
    $("bigFolderTableBody").innerHTML = rows.map((r) => `<tr><td>${cell(Number(r.frequency_hz) / 1000, 5)}</td><td>${cell(r.awg_drive_vpp, 4)}</td><td>${cell(r.impedance_magnitude_ohm)}</td><td>${cell(r.phase_deg, 5)}</td><td>${cell(r.series_resistance_ohm)}</td><td>${cell(r.series_reactance_ohm)}</td><td>${cell(r.effective_inductance_h)}</td><td>${cell(r.quality_factor)}</td><td>${cell(r.voltage_rms_v)}</td><td>${cell(r.current_rms_a)}</td><td>${cell(r.voltage_vpp_v)}</td><td>${cell(r.current_peak_a)}</td><td>${cell(r.voltage_snr_db)}</td><td>${cell(r.current_snr_db)}</td><td>${r.safety_state || "—"}</td></tr>`).join("");
    const maxVpp = Math.max(...rows.map((r) => Number(r.awg_drive_vpp || 0)));
    const curve = rows.filter((r) => Number(r.awg_drive_vpp || 0) === maxVpp).sort((a, b) => Number(a.frequency_hz) - Number(b.frequency_hz));
    drawFolderFrequencyCurve("bigFolderCurveCanvas", curve, [{ key: "impedance_magnitude_ohm", label: "|Z|", color: "#2f73ff" }, { key: "series_resistance_ohm", label: "Rs", color: "#c98435" }, { key: "series_reactance_ohm", label: "Xs", color: "#59a9ff" }]);
    drawBigFolderGrid(rows);
  } else {
    $("smallFolderTableBody").innerHTML = rows.map((r) => `<tr><td>${cell(Number(r.frequency_hz) / 1000, 5)}</td><td>${cell(r.repeats_completed, 3)}</td><td>${cell(r.impedance_magnitude_ohm)}</td><td>${cell(r.phase_deg, 5)}</td><td>${cell(r.impedance_real_ohm ?? r.series_resistance_ohm)}</td><td>${cell(r.impedance_imag_ohm ?? r.series_reactance_ohm)}</td><td>${cell(r.series_capacitance_f)}</td><td>${cell(r.series_inductance_h)}</td><td>${cell(r.parallel_resistance_ohm)}</td><td>${cell(r.parallel_capacitance_f)}</td><td>${cell(r.parallel_inductance_h)}</td><td>${cell(r.quality_factor)}</td><td>${cell(r.dissipation_factor)}</td><td>${cell(r.voltage_snr_db)}</td><td>${cell(r.current_snr_db)}</td></tr>`).join("");
    drawFolderFrequencyCurve("smallFolderCurveCanvas", rows, [{ key: "impedance_magnitude_ohm", label: "|Z|", color: "#2f73ff" }, { key: "impedance_real_ohm", label: "R", color: "#c98435" }, { key: "impedance_imag_ohm", label: "X", color: "#59a9ff" }]);
  }
  const selector = $(`${prefix}RunSelect`), oldValue = selector.value;
  selector.replaceChildren(...data.run_rows.map((r) => new Option(`#${r.run_index} · ${(Number(r.frequency_hz) / 1000).toPrecision(5)} kHz`, String(r.run_index))));
  if (data.run_rows.some((r) => String(r.run_index) === oldValue)) selector.value = oldValue;
  else if (data.run_rows.length) selector.value = String(data.run_rows.at(-1).run_index);
  loadLcrFolderRun(mode);
}

function drawFolderFrequencyCurve(canvasId, rows, series) {
  const values = rows.map((row) => ({ ...row, frequency_hz: Number(row.frequency_hz) }));
  const numeric = values.flatMap((row) => series.map((item) => lcrNumeric(row[item.key]))).filter(Number.isFinite);
  const canvas = $(canvasId); const { ctx, width, height } = canvasSetup(canvas); ctx.clearRect(0, 0, width, height);
  if (!values.length || !numeric.length) { ctx.fillStyle = "#78909c"; ctx.textAlign = "center"; ctx.fillText("没有可绘制的已保存指标", width / 2, height / 2); ctx.textAlign = "left"; return; }
  let xMin = Math.min(...values.map((r) => r.frequency_hz)), xMax = Math.max(...values.map((r) => r.frequency_hz));
  let yMin = Math.min(...numeric), yMax = Math.max(...numeric);
  if (xMin === xMax) { xMin *= .95; xMax *= 1.05; } if (yMin === yMax) { const d = Math.max(Math.abs(yMin) * .1, 1e-9); yMin -= d; yMax += d; }
  const left = 64, right = width - 16, top = 32, bottom = height - 34;
  drawAxes(ctx, width, height, left, top, right, bottom, (r) => ((xMin + r * (xMax - xMin)) / 1000).toPrecision(3), (r) => (yMin + r * (yMax - yMin)).toPrecision(3), 65);
  series.forEach((item, index) => {
    ctx.strokeStyle = item.color; ctx.lineWidth = 1.5; ctx.beginPath(); let connected = false;
    values.forEach((row) => { const yv = lcrNumeric(row[item.key]); if (!Number.isFinite(yv)) { connected = false; return; } const x = left + (row.frequency_hz - xMin) / (xMax - xMin) * (right - left); const y = bottom - (yv - yMin) / (yMax - yMin) * (bottom - top); if (connected) ctx.lineTo(x, y); else ctx.moveTo(x, y); connected = true; }); ctx.stroke();
    ctx.fillStyle = item.color; ctx.font = "10px sans-serif"; ctx.fillText(item.label, left + index * 60, 14);
  });
}

function drawBigFolderGrid(rows) {
  const canvas = $("bigFolderGridCanvas"); const { ctx, width, height } = canvasSetup(canvas); ctx.clearRect(0, 0, width, height);
  const f = [...new Set(rows.map((r) => Number(r.frequency_hz)))].sort((a, b) => a - b);
  const v = [...new Set(rows.map((r) => Number(r.awg_drive_vpp)))].sort((a, b) => a - b);
  const z = rows.map((r) => Number(r.impedance_magnitude_ohm)).filter(Number.isFinite);
  if (!f.length || !v.length || !z.length) { ctx.fillStyle = "#78909c"; ctx.textAlign = "center"; ctx.fillText("没有可显示的 |Z| 网格", width / 2, height / 2); ctx.textAlign = "left"; return; }
  const left = 55, right = width - 12, top = 15, bottom = height - 36, cellW = (right - left) / f.length, cellH = (bottom - top) / v.length;
  const min = Math.min(...z), max = Math.max(...z);
  rows.forEach((r) => { const fi = f.indexOf(Number(r.frequency_hz)), vi = v.indexOf(Number(r.awg_drive_vpp)); const value = Number(r.impedance_magnitude_ohm); if (!Number.isFinite(value)) return; const q = (value - min) / Math.max(max - min, 1e-12); ctx.fillStyle = signalHeatColor(q); ctx.fillRect(left + fi * cellW, bottom - (vi + 1) * cellH, Math.max(1, cellW - 1), Math.max(1, cellH - 1)); });
  ctx.fillStyle = "#78909c"; ctx.font = "10px Consolas"; ctx.fillText(`${(Math.min(...f) / 1000).toPrecision(3)} kHz`, left, height - 8); ctx.textAlign = "right"; ctx.fillText(`${(Math.max(...f) / 1000).toPrecision(3)} kHz`, right, height - 8); ctx.textAlign = "left"; ctx.fillText(`${Math.max(...v).toPrecision(3)} Vpp`, 3, top + 8); ctx.fillText(`${Math.min(...v).toPrecision(3)} Vpp`, 3, bottom);
}

async function loadLcrFolderRun(mode) {
  const data = lcrFolderStates[mode]; if (!data) return;
  const big = mode === "big_lcr", prefix = big ? "bigFolder" : "smallFolder";
  const runIndex = $(`${prefix}RunSelect`).value; if (!runIndex) return;
  try {
    const wave = await api("/api/lcr-analysis/run-preview", { method: "POST", body: JSON.stringify({ mode, directory: data.directory, run_index: Number(runIndex) }) });
    drawFolderRawWave(big ? "bigFolderWaveCanvas" : "smallFolderWaveCanvas", wave);
  } catch (error) { $(big ? "bigFolderStatus" : "smallFolderStatus").textContent = `波形预览失败：${error.message}`; }
}

function drawFolderRawWave(canvasId, wave) {
  const canvas = $(canvasId); if (!wave?.time_s?.length) return;
  const { ctx, width, height } = canvasSetup(canvas); ctx.clearRect(0, 0, width, height);
  const names = Object.keys(wave.channels || {}), t = wave.time_s;
  const left = 72, right = width - 16, top = 10, bottom = height - 18, band = (bottom - top) / Math.max(names.length, 1);
  names.forEach((name, index) => { const arr = wave.channels[name]; const ymin = Math.min(...arr), ymax = Math.max(...arr), span = Math.max(ymax - ymin, 1e-12), y0 = top + index * band; ctx.strokeStyle = colors[index]; ctx.beginPath(); arr.forEach((value, j) => { const x = left + (t[j] - t[0]) / Math.max(t.at(-1) - t[0], 1e-15) * (right - left), y = y0 + band - 3 - (value - ymin) / span * Math.max(band - 8, 1); if (j) ctx.lineTo(x, y); else ctx.moveTo(x, y); }); ctx.stroke(); ctx.fillStyle = colors[index]; ctx.font = "10px sans-serif"; ctx.fillText(`${name} raw V [${ymin.toPrecision(3)}, ${ymax.toPrecision(3)}]`, 4, y0 + 10); });
}

function updateLinearityView() {
  if (!latestLinearity || document.body.dataset.page !== "lcr-linearity") return;
  const frequency = Number($("linearityFrequency").value);
  const rows = (latestLinearity.run_rows || []).filter((row) => Number(row.frequency_hz) === frequency)
    .sort((a, b) => Number(a.current_peak_a ?? a.awg_vpp ?? a.awg_drive_vpp) - Number(b.current_peak_a ?? b.awg_vpp ?? b.awg_drive_vpp));
  $("linearityResultPath").textContent = latestLinearity.directory;
  $("linearityRunCount").textContent = `${latestLinearity.valid_runs} / ${latestLinearity.total_runs}`;
  $("linearityOrderMetric").textContent = `${latestLinearity.analysis_settings?.harmonic_order ?? latestLinearity.harmonic_order ?? "—"} 阶`;
  $("linearityFrequencyMetric").textContent = frequency ? `${(frequency / 1000).toPrecision(6)} kHz` : "—";
  const pointSummaries = new Map((latestLinearity.summary_rows || []).map((item) =>
    [`${item.direction || "up"}:${Number(item.frequency_hz)}:${Number(item.awg_vpp ?? item.awg_drive_vpp)}`, item]));
  $("linearityTableBody").innerHTML = rows.map((row) => {
    const summary = pointSummaries.get(`${row.direction || "up"}:${Number(row.frequency_hz)}:${Number(row.awg_vpp ?? row.awg_drive_vpp)}`);
    return `<tr><td>${row.direction || "—"}</td><td>${cell(Number(row.frequency_hz) / 1000, 5)}</td><td>${cell(row.awg_vpp ?? row.awg_drive_vpp, 4)}</td><td>${cell(row.current_peak_a)}</td><td>${cell(row.receiver_peak_v ?? row.current_fundamental_rms_a)}</td><td>${cell(row.K_ME_v_per_a)}</td><td>${cell(row.current_thd_pct)}</td><td>${cell(row.receiver_thd_pct ?? row.voltage_thd_pct)}</td><td>${cell(row.D_wave_pct)}</td><td>${cell(row.correlation)}</td><td>${cell(row.compression_db)}</td><td>${cell(summary?.sweep_difference_receiver_pct, 4)}</td><td>${cell(row.echo_peak_v)}</td><td>${cell(row.D_echo_pct)}</td><td>${monitorWindowLabel(row)}</td><td>${row.monitor_state || "—"}${row.monitor_valid ? " · valid" : row.monitor_valid === false ? " · invalid" : ""}${row.suspected_trip ? " · 疑似跳闸" : ""}</td><td>${row.status || row.analysis_state || "—"}${row.engineering_state ? ` · ${row.engineering_state}` : ""}</td><td>${row.safety_state || "—"}${row.safety_state === "WARN" ? ` · ${row.safety_message || ""}` : ""}</td><td>${cell(row.voltage_thd_pct)}</td><td>${cell(row.current_thd_pct)}</td><td>${cell(row.voltage_gain_deviation_db)}</td></tr>`;
  }).join("");
  const runs = rows;
  const runSelect = $("linearityRunSelect");
  const previous = runSelect.value;
  runSelect.replaceChildren(...runs.map((row) => new Option(`${row.direction || ""} · ${Number(row.awg_vpp ?? row.awg_drive_vpp ?? 0).toPrecision(5)} Vpp · 第 ${row.repeat || 1} 次${row.valid === false || row.analysis_state === "ERROR" ? " · 无效" : ""}`, String(row.run_index))));
  if (runs.some((row) => String(row.run_index) === previous)) runSelect.value = previous;
  else if (runs.length) runSelect.value = String(runs.at(-1).run_index);
  const xKey = rows.some((row) => Number.isFinite(lcrNumeric(row.current_peak_a))) ? "current_peak_a" : "awg_drive_vpp";
  const receiverKey = rows.some((row) => Number.isFinite(lcrNumeric(row.receiver_peak_v))) ? "receiver_peak_v" : "current_fundamental_rms_a";
  const rxThdKey = rows.some((row) => Number.isFinite(lcrNumeric(row.receiver_thd_pct))) ? "receiver_thd_pct" : "voltage_thd_pct";
  drawLinearityScatter("linearityReceiveCanvas", rows, xKey, [{ key: receiverKey, label: receiverKey === "receiver_peak_v" ? "Receiver peak" : "旧数据 Current fundamental", color: "#2f73ff" }], receiverKey === "receiver_peak_v" ? "V" : "A RMS");
  drawLinearityScatter("linearityKmeCanvas", rows, xKey, [{ key: "K_ME_v_per_a", label: "K_ME", color: "#59a9ff" }], "V/A");
  drawLinearityScatter("linearityShapeCanvas", rows, xKey, [{ key: "D_wave_pct", label: "D_wave", color: "#7eb6e8" }], "%");
  drawLinearityScatter("linearityCorrCanvas", rows, xKey, [{ key: "correlation", label: "Correlation", color: "#59a9ff" }], "r");
  drawLinearityScatter("linearityCompressionCanvas", rows, xKey, [{ key: "compression_db", label: "Compression", color: "#c98435" }], "dB");
  drawLinearityScatter("linearityEchoCanvas", rows, xKey, [{ key: "echo_peak_v", label: "Echo peak", color: "#c98435" }], "V");
  drawLinearityScatter("linearityEchoShapeCanvas", rows, xKey, [{ key: "D_echo_pct", label: "D_echo", color: "#7eb6e8" }], "%");
  drawLinearityScatter("linearityThdCanvas", rows, xKey, [{ key: "current_thd_pct", label: "THD_I", color: "#c98435" }, { key: rxThdKey, label: rxThdKey === "receiver_thd_pct" ? "THD_RX" : "Legacy V THD", color: "#2f73ff" }], "%");
  const directionRows = (latestLinearity.summary_rows || []).filter((row) => Number(row.frequency_hz) === frequency && row.direction === "up");
  drawLinearityScatter("linearityDirectionCanvas", directionRows, "current_peak_a_mean", [{ key: "sweep_difference_receiver_pct", label: "正反扫幅值差异", color: "#c98435" }], "%");
  drawLinearityScatter("linearityGainCanvas", rows, xKey, [{ key: "voltage_gain_deviation_db", label: "Legacy voltage", color: "#2f73ff" }, { key: "current_gain_deviation_db", label: "Legacy current", color: "#c98435" }], "dB");
  updateLinearityRun();
}

function drawLinearityXY(canvasId, rows, series, unit) {
  const canvas = $(canvasId);
  const { ctx, width, height } = canvasSetup(canvas);
  ctx.clearRect(0, 0, width, height);
  const points = rows.map((row) => Number(row.awg_drive_vpp));
  const values = rows.flatMap((row) => series.map((item) => lcrNumeric(row[item.key]))).filter(Number.isFinite);
  if (!points.length || !values.length) {
    ctx.fillStyle = "#78909c"; ctx.font = "12px sans-serif"; ctx.fillText("当前频率没有有效数据", 30, 40); return;
  }
  let xMin = Math.min(...points), xMax = Math.max(...points);
  let yMin = Math.min(...values), yMax = Math.max(...values);
  if (xMin === xMax) { xMin -= Math.max(0.05, Math.abs(xMin) * 0.1); xMax += Math.max(0.05, Math.abs(xMax) * 0.1); }
  if (yMin === yMax) { yMin -= Math.max(0.1, Math.abs(yMin) * 0.1); yMax += Math.max(0.1, Math.abs(yMax) * 0.1); }
  else { const pad = (yMax - yMin) * 0.1; yMin -= pad; yMax += pad; }
  const left = 60, right = width - 20, top = 35, bottom = height - 36;
  drawAxes(ctx, width, height, left, top, right, bottom,
    (ratio) => (xMin + ratio * (xMax - xMin)).toPrecision(3),
    (ratio) => (yMin + ratio * (yMax - yMin)).toPrecision(3), 60);
  series.forEach((item, seriesIndex) => {
    ctx.strokeStyle = item.color; ctx.fillStyle = item.color; ctx.lineWidth = 2; ctx.beginPath();
    let connected = false;
    rows.forEach((row) => {
      const yValue = lcrNumeric(row[item.key]);
      if (!Number.isFinite(yValue)) { connected = false; return; }
      const x = left + (Number(row.awg_drive_vpp) - xMin) / (xMax - xMin) * (right - left);
      const y = bottom - (yValue - yMin) / (yMax - yMin) * (bottom - top);
      if (connected) ctx.lineTo(x, y); else ctx.moveTo(x, y);
      connected = true;
      ctx.fillRect(x - 2, y - 2, 4, 4);
    });
    ctx.stroke(); ctx.font = "10px sans-serif"; ctx.fillText(item.label, left + seriesIndex * 90, 16);
  });
  ctx.fillStyle = "#78909c"; ctx.textAlign = "right"; ctx.fillText("AWG Vpp / V", right, 16); ctx.textAlign = "left";
  ctx.fillText(unit, 7, top - 8);
}

async function updateLinearityRun() {
  if (!latestLinearity || document.body.dataset.page !== "lcr-linearity") return;
  const run = latestLinearity.run_rows.find((row) => String(row.run_index) === $("linearityRunSelect").value);
  drawLinearityHarmonics(run);
  if (!run) return;
  const requested = run.run_index;
  try {
    const wave = await api("/api/lcr-analysis/run-preview", { method: "POST",
      body: JSON.stringify({ mode: "linearity", directory: latestLinearity.directory, run_index: requested,
        settings: linearityAnalysisSettings() }) });
    if (String(requested) !== $("linearityRunSelect").value) return;
    latestLinearityWave = wave;
    drawLinearityWave();
  } catch (error) {
    $("linearityStatus").textContent = `波形预览失败：${error.message}`;
  }
}

function drawLinearityHarmonics(run) {
  const { ctx, width, height } = canvasSetup($("linearityHarmonicCanvas"));
  ctx.clearRect(0, 0, width, height);
  if (!run) return;
  const voltage = run.receiver_harmonics_rms_v || run.voltage_harmonics_rms_v || [];
  const current = run.current_harmonics_rms_a || [];
  const count = Math.min(voltage.length, current.length);
  const floorDb = -100;
  const left = 55, right = width - 20, top = 34, bottom = height - 35;
  drawAxes(ctx, width, height, left, top, right, bottom,
    (ratio) => String(1 + Math.round(ratio * (count - 1))),
    (ratio) => `${Math.round(floorDb * (1 - ratio))}`, 70);
  for (let n = 0; n < count; n += 1) {
    const center = left + (n + 0.5) * (right - left) / count;
    [voltage, current].forEach((array, side) => {
      const dbc = n === 0 ? 0 : Math.max(floorDb, 20 * Math.log10(Math.max(array[n], 1e-30) / Math.max(array[0], 1e-30)));
      const y = top + (0 - dbc) / (0 - floorDb) * (bottom - top);
      ctx.fillStyle = side === 0 ? "#2f73ff" : "#c98435";
      ctx.fillRect(center + (side ? 1 : -7), y, 6, bottom - y);
    });
  }
  ctx.fillStyle = "#2f73ff"; ctx.fillText(run.receiver_harmonics_rms_v ? "Receiver" : "旧电压", left, 17);
  ctx.fillStyle = "#c98435"; ctx.fillText("Current", left + 65, 17);
  ctx.fillStyle = "#78909c"; ctx.fillText("dBc", 6, top - 8); ctx.textAlign = "right"; ctx.fillText("谐波阶数", right, 17); ctx.textAlign = "left";
}

function drawLinearityWave() {
  if (!latestLinearityWave || document.body.dataset.page !== "lcr-linearity") return;
  const wave = latestLinearityWave;
  const { ctx, width, height } = canvasSetup($("linearityWaveCanvas"));
  ctx.clearRect(0, 0, width, height);
  if (!wave.time_s?.length) return;
  const t = wave.time_s, left = 60, right = width - 20, top = 32, bottom = height - 34;
  const tMin = t[0], tMax = t.at(-1);
  if (wave.channels) {
    const names = Object.keys(wave.channels), band = (bottom - top) / Math.max(names.length, 1);
    names.forEach((name, index) => {
      const values = wave.channels[name];
      const isReceiver = index === names.length - 1;
      const reference = isReceiver ? wave.reference_receiver_v : null;
      const aligned = isReceiver ? wave.current_receiver_aligned_v : null;
      const low = Math.min(...values, ...(reference || []), ...(aligned || [])), high = Math.max(...values, ...(reference || []), ...(aligned || []));
      const span = Math.max(high - low, 1e-12), y0 = top + index * band;
      ctx.strokeStyle = colors[index]; ctx.lineWidth = 1.2; ctx.beginPath();
      values.forEach((value, i) => { const x = left + (t[i] - tMin) / Math.max(tMax - tMin, 1e-15) * (right - left); const y = y0 + band - 4 - (value - low) / span * Math.max(band - 12, 1); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }); ctx.stroke();
      if (reference?.length) {
        const refTime = wave.reference_time_s || []; ctx.strokeStyle = "#f0f4f5"; ctx.setLineDash([4, 3]); ctx.beginPath();
        reference.forEach((value, i) => { const x = left + ((refTime[i] ?? tMin) - tMin) / Math.max(tMax - tMin, 1e-15) * (right - left); const y = y0 + band - 4 - (value - low) / span * Math.max(band - 12, 1); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }); ctx.stroke(); ctx.setLineDash([]);
      }
      if (aligned?.length) {
        const alignedTime = wave.current_receiver_aligned_time_s || []; ctx.strokeStyle = "#7eb6e8"; ctx.setLineDash([]); ctx.lineWidth = 1.4; ctx.beginPath();
        aligned.forEach((value, i) => { const x = left + ((alignedTime[i] ?? tMin) - tMin) / Math.max(tMax - tMin, 1e-15) * (right - left); const y = y0 + band - 4 - (value - low) / span * Math.max(band - 12, 1); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }); ctx.stroke();
      }
      ctx.fillStyle = colors[index]; ctx.font = "10px sans-serif"; ctx.fillText(`${name} raw V`, 4, y0 + 11);
    });
    ctx.fillStyle = "#f0f4f5"; ctx.fillText("浅色虚线：同频参考 Receiver", left, 15); ctx.fillStyle = "#7eb6e8"; ctx.fillText("紫色：当前 Receiver 已对齐波形", left + 190, 15); return;
  }
  drawAxes(ctx, width, height, left, top, right, bottom,
    (ratio) => ((tMin + ratio * (tMax - tMin)) * 1e6).toFixed(1),
    (ratio) => (2 * ratio - 1).toFixed(1), 70);
  for (const edge of [wave.window_start_s, wave.window_end_s]) {
    if (edge == null) continue;
    const x = left + (edge - tMin) / (tMax - tMin) * (right - left);
    ctx.setLineDash([4, 4]); ctx.strokeStyle = "#78909c"; ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke(); ctx.setLineDash([]);
  }
  [[wave.voltage_v, "#2f73ff", "线圈电压"], [wave.current_a, "#c98435", "线圈电流"]].forEach(([values, color, label], index) => {
    const peak = Math.max(...values.map(Math.abs), 1e-12);
    ctx.strokeStyle = color; ctx.lineWidth = 1.4; ctx.beginPath();
    values.forEach((value, i) => {
      const x = left + (t[i] - tMin) / (tMax - tMin) * (right - left);
      const y = bottom - (value / peak + 1) / 2 * (bottom - top);
      if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    });
    ctx.stroke(); ctx.fillStyle = color; ctx.font = "10px sans-serif"; ctx.fillText(`${label} / ${peak.toPrecision(4)} peak`, left + index * 180, 15);
  });
  ctx.fillStyle = "#78909c"; ctx.textAlign = "right"; ctx.fillText("时间 / μs", right, 15); ctx.textAlign = "left";
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
  if (!latestLcr?.summary_rows?.length || document.body.dataset.page !== "lcr" || document.body.dataset.lcrMode !== "small") return;
  const rows = [...latestLcr.summary_rows].sort((a, b) => Number(a.frequency_hz) - Number(b.frequency_hz));
  const units = lcrUnitsForRows(rows);
  $("lcrImpedanceUnitLabel").textContent = units.resistance.label;
  $("lcrParallelResistanceUnitLabel").textContent = units.resistance.label;
  $("lcrCapacitanceUnitLabel").textContent = units.capacitance.label;
  $("lcrInductanceUnitLabel").textContent = units.inductance.label;
  drawLcrFrequencyChart("lcrImpedanceCanvas", rows, [
    { key: "impedance_magnitude_ohm", label: "|Z|", color: "#2f73ff" },
    { key: "impedance_real_ohm", label: "R", color: "#c98435" },
    { key: "impedance_imag_ohm", label: "X", color: "#7eb6e8" },
  ], units.resistance, "阻抗");
  drawLcrFrequencyChart("lcrParallelResistanceCanvas", rows, [
    { key: "parallel_resistance_ohm", label: "Rp", color: "#59a9ff" },
  ], units.resistance, "并联电阻");
  drawLcrFrequencyChart("lcrCapacitanceCanvas", rows, [
    { key: "series_capacitance_f", label: "Cs", color: "#2f73ff" },
    { key: "parallel_capacitance_f", label: "Cp", color: "#c98435" },
  ], units.capacitance, "电容");
  drawLcrFrequencyChart("lcrInductanceCanvas", rows, [
    { key: "series_inductance_h", label: "Ls", color: "#59a9ff" },
    { key: "parallel_inductance_h", label: "Lp", color: "#7eb6e8" },
  ], units.inductance, "电感");
  drawLcrFrequencyChart("lcrFactorCanvas", rows, [
    { key: "quality_factor", label: "Q", color: "#2f73ff" },
    { key: "dissipation_factor", label: "D", color: "#c98435" },
  ], { scale: 1, label: "" }, "Q / D");
  drawLcrFrequencyChart("lcrSnrCanvas", rows, [
    { key: "voltage_snr_db", label: "电压 SNR", color: "#59a9ff" },
    { key: "current_snr_db", label: "电流 SNR", color: "#c98435" },
  ], { scale: 1, label: "dB" }, "SNR");
}

function drawLcrBode() {
  if (!latestLcr?.summary_rows?.length || document.body.dataset.page !== "lcr" || document.body.dataset.lcrMode !== "small") return;
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
  ctx.fillStyle = "#7eb6e8"; ctx.font = "10px Consolas"; ctx.textAlign = "left";
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
  plot(logZ, zMin, zMax, "#2f73ff");
  plot(phases, pMin, pMax, "#7eb6e8");
  ctx.fillStyle = "#2f73ff"; ctx.fillText("|Z|", left + 6, top + 12);
  ctx.fillStyle = "#7eb6e8"; ctx.fillText("相位", left + 40, top + 12);
  ctx.fillStyle = "#708790"; ctx.textAlign = "right";
  ctx.fillText(`频率 / ${units.frequency.label}`, right, height - 7);
  ctx.textAlign = "left"; ctx.fillText(`|Z| / ${units.resistance.label}`, 6, top + 26);
  ctx.textAlign = "left";
}

function drawLcrNyquist() {
  if (!latestLcr?.summary_rows?.length || document.body.dataset.page !== "lcr" || document.body.dataset.lcrMode !== "small") return;
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
  ctx.strokeStyle = "#c98435"; ctx.lineWidth = 1.7; ctx.beginPath();
  rows.forEach((row, index) => {
    const x = left + (real[index] - xMin) / (xMax - xMin) * (right - left);
    const y = bottom - (imag[index] - yMin) / (yMax - yMin) * (bottom - top);
    if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
  rows.forEach((row, index) => {
    const x = left + (real[index] - xMin) / (xMax - xMin) * (right - left);
    const y = bottom - (imag[index] - yMin) / (yMax - yMin) * (bottom - top);
    ctx.fillStyle = "#2f73ff"; ctx.beginPath(); ctx.arc(x, y, 3.2, 0, Math.PI * 2); ctx.fill();
  });
  ctx.fillStyle = "#708790"; ctx.font = "10px Consolas";
  ctx.fillText(`R / ${units.resistance.label}`, right - 55, height - 8); ctx.fillText(`X / ${units.resistance.label}`, 8, top + 10);
}

function drawLcrWave() {
  if (!latestLcrWave?.time_s?.length || document.body.dataset.page !== "lcr" || document.body.dataset.lcrMode !== "small") return;
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
    ctx.fillStyle = "rgba(47,115,255,.08)"; ctx.fillRect(x1, 0, x2 - x1, height);
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
let analysisLens = "raw";
let analysisRequestId = 0;
let analysisTimeFrequencyRequestId = 0;
let analysisExperimentalRequestId = 0;
let analysisRefreshTimer = null;
let spectrumWindowCounter = 0;
const hiddenWaveSeries = new Set();
const hiddenSpectrumSeries = new Set();
const spectrumWindowColors = ["#2f73ff", "#78a9ff", "#a8c7ed", "#567dbb", "#bed5f0", "#849bb7", "#c98435", "#d8b783"];
const analysisChannelColors = ["#4b88ff", "#84b4ff", "#9fc0df", "#c98435", "#678fc7", "#c4d2e3", "#718eae", "#d8b783"];

function storedAnalysisTheme() {
  try {
    const saved = localStorage.getItem("waveguard-ui-theme") || localStorage.getItem("waveguard-analysis-theme");
    return saved === "dark" ? "dark" : "light";
  } catch (_) {
    return "light";
  }
}

function applyAnalysisTheme(theme, persist = false) {
  const selected = theme === "dark" ? "dark" : "light";
  document.body.dataset.analysisTheme = selected;
  document.querySelectorAll("[data-analysis-theme-choice]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.analysisThemeChoice === selected));
  });
  if (persist) {
    try {
      localStorage.setItem("waveguard-ui-theme", selected);
      localStorage.setItem("waveguard-analysis-theme", selected);
    } catch (_) { /* Browser storage is optional. */ }
  }
  requestAnimationFrame(() => {
    const page = document.body.dataset.page;
    if (page === "measure") drawScope();
    if (page === "sweep") { drawSweepResult(); drawSweepRun(); }
    if (page === "file-analysis") {
      drawAnalysisWaveform(); drawSpectrum(); drawTimeFrequency(); drawExperimentalModes();
    }
    if (page === "sweep-analysis") { drawArchiveHeatmap(); drawArchiveRun(); }
    if (page === "lcr") {
      drawLcrParameterCharts(); drawLcrBode(); drawLcrNyquist(); drawLcrWave();
      drawBigLcrCharts(); drawBigLcrWaves();
    }
    if (page === "lcr-linearity" && latestLinearity) updateLinearityView();
  });
}

function setAnalysisViewState(state) {
  document.body.dataset.analysisState = state;
  const labels = {
    empty: "待载入 · EMPTY",
    loading: "读取中 · LOADING",
    pending: "设置待应用 · PENDING",
    processing: "分析中 · PROCESSING",
    ready: "数据已载入 · READY",
    error: "结果未更新 · ERROR",
  };
  const status = $("analysisViewState");
  if (status) status.textContent = labels[state] || labels.empty;
}

function syncAnalysisTimeRange(source = "time", pending = false) {
  const sourcePrefix = source === "time-frequency" ? "timeFrequency" : "analysisTime";
  const targetPrefix = sourcePrefix === "analysisTime" ? "timeFrequency" : "analysisTime";
  const start = $(`${sourcePrefix}Start`)?.value;
  const end = $(`${sourcePrefix}End`)?.value;
  if (start !== undefined && end !== undefined) {
    $(`${targetPrefix}Start`).value = start;
    $(`${targetPrefix}End`).value = end;
  }
  syncAnalysisLensContext();
  if (pending && analysisSource) setAnalysisViewState("pending");
}

function syncAnalysisLensContext(result = analysisResult) {
  const metadata = result?.metadata || analysisResult?.metadata;
  const file = $("analysisLensFile");
  const channels = $("analysisLensChannels");
  const rate = $("analysisLensRate");
  const range = $("analysisLensRange");
  if (!file || !channels || !rate || !range) return;
  const path = metadata?.source || analysisSource;
  const parts = path ? path.split(/[\\/]/) : [];
  file.textContent = parts.at(-1) || "未加载";
  file.title = path || "";
  const timeFrequencyLens = ["stft", "wpd", "cwt"].includes(analysisLens);
  const experimentalLens = analysisLens === "experimental";
  const selected = experimentalLens
    ? `${$("modeReferenceChannel")?.value || "—"} / ${$("modeComparisonChannel")?.value || "—"}`
    : timeFrequencyLens
      ? $("timeFrequencyChannel")?.value || "—"
      : analysisSelectedChannels.join(" / ") || metadata?.channels?.join(" / ") || "—";
  channels.textContent = selected;
  $("analysisLensChannelLabel").textContent = experimentalLens ? "CHANNEL PAIR" : timeFrequencyLens ? "CHANNEL" : "CHANNELS";
  rate.textContent = metadata?.sample_rate_hz
    ? `${(metadata.sample_rate_hz / 1e6).toFixed(4)} MS/s`
    : "—";
  const startId = experimentalLens ? "modeSearchStart" : timeFrequencyLens ? "timeFrequencyStart" : "analysisTimeStart";
  const endId = experimentalLens ? "modeSearchEnd" : timeFrequencyLens ? "timeFrequencyEnd" : "analysisTimeEnd";
  const start = $(startId)?.value;
  const end = $(endId)?.value;
  $("analysisLensRangeLabel").textContent = experimentalLens ? "SEARCH WINDOW" : "TIME RANGE";
  range.textContent = start && end ? `${start} — ${end} μs` : "—";
}

function clearAnalysisLensOutputs() {
  ["analysisWaveCanvas", "spectrumCanvas", "timeFrequencyCanvas", "modeWaveCanvas", "modeSymmetryCanvas", "modeMatchCanvas"]
    .forEach((id) => {
      const canvas = $(id);
      if (!canvas) return;
      canvas.getContext("2d")?.clearRect(0, 0, canvas.width, canvas.height);
    });
  $("analysisWaveLegend").replaceChildren();
  $("spectrumLegend").replaceChildren();
  $("analysisWaveEmpty").style.display = "grid";
  $("spectrumEmpty").style.display = "grid";
  $("timeFrequencyEmpty").style.display = "grid";
  $("modeWaveEmpty").style.display = "grid";
  $("modeMetricCards").replaceChildren();
  $("modeCandidatesBody").replaceChildren();
  $("analysisCursorTime").textContent = "t = —";
  $("analysisCursorValue").textContent = "V = —";
  $("timeFrequencyTitle").textContent = `${$("timeFrequencyMethod").value.toUpperCase()}时频图`;
  $("timeFrequencyMeta").textContent = "尚未计算当前文件";
  $("modeResultTitle").textContent = "双通道空间—时间对称性与端面匹配";
  $("modeAnalysisMeta").textContent = "不参与正式评分 · 尚未运行";
}

function syncAnalysisLensChrome(lens) {
  const waveCopy = {
    raw: ["TIME DOMAIN / RAW", "原始波形"],
    filtered: ["TIME DOMAIN / FILTERED", "带通波形"],
    mix: ["TIME DOMAIN / MIX", "原始与带通对照"],
  };
  const copy = waveCopy[lens];
  if (copy) {
    $("analysisWaveEyebrow").textContent = copy[0];
    $("analysisWaveTitle").textContent = copy[1];
  }
  setAnalysisSettingsOpen(false);
}

function setAnalysisSettingsOpen(open) {
  const settings = $("analysisLensSettings");
  const button = $("analysisLensSettingsButton");
  const popover = $("analysisLensSettingsPopover");
  if (!settings || !button || !popover) return;
  const expanded = Boolean(open);
  popover.hidden = !expanded;
  button.setAttribute("aria-expanded", String(expanded));
  settings.classList.toggle("is-open", expanded);
}

function setAnalysisLens(lens, refresh = true) {
  const allowed = new Set(["raw", "filtered", "mix", "fft", "stft", "wpd", "cwt", "experimental"]);
  if (!allowed.has(lens)) return;
  const previous = analysisLens;
  const timeFrequencyLenses = ["stft", "wpd", "cwt"];
  const previousRangeSource = timeFrequencyLenses.includes(previous) ? "time-frequency" : "time";
  const nextRangeSource = timeFrequencyLenses.includes(lens) ? "time-frequency" : "time";
  if (previousRangeSource !== nextRangeSource) syncAnalysisTimeRange(previousRangeSource);
  analysisLens = lens;
  document.body.dataset.analysisLens = lens;
  syncAnalysisLensChrome(lens);
  document.querySelectorAll(".analysis-lenses [data-analysis-lens]").forEach((button) => {
    button.setAttribute("aria-selected", String(button.dataset.analysisLens === lens));
  });
  syncAnalysisLensContext();
  requestAnimationFrame(() => {
    if (["raw", "filtered", "mix"].includes(lens)) drawAnalysisWaveform();
    if (lens === "fft") drawSpectrum();
    if (["stft", "wpd", "cwt"].includes(lens) && timeFrequencyResult?.method === lens) drawTimeFrequency();
    if (lens === "experimental" && experimentalModeResult) drawExperimentalModes();
  });

  if (["raw", "filtered", "mix"].includes(lens)) {
    const showRaw = lens !== "filtered";
    const showFiltered = lens !== "raw";
    const changed = $("analysisShowRaw").checked !== showRaw
      || $("analysisShowFiltered").checked !== showFiltered
      || (showFiltered && !$("analysisFilterEnabled").checked);
    $("analysisShowRaw").checked = showRaw;
    $("analysisShowFiltered").checked = showFiltered;
    if (showFiltered) $("analysisFilterEnabled").checked = true;
    if (refresh && analysisSource && changed) scheduleAnalysisRefresh();
    return;
  }
  if (["stft", "wpd", "cwt"].includes(lens)) {
    $("timeFrequencyMethod").value = lens;
    updateTimeFrequencyControls();
    syncAnalysisLensContext();
    if (timeFrequencyResult?.method !== lens) {
      timeFrequencyResult = null;
      const canvas = $("timeFrequencyCanvas");
      canvas.getContext("2d")?.clearRect(0, 0, canvas.width, canvas.height);
      $("timeFrequencyEmpty").style.display = "grid";
      $("timeFrequencyEmpty").textContent = analysisSource ? `${lens.toUpperCase()} 正在计算…` : "请先载入波形数据";
      $("timeFrequencyTitle").textContent = `${lens.toUpperCase()}时频图 · CH ${$("timeFrequencyChannel").value}`;
      $("timeFrequencyMeta").textContent = analysisSource ? "正在使用当前参数计算…" : "等待数据";
      if (refresh && analysisSource) void calculateTimeFrequency();
    } else {
      requestAnimationFrame(drawTimeFrequency);
    }
    return;
  }
  if (lens === "experimental" && !experimentalModeResult) {
    $("modeWaveEmpty").style.display = "grid";
    $("modeWaveEmpty").textContent = "选择实验参数后运行分析";
    $("modeAnalysisMeta").textContent = "TEST · 等待显式运行";
  }
}

function currentPage() {
  const part = location.pathname.replace(/^\/+|\/+$/g, "");
  if (part === "analysis") return "file-analysis";
  return ["measure", "sweep", "lcr", "lcr-linearity", "file-analysis", "sweep-analysis"].includes(part)
    ? part
    : "measure";
}

let lcrModeRecords = null;

function ensureLcrModeRecords() {
  if (lcrModeRecords) return;
  lcrModeRecords = [...document.querySelectorAll("[data-lcr-mode]")].map((node) => {
    const placeholder = document.createComment(`lcr-${node.dataset.lcrMode}`);
    node.parentNode.insertBefore(placeholder, node);
    node.remove();
    return { node, placeholder, mode: node.dataset.lcrMode };
  });
}

function mountLcrMode(mode) {
  ensureLcrModeRecords();
  const selected = mode === "small" ? "small" : "big";
  lcrModeRecords.forEach(({ node, placeholder, mode: nodeMode }) => {
    if (nodeMode === selected) {
      if (node.parentNode !== placeholder.parentNode) {
        placeholder.parentNode.insertBefore(node, placeholder.nextSibling);
      }
    } else if (node.parentNode) {
      node.remove();
    }
  });
  document.body.dataset.lcrMode = selected;
  bindLcrModeListeners();
  if (selected === "small") updateLcrControls();
  else { updateBigLcrControls(); updateBigSafetyStatus(); }
}

async function handleLcrModeChange() {
  const requested = $("lcrMeasurementMode").value === "small" ? "small" : "big";
  const previous = document.body.dataset.lcrMode || "big";
  try {
    const status = await api("/api/status");
    if (["running", "paused"].includes(status.state) && ["lcr", "lcr_calibration", "big_lcr", "lcr_linearity"].includes(status.task_kind)) {
      $("lcrMeasurementMode").value = previous;
      setEvent(status.state === "paused" ? "疑似跳闸处置期间不能切换 LCR 模式。" : "当前 LCR 任务正在运行，完成或停止后才能切换模式。", status.state);
      return;
    }
  } catch (_) {
    // Local mode switching remains available if the service is disconnected.
  }
  mountLcrMode(requested);
  if (requested === "small") {
    refreshLcrCalibrations();
    drawLcrParameterCharts(); drawLcrBode(); drawLcrNyquist(); drawLcrWave();
  } else {
    drawBigLcrCharts(); drawBigLcrWaves();
  }
}

function onElement(id, eventName, handler) {
  const element = $(id);
  if (!element) return;
  if (!element.__boundEvents) element.__boundEvents = new Set();
  if (element.__boundEvents.has(eventName)) return;
  element.addEventListener(eventName, handler);
  element.__boundEvents.add(eventName);
}

function bindLcrModeListeners() {
  onElement("lcrMeasurementMode", "change", handleLcrModeChange);
  onElement("bigLcrStartBtn", "click", startBigLcr);
  onElement("bigLcrStopBtn", "click", stopCapture);
  onElement("linearityStartBtn", "click", startLinearityTest);
  onElement("linearityStopBtn", "click", stopCapture);
  onElement("tripContinueBtn", "click", continueAfterTrip);
  onElement("tripStopBtn", "click", stopCapture);
  onElement("tripMuteBtn", "click", () => {
    tripAlarmMuted = !tripAlarmMuted;
    if (tripAlarmMuted) stopTripAlarmSound();
    else startTripAlarmSound();
    const button = $("tripMuteBtn");
    if (button) {
      button.textContent = tripAlarmMuted ? "恢复声音" : "停止声音";
      button.setAttribute("aria-pressed", String(tripAlarmMuted));
    }
  });
  onElement("bigLcrScanMode", "change", updateBigLcrControls);
  onElement("bigLcrMode", "change", updateBigLcrControls);
  [
    "bigLcrFrequency", "bigLcrFrequencyStart", "bigLcrFrequencyStop", "bigLcrFrequencyStep",
    "bigLcrPointsPerDecade", "bigLcrVppStart", "bigLcrVppStop", "bigLcrVppStep",
    "bigLcrRepeats", "bigLcrAwgVpp", "bigLcrBurstCycles",
    "bigLcrRampCycles", "bigLcrAnalysisCycles", "bigLcrGuardCycles", "bigLcrMaxDrive",
    "bigLcrMaxFrequency", "bigLcrAlertVoltageVpp", "bigLcrAlertCurrentPeak", "bigLcrAtaGain", "bigLcrAutoRange",
    "bigLcrTripDrop", "bigLcrTripHold", "bigLcrMinMonitorRms", "bigLcrSafetyAck",
  ].forEach((id) => onElement(id, "input", updateBigLcrControls));
  onElement("bigLcrSafetyAck", "change", updateBigSafetyStatus);
  onElement("lcrStartBtn", "click", startLcr);
  onElement("lcrCalibrateBtn", "click", startLcrCalibration);
  onElement("lcrStopBtn", "click", stopCapture);
  onElement("lcrMode", "change", updateLcrControls);
  onElement("lcrCalibrationFile", "change", () => {
    const selected = $("lcrCalibrationFile").selectedOptions[0];
    $("lcrCalibrationStatus").textContent = $("lcrCalibrationFile").value
      ? `将使用：${selected.textContent}` : "当前测量不使用精准电阻校准";
  });
  ["lcrFrequencyAxisScale", "lcrFrequencyUnit", "lcrResistanceUnit", "lcrCapacitanceUnit", "lcrInductanceUnit"].forEach((id) =>
    onElement(id, "change", () => { if (latestLcr) updateLcrResult(); }));
  [
    "lcrFrequency", "lcrFrequencyStart", "lcrFrequencyStop", "lcrFrequencyStep",
    "lcrPointsPerDecade", "lcrRepeats", "lcrBurstCycles", "lcrRampCycles",
    "lcrAnalysisCycles", "lcrGuardCycles",
  ].forEach((id) => onElement(id, "input", updateLcrControls));
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
  const workspaceCopy = {
    measure: ["LIVE ACQUISITION", "实时测量", "PicoScope 4824A · 8CH BLOCK CAPTURE", "系统就绪 · READY"],
    sweep: ["PARAMETER EXPLORATION", "参数扫描", "AWG → TRIGGER → 8CH CAPTURE → EVALUATION", "扫描待命 · READY"],
    lcr: ["IMPEDANCE WORKSPACE", "LCR 测量", "SMALL SIGNAL / HIGH DRIVE · CALIBRATED", "测量待命 · READY"],
    "lcr-linearity": ["ENGINEERING ANALYSIS", "LCR 数据分析", "SAVED DATA · LINEARITY · HARMONICS", "离线分析 · READY"],
    "file-analysis": ["ANALYSIS WORKSPACE", "单数据分析", "PicoScope 4824A · OFFLINE ANALYSIS", "待载入 · EMPTY"],
    "sweep-analysis": ["SWEEP ARCHIVE", "参数数据分析", "SAVED SWEEP · RE-EVALUATION · RUN PREVIEW", "等待归档 · READY"],
  };
  const copy = workspaceCopy[page] || workspaceCopy.measure;
  document.body.dataset.page = page;
  document.title = `WaveGuard · ${copy[1]}`;
  $("productBrandEyebrow").textContent = "SCIENTIFIC SIGNAL WORKSTATION";
  $("productBrandTitle").textContent = "WaveGuard";
  $("workspaceEyebrow").textContent = copy[0];
  $("workspaceTitle").textContent = copy[1];
  $("workspaceInstrument").textContent = copy[2];
  if (page !== "file-analysis") $("analysisViewState").textContent = copy[3];
  document.querySelectorAll("[data-nav]").forEach((link) => {
    link.classList.toggle("active", link.dataset.nav === page);
    if (link.dataset.nav === page) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  if (push && location.pathname !== `/${page}`) history.pushState({ page }, "", `/${page}`);
  if (page === "sweep") {
    $("sweepIdleStage").hidden = Boolean(latestSweep);
    if (!latestSweep) {
      $("sweepIdleStage").dataset.state = "idle";
      $("sweepIdleEyebrow").textContent = "PARAMETER SWEEP · IDLE";
      $("sweepIdleTitle").textContent = "扫描数据舞台";
      $("sweepIdleDescription").textContent = "配置左侧参数网格并启动扫描。首个有效参数点完成后，这里将呈现评价结果与逐次采集波形。";
    }
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
    $("archiveIdleStage").hidden = Boolean(archiveResult);
    drawArchiveHeatmap();
    drawArchiveRun();
  } else if (page === "lcr") {
    mountLcrMode($("lcrMeasurementMode")?.value || "big");
    if (document.body.dataset.lcrMode === "small") {
      refreshLcrCalibrations();
      drawLcrParameterCharts();
      drawLcrBode();
      drawLcrNyquist();
      drawLcrWave();
    } else {
      drawBigLcrCharts();
      drawBigLcrWaves();
      updateBigLcrResult();
    }
  } else if (page === "lcr-linearity") {
    document.body.dataset.lcrAnalysisMode = $("lcrAnalysisMode")?.value || "big_lcr";
    if (latestLinearity) updateLinearityView();
    if (lcrFolderStates.big_lcr) renderLcrFolder("big_lcr");
    if (lcrFolderStates.small_lcr) renderLcrFolder("small_lcr");
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
  "bigLcrSimulate", "bigLcrSampleRate", "bigLcrTriggerLevel", "bigLcrVoltageChannel",
  "bigLcrCurrentChannel", "bigLcrVoltageRange", "bigLcrCurrentRange", "bigLcrTriggerSignal",
  "bigLcrScanMode", "bigLcrMode", "bigLcrFrequency", "bigLcrFrequencyStart", "bigLcrFrequencyStop",
  "bigLcrFrequencyStep", "bigLcrPointsPerDecade", "bigLcrAwgVpp", "bigLcrVppStart", "bigLcrVppStop",
  "bigLcrVppStep", "bigLcrBurstCycles",
  "bigLcrRampCycles", "bigLcrAnalysisCycles", "bigLcrGuardCycles", "bigLcrRepeats",
  "bigLcrInterval", "bigLcrVoltageScale", "bigLcrCurrentScale", "bigLcrVoltageOffset",
  "bigLcrCurrentPolarity", "bigLcrCurrentOffset", "bigLcrMaxDrive", "bigLcrAlertVoltageVpp", "bigLcrAlertCurrentPeak",
  "bigLcrMaxFrequency", "bigLcrSafetyAck", "bigLcrAtaGain", "bigLcrAutoRange",
  "bigLcrTripDetection", "bigLcrTripDrop", "bigLcrTripHold", "bigLcrMinMonitorRms",
  "linearityDirectory", "linearityHarmonicOrder", "linearityAlignShift", "linearityReferenceCount", "linearityReferenceMaxCurrent",
  "linearityAnalysisNoiseStart", "linearityAnalysisNoiseEnd", "linearityAnalysisDirectStart", "linearityAnalysisDirectEnd",
  "linearityAnalysisEchoStart", "linearityAnalysisEchoEnd", "linearityForceOrigin", "linearityLimitDwave", "linearityLimitRxThd",
  "linearityLimitIThd", "linearityLimitCompression", "linearityLimitKme", "linearityLimitCorrelation", "lcrAnalysisMode",
  "bigFolderDirectory", "smallFolderDirectory", "linearityReceiverChannel", "linearityReceiverRange", "linearityFrequencyKHz",
  "linearitySampleRate", "linearityCaptureDuration", "linearityTriggerPosition", "linearityTriggerSignal", "linearityTriggerLevel",
  "linearityCycles", "linearityRampCycles", "linearityVppStart", "linearityVppStop", "linearityVppStep", "linearityDirection",
  "linearityRepeats", "linearityInterval", "linearityNoiseStart", "linearityNoiseEnd", "linearityDirectStart", "linearityDirectEnd",
  "linearityEchoStart", "linearityEchoEnd", "linearityMeasureLimitDwave", "linearityMeasureLimitRxThd",
  "linearityMeasureLimitIThd", "linearityMeasureLimitCompression", "linearityMeasureLimitKme", "linearityMeasureLimitCorrelation",
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
  analysisFullRangeUs = null;
  analysisResult = null;
  timeFrequencyResult = null;
  experimentalModeResult = null;
  analysisSelectedChannels = [];
  analysisTimeFrequencyRequestId += 1;
  analysisExperimentalRequestId += 1;
  clearAnalysisLensOutputs();
  setAnalysisViewState("loading");
  syncAnalysisLensContext(null);
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
    setAnalysisViewState("ready");
    syncAnalysisLensContext(result);
    setEvent(`已加载：${metadata.source}`, "complete");
    await refreshAnalysis();
    if (["stft", "wpd", "cwt"].includes(analysisLens)) await calculateTimeFrequency();
  } catch (error) {
    setAnalysisViewState("error");
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
  setAnalysisViewState("processing");
  try {
    const result = await api("/api/analysis/process", {
      method: "POST",
      body: JSON.stringify(buildAnalysisPayload(false)),
    });
    if (requestId !== analysisRequestId) return;
    analysisResult = result;
    updateAnalysisResult(result);
    setAnalysisViewState("ready");
    syncAnalysisLensContext(result);
  } catch (error) {
    if (requestId === analysisRequestId) {
      setAnalysisViewState("error");
      setEvent(error.message, "error");
    }
  }
}

function waveSeriesColor(key) {
  const [channel, kind] = key.split(":");
  const base = analysisChannelColors[Math.max(0, channelNames.indexOf(channel))];
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
  syncAnalysisLensContext(result);
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

function drawAxes(ctx, width, height, left, top, right, bottom, xLabels, yLabels, minXTickSpacing = 0) {
  const analysisPage = document.body.dataset.page === "file-analysis";
  ctx.strokeStyle = analysisPage ? "rgba(150,170,195,.2)" : "rgba(120,144,156,.19)";
  ctx.lineWidth = 1;
  ctx.font = "10px Consolas";
  const xIntervals = minXTickSpacing > 0
    ? Math.max(2, Math.min(5, Math.floor((right - left) / minXTickSpacing)))
    : 5;
  for (let index = 0; index <= xIntervals; index += 1) {
    const x = left + (right - left) * index / xIntervals;
    ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke();
    ctx.fillStyle = analysisPage ? "#94a4b8" : "#69808a"; ctx.textAlign = "center";
    ctx.fillText(xLabels(index / xIntervals), x, height - 9);
  }
  for (let index = 0; index <= 4; index += 1) {
    const y = top + (bottom - top) * index / 4;
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
    ctx.fillStyle = analysisPage ? "#94a4b8" : "#69808a"; ctx.textAlign = "right";
    ctx.fillText(yLabels(1 - index / 4), left - 7, y + 4);
  }
  ctx.textAlign = "left";
}

function drawBigFrequencyChart(canvasId, rows, series, yUnit, emptyLabel) {
  const canvas = $(canvasId);
  if (!canvas) return;
  const { ctx, width, height } = canvasSetup(canvas);
  ctx.clearRect(0, 0, width, height);
  const values = rows.flatMap((row) => series.map((item) => lcrNumeric(row[item.key]) / yUnit.scale)).filter(Number.isFinite);
  if (!values.length) {
    ctx.fillStyle = "#607781"; ctx.font = "11px sans-serif"; ctx.textAlign = "center";
    ctx.fillText(`当前频段没有有效${emptyLabel}数据`, width / 2, height / 2); ctx.textAlign = "left"; return;
  }
  const frequencies = rows.map((row) => Math.max(Number(row.frequency_hz), 1));
  const xValues = frequencies.map(Math.log10);
  let xMin = Math.min(...xValues), xMax = Math.max(...xValues);
  let yMin = Math.min(...values), yMax = Math.max(...values);
  if (xMax <= xMin) { xMin -= .05; xMax += .05; }
  if (yMax <= yMin) { const margin = Math.max(Math.abs(yMin) * .08, 1e-12); yMin -= margin; yMax += margin; }
  else { const margin = (yMax - yMin) * .08; yMin -= margin; yMax += margin; }
  const left = 74, right = width - 18, top = 34, bottom = height - 38;
  drawAxes(ctx, width, height, left, top, right, bottom,
    (ratio) => lcrAxisNumber(10 ** (xMin + (xMax - xMin) * ratio) / 1000),
    (ratio) => lcrAxisNumber(yMin + (yMax - yMin) * ratio), 70);
  series.forEach((item, seriesIndex) => {
    ctx.strokeStyle = item.color; ctx.lineWidth = 1.7; ctx.beginPath();
    let connected = false;
    rows.forEach((row, index) => {
      const value = lcrNumeric(row[item.key]);
      if (!Number.isFinite(value)) { connected = false; return; }
      const x = left + (xValues[index] - xMin) / (xMax - xMin) * (right - left);
      const y = bottom - (value / yUnit.scale - yMin) / (yMax - yMin) * (bottom - top);
      if (!connected) ctx.moveTo(x, y); else ctx.lineTo(x, y); connected = true;
    });
    ctx.stroke();
    if (rows.length <= 40) {
      ctx.fillStyle = item.color;
      rows.forEach((row, index) => {
        const value = lcrNumeric(row[item.key]);
        if (!Number.isFinite(value)) return;
        const x = left + (xValues[index] - xMin) / (xMax - xMin) * (right - left);
        const y = bottom - (value / yUnit.scale - yMin) / (yMax - yMin) * (bottom - top);
        ctx.beginPath(); ctx.arc(x, y, 2.2, 0, Math.PI * 2); ctx.fill();
      });
    }
    ctx.fillStyle = item.color; ctx.font = "10px Consolas"; ctx.fillText(item.label, left + seriesIndex * 58, 17);
  });
  ctx.fillStyle = "#708790"; ctx.font = "10px Consolas"; ctx.textAlign = "right";
  ctx.fillText("频率 / kHz（对数）", right, 17); ctx.textAlign = "left";
  ctx.fillText(yUnit.label || "", 8, top - 8);
}

function drawBigComponentChart(rows) {
  const canvas = $("bigLcrComponentCanvas");
  if (!canvas) return;
  const { ctx, width, height } = canvasSetup(canvas);
  ctx.clearRect(0, 0, width, height);
  const lRaw = rows.map((row) => Number(row.effective_inductance_h));
  const qRaw = rows.map((row) => Number(row.quality_factor));
  const finiteL = lRaw.filter(Number.isFinite);
  const finiteQ = qRaw.filter(Number.isFinite);
  if (!finiteL.length && !finiteQ.length) {
    ctx.fillStyle = "#607781"; ctx.font = "11px sans-serif"; ctx.textAlign = "center";
    ctx.fillText("当前频段没有有效 Leff / Q 数据", width / 2, height / 2); ctx.textAlign = "left"; return;
  }
  const lScale = finiteL.length && Math.max(...finiteL.map(Math.abs)) >= 1e-3 ? 1e-3 : 1e-6;
  const lLabel = lScale === 1e-3 ? "mH" : "μH";
  const lValues = lRaw.map((value) => value / lScale);
  const qValues = qRaw;
  const range = (values, fallback) => {
    const finite = values.filter(Number.isFinite);
    if (!finite.length) return [fallback - 1, fallback + 1];
    let min = Math.min(...finite), max = Math.max(...finite);
    if (max <= min) { const margin = Math.max(Math.abs(min) * .08, 1e-9); min -= margin; max += margin; }
    else { const margin = (max - min) * .08; min -= margin; max += margin; }
    return [min, max];
  };
  const [lMin, lMax] = range(lValues, 0), [qMin, qMax] = range(qValues, 1);
  const xValues = rows.map((row) => Math.log10(Math.max(Number(row.frequency_hz), 1)));
  let xMin = Math.min(...xValues), xMax = Math.max(...xValues);
  if (xMax <= xMin) { xMin -= .05; xMax += .05; }
  const left = 68, right = width - 54, top = 34, bottom = height - 38;
  drawAxes(ctx, width, height, left, top, right, bottom,
    (ratio) => lcrAxisNumber(10 ** (xMin + (xMax - xMin) * ratio) / 1000),
    (ratio) => lcrAxisNumber(lMin + (lMax - lMin) * ratio), 70);
  for (let index = 0; index <= 4; index += 1) {
    const y = bottom - (bottom - top) * index / 4;
    ctx.fillStyle = "#c98435"; ctx.textAlign = "left";
    ctx.fillText(lcrAxisNumber(qMin + (qMax - qMin) * index / 4), right + 7, y + 4);
  }
  const plot = (values, min, max, color) => {
    ctx.strokeStyle = color; ctx.lineWidth = 1.7; ctx.beginPath(); let connected = false;
    values.forEach((value, index) => {
      if (!Number.isFinite(value)) { connected = false; return; }
      const x = left + (xValues[index] - xMin) / (xMax - xMin) * (right - left);
      const y = bottom - (value - min) / (max - min) * (bottom - top);
      if (!connected) ctx.moveTo(x, y); else ctx.lineTo(x, y); connected = true;
    }); ctx.stroke();
    if (rows.length <= 40) {
      ctx.fillStyle = color;
      values.forEach((value, index) => {
        if (!Number.isFinite(value)) return;
        const x = left + (xValues[index] - xMin) / (xMax - xMin) * (right - left);
        const y = bottom - (value - min) / (max - min) * (bottom - top);
        ctx.beginPath(); ctx.arc(x, y, 2.2, 0, Math.PI * 2); ctx.fill();
      });
    }
  };
  plot(lValues, lMin, lMax, "#59a9ff"); plot(qValues, qMin, qMax, "#c98435");
  ctx.font = "10px Consolas"; ctx.fillStyle = "#59a9ff"; ctx.textAlign = "left"; ctx.fillText(`Leff / ${lLabel}`, left + 5, 17);
  ctx.fillStyle = "#c98435"; ctx.fillText("Q", left + 70, 17); ctx.fillStyle = "#708790"; ctx.textAlign = "right"; ctx.fillText("频率 / kHz（对数）", right, 17); ctx.textAlign = "left";
}

function drawBigLcrGridChart(rows) {
  const canvas = $("bigLcrGridCanvas");
  if (!canvas) return;
  const { ctx, width, height } = canvasSetup(canvas);
  ctx.clearRect(0, 0, width, height);
  const data = rows.map((row) => ({
    frequency: Number(row.frequency_hz),
    vpp: Number(row.awg_drive_vpp),
    impedance: Number(row.impedance_magnitude_ohm),
  })).filter((row) => row.frequency > 0 && row.vpp > 0 && Number.isFinite(row.impedance));
  if (!data.length) {
    ctx.fillStyle = "#607781"; ctx.font = "11px sans-serif"; ctx.textAlign = "center";
    ctx.fillText("没有可绘制的频率 × Vpp 阻抗数据", width / 2, height / 2); ctx.textAlign = "left"; return;
  }
  const frequencies = [...new Set(data.map((row) => row.frequency))].sort((a, b) => a - b);
  const vpps = [...new Set(data.map((row) => row.vpp))].sort((a, b) => a - b);
  const xCenters = frequencies.map((value) => Math.log10(value));
  const edges = (values, halfWidth) => values.length === 1
    ? [values[0] - halfWidth, values[0] + halfWidth]
    : [values[0] - (values[1] - values[0]) / 2,
      ...values.slice(1).map((value, index) => (values[index] + value) / 2),
      values.at(-1) + (values.at(-1) - values.at(-2)) / 2];
  const xEdges = edges(xCenters, 0.12);
  const yEdges = edges(vpps, Math.max(vpps[0] * 0.08, 0.05));
  yEdges[0] = Math.max(0, yEdges[0]);
  const xMin = xEdges[0], xMax = xEdges.at(-1);
  const yMin = yEdges[0], yMax = yEdges.at(-1);
  const zValues = data.map((row) => row.impedance);
  const zMin = Math.min(...zValues), zMax = Math.max(...zValues);
  const left = 76, right = width - 64, top = 30, bottom = height - 36;
  const pxX = (x) => left + (x - xMin) / Math.max(xMax - xMin, 1e-12) * (right - left);
  const pxY = (y) => bottom - (y - yMin) / Math.max(yMax - yMin, 1e-12) * (bottom - top);
  drawAxes(ctx, width, height, left, top, right, bottom,
    (ratio) => lcrAxisNumber(10 ** (xMin + (xMax - xMin) * ratio) / 1000),
    (ratio) => lcrAxisNumber(yMin + (yMax - yMin) * ratio), 70);
  const fIndex = new Map(frequencies.map((value, index) => [value, index]));
  const vIndex = new Map(vpps.map((value, index) => [value, index]));
  data.forEach((row) => {
    const xi = fIndex.get(row.frequency), yi = vIndex.get(row.vpp);
    const x0 = pxX(xEdges[xi]), x1 = pxX(xEdges[xi + 1]);
    const y0 = pxY(yEdges[yi + 1]), y1 = pxY(yEdges[yi]);
    const ratio = zMax > zMin ? (row.impedance - zMin) / (zMax - zMin) : 0.5;
    ctx.fillStyle = signalHeatColor(ratio);
    ctx.fillRect(x0, y0, Math.max(1, x1 - x0), Math.max(1, y1 - y0));
    ctx.strokeStyle = "rgba(9,19,26,.7)"; ctx.lineWidth = 1;
    ctx.strokeRect(x0, y0, Math.max(1, x1 - x0), Math.max(1, y1 - y0));
  });
  const legend = ctx.createLinearGradient(0, bottom, 0, top);
  legend.addColorStop(0, signalHeatColor(0)); legend.addColorStop(1, signalHeatColor(1));
  ctx.fillStyle = legend; ctx.fillRect(width - 43, top + 4, 12, bottom - top - 8);
  ctx.fillStyle = "#8299a2"; ctx.font = "10px Consolas"; ctx.textAlign = "left";
  ctx.fillText(lcrAxisNumber(zMax), width - 27, top + 10);
  ctx.fillText(lcrAxisNumber(zMin), width - 27, bottom - 1);
  ctx.fillStyle = "#8ca1aa"; ctx.fillText("AWG Vpp / V", 7, top - 9);
  ctx.textAlign = "right"; ctx.fillText("频率 / kHz（对数）", right, 17); ctx.textAlign = "left";
}

function drawBigLcrCharts() {
  if (document.body.dataset.page !== "lcr" || document.body.dataset.lcrMode !== "big" || !latestBigLcr?.summary_rows?.length) return;
  const rows = [...latestBigLcr.summary_rows].sort((a, b) =>
    Number(a.frequency_hz) - Number(b.frequency_hz) || Number(a.awg_drive_vpp) - Number(b.awg_drive_vpp));
  drawBigLcrGridChart(rows);
  const highestVpp = Math.max(...rows.map((row) => Number(row.awg_drive_vpp) || 0));
  const curveRows = rows.filter((row) => Math.abs((Number(row.awg_drive_vpp) || 0) - highestVpp) < 1e-12);
  drawBigFrequencyChart("bigLcrImpedanceCanvas", curveRows, [
    { key: "impedance_magnitude_ohm", label: "|Z|", color: "#2f73ff" },
    { key: "series_resistance_ohm", label: "Rs", color: "#c98435" },
    { key: "series_reactance_ohm", label: "Xs", color: "#7eb6e8" },
  ], { scale: 1, label: "Ω" }, "阻抗");
  drawBigComponentChart(curveRows);
}

function drawBigLcrWaves() {
  if (document.body.dataset.page !== "lcr" || document.body.dataset.lcrMode !== "big" || !latestBigLcrWave?.time_s?.length) return;
  const savedRun = latestBigLcr?.run_rows?.at(-1) || {};
  const draw = (canvasId, channelId, color, label, scale, unit) => {
    const canvas = $(canvasId); if (!canvas) return;
    const savedChannel = channelId === "bigLcrVoltageChannel"
      ? savedRun.voltage_monitor_channel : savedRun.current_monitor_channel;
    const channel = savedChannel || $(channelId)?.value;
    const rawValues = latestBigLcrWave.channels[channel] || [];
    const values = rawValues.map((value) => Number(value) * scale);
    if (!values.length) return;
    const { ctx, width, height } = canvasSetup(canvas); ctx.clearRect(0, 0, width, height);
    const peak = Math.max(...values.map((value) => Math.abs(Number(value))), 1e-12);
    ctx.strokeStyle = "rgba(111,139,150,.2)"; ctx.beginPath(); ctx.moveTo(0, height / 2); ctx.lineTo(width, height / 2); ctx.stroke();
    ctx.strokeStyle = color; ctx.lineWidth = 1.2; ctx.beginPath();
    values.forEach((value, index) => { const x = index / Math.max(values.length - 1, 1) * width; const y = height / 2 - Number(value) / peak * height * .4; if (!index) ctx.moveTo(x, y); else ctx.lineTo(x, y); });
    ctx.stroke(); ctx.fillStyle = color; ctx.font = "10px Consolas"; ctx.fillText(`${label} · ±${peak.toPrecision(4)} ${unit}（换算后）`, 8, 15);
    ctx.fillStyle = "#708790"; ctx.fillText(formatTime(latestBigLcrWave.time_s[0]), 6, height - 6); ctx.textAlign = "right"; ctx.fillText(formatTime(latestBigLcrWave.time_s.at(-1)), width - 6, height - 6); ctx.textAlign = "left";
  };
  const voltageScale = Number(savedRun.voltage_monitor_scale_v_per_v ?? $("bigLcrVoltageScale")?.value ?? 1);
  const currentScale = Number(savedRun.current_monitor_scale_a_per_v ?? $("bigLcrCurrentScale")?.value ?? 1);
  const polarity = Number(savedRun.current_monitor_polarity ?? $("bigLcrCurrentPolarity")?.value ?? 1);
  draw("bigLcrVoltageWaveCanvas", "bigLcrVoltageChannel", "#2f73ff", "Voltage Monitor", voltageScale, "V");
  draw("bigLcrCurrentWaveCanvas", "bigLcrCurrentChannel", "#c98435", "Current Monitor", currentScale * polarity, "A");
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
      ctx.fillStyle = stacked ? analysisChannelColors[Math.max(0, channelNames.indexOf(channel))] : "#aab8c8";
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
  ctx.fillStyle = "#a5b4c7"; ctx.font = "10px Consolas";
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
  syncAnalysisTimeRange("time");
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
    syncAnalysisTimeRange("time");
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
      syncAnalysisTimeRange("time");
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
    $("archiveIdleStage").hidden = Boolean(result.is_sweep);
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
    $("archiveIdleStage").hidden = false;
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

function interpolateRgb(from, to, ratio) {
  const value = Math.max(0, Math.min(1, Number(ratio) || 0));
  const channels = from.map((channel, index) => Math.round(channel + (to[index] - channel) * value));
  return `rgb(${channels.join(",")})`;
}

function signalHeatColor(ratio) {
  const value = Math.max(0, Math.min(1, Number(ratio) || 0));
  if (value < 0.56) return interpolateRgb([19, 34, 53], [47, 115, 255], value / 0.56);
  return interpolateRgb([47, 115, 255], [89, 169, 255], (value - 0.56) / 0.44);
}

function archiveColor(ratio, higherIsBetter) {
  const good = Math.max(0, Math.min(1, higherIsBetter ? ratio : 1 - ratio));
  return interpolateRgb([201, 132, 53], [47, 115, 255], good);
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
      ctx.fillStyle = "rgba(239,244,250,.92)";
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
      body: JSON.stringify({
        path: sourcePath,
        channel,
        filter: analysisFilterPayload(),
        method,
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
    if (requestId !== analysisTimeFrequencyRequestId || sourcePath !== analysisSource) return;
    timeFrequencyResult = result;
    $("timeFrequencyEmpty").style.display = "none";
    $("timeFrequencyTitle").textContent = `${result.method.toUpperCase()}时频图 · CH ${channel}`;
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
    if (requestId !== analysisTimeFrequencyRequestId || sourcePath !== analysisSource) return;
    setAnalysisViewState("error");
    $("timeFrequencyEmpty").style.display = "grid";
    $("timeFrequencyEmpty").textContent = `计算失败 · ${error.message}`;
    $("timeFrequencyMeta").textContent = error.message;
    setEvent(error.message, "error");
  }
}

function tfColor(value, floor) {
  const ratio = Math.max(0, Math.min(1, (value - floor) / -floor));
  const stops = [
    [8, 15, 25], [19, 40, 72], [34, 75, 137], [47, 115, 255], [111, 164, 255], [220, 233, 255],
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
  ctx.fillStyle = "#a5b4c7"; ctx.font = "10px Consolas";
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
  const requestId = ++analysisExperimentalRequestId;
  const sourcePath = analysisSource;
  try {
    setAnalysisViewState("processing");
    $("modeAnalysisMeta").textContent = "正在计算实验指标…";
    const result = await api("/api/analysis/experimental-modes", {
      method: "POST",
      body: JSON.stringify({
        path: sourcePath,
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
    if (requestId !== analysisExperimentalRequestId || sourcePath !== analysisSource) return;
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
  [["common_v", "#4b88ff", []], ["differential_v", "#c98435", [5, 3]]].forEach(([key, color, dash]) => {
    ctx.strokeStyle = color; ctx.lineWidth = 1.2; ctx.setLineDash(dash); ctx.beginPath();
    result[key].forEach((value, index) => {
      const x = left + index / Math.max(result[key].length - 1, 1) * (right - left);
      const y = bottom - (value + maximum) / (2 * maximum) * (bottom - top);
      if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
  ctx.setLineDash([]); ctx.fillStyle = "#4b88ff"; ctx.fillText("共模", left + 6, top + 13);
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
  ctx.strokeStyle = "#84b4ff"; ctx.lineWidth = 1.25; ctx.beginPath();
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
  ctx.strokeStyle = "#4b88ff"; ctx.lineWidth = 1.25; ctx.beginPath();
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

function mountAnalysisLensControls() {
  const target = $("analysisLensSettingsPopover");
  if (!target) return;
  [".analysis-spectrum-controls", ".analysis-transform-controls"].forEach((selector) => {
    const panel = document.querySelector(selector);
    if (panel) target.appendChild(panel);
  });
}

buildChannels();
mountAnalysisLensControls();
applyAnalysisTheme(storedAnalysisTheme());
restoreSettings();
updateMeasureFilterStatus();
showPage(currentPage());
setAnalysisLens("raw", false);
setAnalysisViewState("empty");
syncAnalysisLensContext();
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
onElement("linearityAnalyzeBtn", "click", () => analyzeBigLinearity(false));
onElement("linearityReanalyzeBtn", "click", () => analyzeBigLinearity(true));
onElement("linearityFrequency", "change", updateLinearityView);
onElement("linearityRunSelect", "change", updateLinearityRun);
onElement("linMeasurementRunSelect", "change", loadLinearityMeasurementWave);
onElement("lcrAnalysisMode", "change", () => {
  document.body.dataset.lcrAnalysisMode = $("lcrAnalysisMode").value;
  if ($("lcrAnalysisMode").value === "linearity" && latestLinearity) updateLinearityView();
  if (lcrFolderStates.big_lcr) renderLcrFolder("big_lcr");
  if (lcrFolderStates.small_lcr) renderLcrFolder("small_lcr");
});
onElement("bigFolderLoadBtn", "click", () => loadLcrFolder("big_lcr", false));
onElement("bigFolderReanalyzeBtn", "click", () => loadLcrFolder("big_lcr", true));
onElement("smallFolderLoadBtn", "click", () => loadLcrFolder("small_lcr", false));
onElement("smallFolderReanalyzeBtn", "click", () => loadLcrFolder("small_lcr", true));
onElement("bigFolderRunSelect", "change", () => loadLcrFolderRun("big_lcr"));
onElement("smallFolderRunSelect", "change", () => loadLcrFolderRun("small_lcr"));
onElement("bigLcrAutoRange", "change", updateBigLcrControls);
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
$("timeFrequencyMethod").addEventListener("change", () => {
  const method = $("timeFrequencyMethod").value;
  if (["stft", "wpd", "cwt"].includes(document.body.dataset.analysisLens)) {
    setAnalysisLens(method, false);
  }
});
$("timeFrequencyChannel").addEventListener("change", syncAnalysisLensContext);
["modeReferenceChannel", "modeComparisonChannel", "modeSearchStart", "modeSearchEnd"].forEach((id) => {
  $(id).addEventListener(id.includes("Channel") ? "change" : "input", syncAnalysisLensContext);
});
["analysisTimeStart", "analysisTimeEnd"].forEach((id) =>
  $(id).addEventListener("input", () => syncAnalysisTimeRange("time", true)));
["timeFrequencyStart", "timeFrequencyEnd"].forEach((id) =>
  $(id).addEventListener("input", () => syncAnalysisTimeRange("time-frequency", true)));
$("analysisChannels").addEventListener("click", syncAnalysisLensContext);
document.querySelectorAll(".analysis-lenses [data-analysis-lens]").forEach((button) => {
  button.addEventListener("click", () => setAnalysisLens(button.dataset.analysisLens));
});
document.querySelectorAll("[data-analysis-theme-choice]").forEach((button) => {
  button.addEventListener("click", () => applyAnalysisTheme(button.dataset.analysisThemeChoice, true));
});
$("analysisLensSettingsButton").addEventListener("click", (event) => {
  event.stopPropagation();
  const popover = $("analysisLensSettingsPopover");
  setAnalysisSettingsOpen(popover.hidden);
});
$("analysisLensSettingsPopover").addEventListener("click", (event) => event.stopPropagation());
document.addEventListener("click", (event) => {
  const settings = $("analysisLensSettings");
  if (!settings.contains(event.target)) setAnalysisSettingsOpen(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") setAnalysisSettingsOpen(false);
});
$("calculateTimeFrequencyBtn").addEventListener("click", calculateTimeFrequency);
$("calculateModesBtn").addEventListener("click", calculateExperimentalModes);
$("captureBtn").addEventListener("click", startCapture);
$("sweepBtn").addEventListener("click", startSweep);
$("stopBtn").addEventListener("click", stopCapture);
$("sweepStopBtn").addEventListener("click", stopCapture);
$("saveNpz").addEventListener("click", () => save("npz"));
$("saveCsv").addEventListener("click", () => save("csv"));
$("waveform").addEventListener("change", updateAwgControls);
$("sweepMode").addEventListener("change", updateSweepControls);
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
  drawBigLcrCharts();
  drawBigLcrWaves();
  scheduleAwgPreview();
});
bindLcrModeListeners();
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
updateBigLcrControls();
updateSweepBaseSummary();
syncSweepReceiverChannels();
updateTimeFrequencyControls();
setEvent("系统就绪，可以开始配置。", "idle");
pollStatus();
