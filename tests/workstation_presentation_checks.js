// Run with node tests/workstation_presentation_checks.js. No external packages.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const js = fs.readFileSync(path.join(__dirname, '../web/views/signal-analysis.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '../web/ui/workstation.css'), 'utf8');
const axis = js.match(/function signalTimeAtPixel[\s\S]*?\n}/)[0];
const pulse = js.match(/const pulse=\(node,className\)=>\{[\s\S]*?\n  };/)[0];
const make = (reduced) => vm.runInNewContext(`${axis}\n${pulse}\n({signalTimeAtPixel,pulse})`, {
  matchMedia: () => ({ matches: reduced }),
});
const reduced = make(true);
assert.equal(reduced.signalTimeAtPixel(68, 1084, -100, 900), -100);
assert.equal(reduced.signalTimeAtPixel(1068, 1084, -100, 900), 900);
assert.equal(reduced.signalTimeAtPixel(568, 1084, -100, 900), 400);
assert.equal(reduced.signalTimeAtPixel(-100, 1084, -100, 900), -100);
assert.equal(reduced.signalTimeAtPixel(5000, 1084, -100, 900), 900);
assert(Number.isFinite(reduced.signalTimeAtPixel(0, 0, -100, 900)));
assert.doesNotThrow(() => reduced.pulse({ classList: {
  remove: () => { throw new Error('animation touched'); },
  add: () => { throw new Error('animation touched'); },
}}, 'test'));
const operations = [];
make(false).pulse({ offsetWidth: 100, classList: {
  remove: () => operations.push('remove'), add: () => operations.push('add'),
}}, 'test');
assert.deepEqual(operations, ['remove', 'add']);
assert(css.includes('@media(prefers-reduced-motion:reduce)'));
assert(css.includes('animation:none!important'));
console.log('PASS: time-axis selection geometry (bounds, center, zero width); Reduced Motion skips result/Lens animations; normal motion remains available.');
