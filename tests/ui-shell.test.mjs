import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const html = await readFile(new URL('../web/index.html', import.meta.url), 'utf8');
const ui = await readFile(new URL('../web/ui.js', import.meta.url), 'utf8');
const css = await readFile(new URL('../web/styles.css', import.meta.url), 'utf8');
const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);

test('workspace IDs are unique and every UI adapter hook exists', () => {
  assert.equal(new Set(ids).size, ids.length, 'duplicate HTML id');
  const hooks = ui.slice(ui.indexOf('const el = Object.fromEntries(['), ui.indexOf('].map((id) => [id, byId(id)]));'));
  for (const [, id] of hooks.matchAll(/"([a-z][a-z-]+)"/g)) assert.ok(ids.includes(id), `missing ${id}`);
});
test('provider controller hooks remain available after settings regrouping', async () => {
  const controller = await readFile(new URL('../web/provider-settings.js', import.meta.url), 'utf8');
  const list = controller.match(/const ids = \[([^\]]+)\]/)[1];
  for (const [, id] of list.matchAll(/'([^']+)'/g)) assert.ok(ids.includes(`provider-${id}`), `missing provider-${id}`);
});
test('settings tabs have matching labelled panels and one tab stop', () => {
  for (const name of ['provider', 'voice', 'preferences', 'service']) {
    assert.match(html, new RegExp(`id="tab-${name}" role="tab"[^>]+aria-controls="settings-${name}"`));
    assert.match(html, new RegExp(`id="settings-${name}" role="tabpanel" aria-labelledby="tab-${name}"`));
  }
  assert.equal((html.match(/role="tab"[^>]+tabindex="0"/g) || []).length, 1);
  assert.match(ui, /ArrowRight/); assert.match(ui, /ArrowLeft/);
});
test('workspace assets stay local and appearance follows OS without a saved choice', () => {
  assert.doesNotMatch(html, /(?:src|href)="https?:\/\//);
  assert.match(css, /prefers-color-scheme:dark/);
  assert.match(css, /prefers-reduced-motion:reduce/);
  assert.match(ui, /else delete document.documentElement.dataset.theme/);
  assert.match(html, /src="\.\/app.js"/);
});
test('conversation mirroring never interprets transcript as HTML', () => {
  assert.doesNotMatch(ui, /\.innerHTML\s*=|insertAdjacentHTML/);
  assert.match(ui, /cloneNode\(true\)/);
  assert.match(ui, /\.textContent = text\(message.text\)/);
});
