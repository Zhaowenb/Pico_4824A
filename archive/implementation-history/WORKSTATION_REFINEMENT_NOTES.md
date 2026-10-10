# WaveGuard Signal Workstation refinement

Date: 2026-09-30

## Scope and backup

- Writable project: `F:\Project\01guided_waves\software\Pico_4824A_btf`.
- Read-only reference: `F:\Project\01guided_waves\software\Pico_4824A`.
- Pre-change Git snapshot: `snapshot-before-signal-workstation-20260930` (commit `b70df22`).
- This iteration refines `/measure` and `/file-analysis`, including the existing Experimental Lens. Other routes retain their established layout.
- Modified application files: `web/index.html`, `web/styles.css`, `web/app.js`. No Python, algorithms, drivers, public API, or dependencies changed.

## Presentation changes

- Shared 64px global header, 264px control column and a single midnight-blue data surface. Light and Dark use the same geometry; the theme switch remains in the global header.
- Removed repeated chart borders, increased text hierarchy and real-curve contrast, reduced channel-button emphasis.
- Capture/stop/save and analysis actions remain at the bottom of the work surface.
- Existing file browser is a drawer; the normal sidebar shows the current file and common channel/time context.
- Existing Lens controls are docked in a 300px Inspector inside the work surface; narrow layouts use an overlay. Filter parameters belong to the applicable Lens rather than the common sidebar.
- Focus mode expands the real chart, preserving file, channels, ranges and selection. Layout transition is 350ms; Lens settling is 280ms; real-result arrival is 180ms. No animation delays acquisition commands.
- FFT/STFT/CWT/WPD retain a miniature trace drawn from the current real signal. Shift-drag selects a time range and links the first FFT window to it. FFT frequency cursors remain distinct from time cursors.
- Selected channel carries into time-frequency views. Existing request guards remain in place during rapid Lens changes.
- EX keeps its two-channel comparison layout, compact metric strip and expandable candidate list. Actual warnings and results remain visible.
- CWT labels follow the actual logarithmic frequency rows; WPD bands are not interpolated into a continuous heatmap. The time-frequency legend matches the existing blue-white result palette.
- Fixed navigation to the independent `/interference` application, which must use its own document and assets.

## Calculation and device boundary

Original arrays, filtering, FFT, STFT, WPD, CWT, Experimental processing, metadata, units, exports and backend routes are retained. Canvas changes affect display and axis geometry only. There are no synthetic replacement plots or new public interfaces. Simulation uses the existing backend simulation path and is visibly labeled SIMULATED.

## Validation

| Check | Result |
|---|---|
| Python unittest discovery | 86 tests passed, including existing device-timing/simulation coverage and four new presentation/source checks |
| File-analysis API regression | 11 API signatures identical to the recorded baseline; page-route markup intentionally changed |
| JavaScript syntax | `node --check web/app.js` passed |
| Presentation helper checks | Time/pixel bounds, zero-width handling and Reduced Motion/normal-motion branches passed |
| Real fixture | Existing TARGET `A1.npz`, 40k samples at 40 MS/s; fixture unchanged |
| Analysis UI | Browse/load, channels, Raw, Filtered, Mix, FFT, STFT, CWT, WPD, EX and exports exercised |
| Selection linkage | -20 to 100 μs range entered FFT, with actual 8.332 kHz spacing; CH C retained when entering STFT |
| Inspector | Remained open during control changes; no document-height growth or dismissal flash |
| Focus | File, channels and time range preserved across enter/exit |
| Simulation | 8 channels, 200k samples/channel, 20 MS/s; original NPZ and CSV saves succeeded; generated capture files removed after checking |
| Navigation | Sweep, LCR, LCR analysis, sweep analysis and standalone interference entry checked |
| Browser console | Final fresh verification tab reported no error/warn; no visible NaN/undefined or SVG path errors |
| SOURCE read-only | All recorded source-file hashes unchanged |

Responsive checks: **1366×768, 1440×900, 1600×900, 1920×1080, 2560×1440, 1024×768**. Both pages were measured in Light and Dark: identical chart geometry for each size, no outer page overflow, primary actions inside the viewport. EX can scroll its additional comparisons internally. Exact geometry and interaction observations are in `tests/workstation-ui-qa.json`.

New checks: `tests/test_workstation_presentation.py`, `tests/workstation_presentation_checks.js`; read-only reference fingerprint: `tests/source-readonly-manifest.json`.

## Limits

- Physical hardware acquisition was not performed. The QA server was created without device initialization; simulation and existing mocked ADC-before-AWG tests passed. Real hardware timing still requires bench verification.
- Reduced Motion was checked through the actual presentation helper branch and CSS rules; the operating-system preference was not toggled during browser QA.
- The older optional `file_analysis_static_qa.py` needs `tinycss2`, unavailable in the current environment. It was not run successfully, and no dependency was installed. Standard-library invariant tests and actual browser layout checks were used instead.
- The source fingerprint excludes generated/dependency directories and source data. No SOURCE command, installation, server, test, cache or Git mutation was performed.

## Run and use

From TARGET, run `powershell -ExecutionPolicy Bypass -File .\start_web.ps1`, then open `http://127.0.0.1:4824/measure` or `/file-analysis`. Hard-refresh an existing tab to load the updated assets.

- Analysis: click the current-file summary to browse/load; select a Lens; use 参数设置 for its Inspector.
- Shift-drag a time waveform to select an analysis range. Apply display/full-range actions also relink the first FFT window.
- Use 专注 to expand the chart; exit restores the common controls.
- Existing backend/API tests and exports remain authoritative for numerical results.
