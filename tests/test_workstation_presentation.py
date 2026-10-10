"""Presentation invariants, with no third-party test dependencies."""
from html.parser import HTMLParser
from pathlib import Path
import hashlib
import json
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.lenses = set()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if 'id' in values:
            self.ids.append(values['id'])
        if 'data-analysis-lens' in values:
            self.lenses.add(values['data-analysis-lens'])


class WorkstationPresentationTests(unittest.TestCase):
    def test_existing_ids_and_lenses_remain_available(self):
        markup = Markup()
        markup.feed((ROOT / 'web/index.html').read_text(encoding='utf-8'))
        self.assertEqual(len(markup.ids), len(set(markup.ids)))
        js = (ROOT / 'web/app.js').read_text(encoding='utf-8')
        js += ''.join(path.read_text(encoding='utf-8') for path in (ROOT/'web/analysis').glob('*.js'))
        references = set(re.findall(r'''\$\(["']([\w-]+)["']\)''', js))
        ui = (ROOT / 'web/ui/workstation.js').read_text(encoding='utf-8')
        shared_ids = set(re.findall(r'id="([\w-]+)"', ui)) | set(re.findall(r"\.id='([\w-]+)'", ui))
        self.assertFalse(references - set(markup.ids) - shared_ids, references - set(markup.ids) - shared_ids)
        self.assertEqual(markup.lenses, {'raw', 'filtered', 'mix', 'fft', 'stft', 'cwt', 'wpd', 'experimental'})

    def test_presentation_does_not_add_network_dependencies(self):
        html = (ROOT / 'web/index.html').read_text(encoding='utf-8')
        self.assertFalse(re.search(r'(?:src|href)=["\']https?://', html))
        for name in ['ui/workstation.js','views/signal-analysis.js','views/instruments.js','views/experiment.js']:
            js = (ROOT / 'web' / name).read_text(encoding='utf-8')
            self.assertNotIn('fetch(', js)
            self.assertNotIn('setInterval(', js)
        self.assertIn('prefers-reduced-motion:reduce', (ROOT / 'web/ui/workstation.css').read_text(encoding='utf-8'))

    def test_scope_and_discrete_wpd_rendering(self):
        css = (ROOT / 'web/ui/workstation.css').read_text(encoding='utf-8')
        self.assertIn('--wg-rail-width:264px', css)
        self.assertIn('--wg-inspector-width:300px', css)
        self.assertIn('.wg-inspector {position:absolute', css)
        for name in ['index.html','interference/index.html','interference/manual.html']:
            html=(ROOT / 'web' / name).read_text(encoding='utf-8')
            self.assertIn('/ui/workstation.js', html)
            self.assertIn('/ui/workstation.css', html)
            self.assertIn('data-wg-header', html)
            self.assertNotIn('id="workstationAppearanceSettings"', html)
        for name in ['views/signal-analysis.js','views/instruments.js','views/experiment.js']:
            self.assertIn('WaveGuardUI.createStage', (ROOT / 'web' / name).read_text(encoding='utf-8'))
        self.assertNotIn('new ResizeObserver', (ROOT / 'web/app.js').read_text(encoding='utf-8'))
        js = (ROOT / 'web/analysis/time-frequency.js').read_text(encoding='utf-8')
        self.assertIn('ctx.imageSmoothingEnabled = result.method !== "wpd"', js)
        self.assertIn('result.frequency_hz[Math.round(ratio * (rows - 1))]', js)

    def test_action_bindings_survive_ui_extraction(self):
        js = (ROOT / 'web/app.js').read_text(encoding='utf-8')
        for identifier, action in {
            'calculateTimeFrequencyBtn': 'calculateTimeFrequency',
            'calculateModesBtn': 'calculateExperimentalModes',
            'captureBtn': 'startCapture', 'sweepBtn': 'startSweep',
            'stopBtn': 'stopCapture', 'sweepStopBtn': 'stopCapture',
        }.items():
            self.assertIn(f'$("{identifier}").addEventListener("click", {action})', js)

    def test_source_code_snapshot_is_unchanged(self):
        source = ROOT.with_name('Pico_4824A')
        if not source.is_dir():
            self.skipTest('Read-only reference repository is not present in this checkout')
        manifest = json.loads((ROOT / 'tests/source-readonly-manifest.json').read_text(encoding='utf-8'))
        self.assertTrue(manifest)
        for name, digest in manifest.items():
            self.assertEqual(hashlib.sha256((source / name).read_bytes()).hexdigest(), digest, name)


if __name__ == '__main__':
    unittest.main()
