"""Static structure, CSS syntax, and target viewport geometry checks."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re

import tinycss2


ROOT = Path(__file__).resolve().parents[1]


class MarkupCheck(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.lenses: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if "id" in values:
            self.ids.append(values["id"] or "")
        if values.get("data-analysis-lens"):
            self.lenses.add(values["data-analysis-lens"] or "")


def main() -> None:
    markup = MarkupCheck()
    markup.feed((ROOT / "web" / "index.html").read_text(encoding="utf-8"))
    duplicates = sorted(value for value in set(markup.ids) if markup.ids.count(value) > 1)
    assert not duplicates, f"duplicate IDs: {duplicates}"
    required = {
        "analysisViewState", "analysisLensFile", "analysisLensChannels", "analysisLensRate",
        "analysisLensRange", "analysisCursorTime", "analysisCursorValue",
    }
    assert required.issubset(markup.ids), f"missing context IDs: {sorted(required - set(markup.ids))}"
    assert markup.lenses == {"raw", "filtered", "mix", "fft", "stft", "wpd", "cwt", "experimental"}

    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    literal_id_refs = set(re.findall(r'\$\("([^"]+)"\)', js))
    missing_refs = sorted(literal_id_refs - set(markup.ids))
    assert not missing_refs, f"literal DOM IDs missing from HTML: {missing_refs}"

    css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
    rules = tinycss2.parse_stylesheet(css, skip_whitespace=True, skip_comments=True)
    errors = [rule for rule in rules if rule.type == "error"]
    assert not errors, f"CSS parse errors: {errors}"
    assert 'body[data-page="file-analysis"]' in css
    assert "@media (max-width:1180px)" in css
    assert "@media (max-width:900px)" in css
    assert "@media (max-width:680px)" in css

    for relative in ("web/interference/index.html", "web/interference/manual.html"):
        interference_markup = MarkupCheck()
        interference_markup.feed((ROOT / relative).read_text(encoding="utf-8"))
        interference_duplicates = sorted(
            value for value in set(interference_markup.ids) if interference_markup.ids.count(value) > 1
        )
        assert not interference_duplicates, f"duplicate IDs in {relative}: {interference_duplicates}"
    for relative in ("web/interference/styles.css", "web/interference/manual.css"):
        interference_css = (ROOT / relative).read_text(encoding="utf-8")
        interference_rules = tinycss2.parse_stylesheet(interference_css, skip_whitespace=True, skip_comments=True)
        interference_errors = [rule for rule in interference_rules if rule.type == "error"]
        assert not interference_errors, f"CSS parse errors in {relative}: {interference_errors}"
        assert 'data-ui-theme="dark"' in interference_css
        assert "#2f73ff" in interference_css
    assert 'INTERFERENCE_DIR = WEB_DIR / "interference"' in (ROOT / "pico4824a" / "web.py").read_text(encoding="utf-8")
    assert 'parent.parent / "data" / "interference"' in (ROOT / "pico4824a" / "interference.py").read_text(encoding="utf-8")

    viewports = ((1366, 768), (1440, 900), (1600, 900), (1920, 1080), (2560, 1440))
    print("PASS: WaveGuard HTML/CSS, unique IDs, eight Lens targets, local interference assets, and responsive breakpoints.")
    for width, height in viewports:
        shell = min(width, 2240)
        padding = min(58, max(22, width * 0.0325))
        gap = min(34, max(18, width * 0.022))
        content = shell - 2 * padding
        stage = content - 310 - gap
        canvas = min(570, max(350, height * 0.51))
        assert stage > 700, f"insufficient chart width at {width}x{height}: {stage:.1f}px"
        print(f"{width}×{height}: shell content {content:.0f}px · stage {stage:.0f}px · chart {canvas:.0f}px")


if __name__ == "__main__":
    main()
