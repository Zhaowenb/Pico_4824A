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
        references = set(re.findall(r'''\$\(["']([\w-]+)["']\)''', js))
        self.assertFalse(references - set(markup.ids), references - set(markup.ids))
        self.assertEqual(markup.lenses, {'raw', 'filtered', 'mix', 'fft', 'stft', 'cwt', 'wpd', 'experimental'})

    def test_presentation_does_not_add_network_dependencies(self):
        html = (ROOT / 'web/index.html').read_text(encoding='utf-8')
        self.assertFalse(re.search(r'(?:src|href)=["\']https?://', html))
        js = (ROOT / 'web/app.js').read_text(encoding='utf-8').split('/* Signal workstation presentation adapter.')[1]
        self.assertNotIn('fetch(', js)
        self.assertNotIn('setInterval(', js)
        self.assertIn('prefers-reduced-motion:reduce', js)

    def test_scope_and_discrete_wpd_rendering(self):
        css = (ROOT / 'web/styles.css').read_text(encoding='utf-8')
        self.assertIn('body.signal-workstation[data-page]', css)
        self.assertIn('grid-template-columns:264px minmax(0,1fr)', css)
        self.assertIn('#analysisLensSettingsPopover { position:absolute', css)
        js = (ROOT / 'web/app.js').read_text(encoding='utf-8')
        self.assertIn('ctx.imageSmoothingEnabled = result.method !== "wpd"', js)
        self.assertIn('result.frequency_hz[Math.round(ratio * (rows - 1))]', js)

    def test_source_code_snapshot_is_unchanged(self):
        source = ROOT.with_name('Pico_4824A')
        manifest = json.loads((ROOT / 'tests/source-readonly-manifest.json').read_text(encoding='utf-8'))
        self.assertTrue(manifest)
        for name, digest in manifest.items():
            self.assertEqual(hashlib.sha256((source / name).read_bytes()).hexdigest(), digest, name)


if __name__ == '__main__':
    unittest.main()
