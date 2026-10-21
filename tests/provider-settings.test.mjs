import {test} from 'node:test';
import assert from 'node:assert/strict';
import {attachProviderSettings} from '../web/provider-settings.js';

class Element extends EventTarget {
  value = ''; textContent = ''; disabled = false; attributes = {};
  setAttribute(name, value) { this.attributes[name] = value; }
  replaceChildren(...items) { this.children = items; }
  reportValidity() { return true; }
}
const tick = () => new Promise(resolve => setImmediate(resolve));
async function fixture(t, {changedAtSave = false} = {}) {
  const elements = new Map();
  const previous = globalThis.document;
  globalThis.document = {
    getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
    createElement() { return new Element(); },
  };
  const state = {disconnects: 0, saved: [], config: {provider: 'deepseek', url: 'https://api.deepseek.com/chat/completions', model: 'deepseek-flash', api_key_configured: true}};
  const panel = attachProviderSettings({
    client: {async disconnect() { state.disconnects++; }, async refreshHealth() {}},
    baseUrl: 'http://127.0.0.1:8790/',
    async fetch(url, init = {}) {
      let result;
      if (url.pathname === '/api/bootstrap') result = {token: 'local-fixture'};
      else if (url.pathname === '/api/provider/models') result = {ok: true, models: [{id: 'deepseek-flash', name: 'Flash'}], message: 'ready'};
      else if (init.method === 'PUT') {
        const data = JSON.parse(init.body);
        state.saved.push(data);
        result = {...state.config, ...data, unchanged: !changedAtSave && !data.api_key && data.model === state.config.model};
      } else result = state.config;
      return {ok: true, status: 200, async json() { return result; }};
    },
  });
  t.after(() => {panel.destroy(); globalThis.document = previous;});
  await panel.load();
  state.field = name => elements.get('provider-' + name);
  state.save = async () => {
    state.field('form').dispatchEvent(new Event('submit', {cancelable: true}));
    for (let i = 0; i < 20 && state.field('form').attributes['aria-busy'] === 'true'; i++) await tick();
    assert.equal(state.field('form').attributes['aria-busy'], 'false');
  };
  return state;
}

test('unchanged provider save keeps the live client connected', async t => {
  const h = await fixture(t);
  await h.save();
  assert.equal(h.disconnects, 0);
  assert.equal(h.saved.length, 1);
  assert.match(h.field('message').textContent, /無需重新載入模型/);
});
test('changed model still disconnects before applying', async t => {
  const h = await fixture(t);
  h.field('model').value = 'different-model';
  await h.save();
  assert.equal(h.disconnects, 1);
  assert.equal(h.saved[0].model, 'different-model');
});
test('new key is never assumed unchanged by the browser', async t => {
  const h = await fixture(t);
  h.field('key').value = 'local-test-key';
  await h.save();
  assert.equal(h.disconnects, 1);
});
test('a server-side concurrent change still clears the old session', async t => {
  const h = await fixture(t, {changedAtSave: true});
  await h.save();
  assert.equal(h.disconnects, 1);
});