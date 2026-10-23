import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

// A renderer can pass its unit tests while the served page still has old markup.
test('served homepage supplies every element required to initialize createUI', () => {
  const ui = readFileSync(new URL('../web/ui.js', import.meta.url), 'utf8');
  const html = readFileSync(new URL('../web/index.html', import.meta.url), 'utf8');
  const requiredBlock = ui.match(/const el = Object\.fromEntries\(\[([\s\S]*?)\]\.map/);
  assert.ok(requiredBlock, 'createUI element contract must be discoverable');
  const required = [...requiredBlock[1].matchAll(/"([\w-]+)"/g)].map(match => match[1]);
  assert.ok(required.length > 0, 'must check the actual element contract');
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  const missing = required.filter(id => !ids.includes(id));
  assert.deepEqual(missing, [], `Missing UI elements: ${missing.join(', ')}`);
  assert.equal(new Set(ids).size, ids.length, 'homepage IDs must be unique');
});