// Real frontend + real Python create_app routes, with an empty temporary demo
// workspace. Start `python -m her_voice --demo --workspace <empty directory>`.
// The test refuses live mode or an existing voice library. It imports one
// synthetic PCM WAV and changes only this disposable workspace's selection.
// Optional env: VOICE_QA_URL, VOICE_QA_OUTPUT, VOICE_CHROMIUM.
import {createRequire} from 'node:module';
import {mkdir, writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import path from 'node:path';

const require = createRequire(import.meta.url);
const {chromium} = require(require.resolve('playwright', {
  paths: [process.cwd(), process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES || process.cwd()],
}));
const baseUrl = process.env.VOICE_QA_URL || 'http://127.0.0.1:8790';
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(baseUrl).hostname), 'Use a loopback test server.');
const output = path.resolve(process.env.VOICE_QA_OUTPUT || '.runtime/browser-voices-api-qa');
await mkdir(output, {recursive: true});
const bootstrap = await (await fetch(new URL('/api/bootstrap', baseUrl))).json();
assert.equal(bootstrap.health.demo_only, true, 'This check requires an explicit demo server.');
assert.equal(bootstrap.health.provider_ready, false, 'Use a temporary workspace with no provider credentials.');

async function api(route, {method = 'GET', json, authorized = true} = {}) {
  const response = await fetch(new URL(route, baseUrl), {
    method, headers: {
      ...(authorized ? {'X-Voice-Token': bootstrap.token} : {}),
      ...(json ? {'Content-Type': 'application/json'} : {}),
    }, ...(json ? {body: JSON.stringify(json)} : {}),
  });
  const payload = await response.text();
  let body;
  try { body = JSON.parse(payload); }
  catch { body = {error: payload}; } // aiohttp authentication rejects with plain text.
  return {status: response.status, body};
}
assert.deepEqual((await api('/api/voices')).body.voices, [], 'Use a fresh disposable workspace, never a real voice library.');

function syntheticWav({rate = 16000, seconds = 2, channels = 2} = {}) {
  const samples = rate * seconds;
  const size = samples * channels * 2;
  const wav = Buffer.alloc(44 + size);
  wav.write('RIFF', 0); wav.writeUInt32LE(36 + size, 4); wav.write('WAVEfmt ', 8);
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(channels, 22);
  wav.writeUInt32LE(rate, 24); wav.writeUInt32LE(rate * channels * 2, 28);
  wav.writeUInt16LE(channels * 2, 32); wav.writeUInt16LE(16, 34);
  wav.write('data', 36); wav.writeUInt32LE(size, 40);
  for (let frame = 0; frame < samples; frame++) {
    const value = Math.round(Math.sin(2 * Math.PI * 440 * frame / rate) * 7000);
    for (let channel = 0; channel < channels; channel++) wav.writeInt16LE(value, 44 + (frame * channels + channel) * 2);
  }
  return wav;
}

const browser = await chromium.launch({headless: true,
  ...(process.env.VOICE_CHROMIUM ? {executablePath: process.env.VOICE_CHROMIUM} : {}),
});
const context = await browser.newContext({viewport: {width: 1536, height: 1024}, reducedMotion: 'reduce'});
const page = await context.newPage();
const checks = [], errors = [], expectedConsoleErrors = [], externalRequests = [], apiRequests = [], sessionUpdates = [];
let voice, finalConfig, historyBefore, audioAfterSwitch;
await context.route('**/*', route => {
  const request = route.request(), url = new URL(request.url());
  if (url.origin !== new URL(baseUrl).origin) {
    externalRequests.push(url.origin); return route.abort();
  }
  if (url.pathname.startsWith('/api/')) apiRequests.push({method: request.method(), path: url.pathname});
  return route.continue();
});
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => {
  if (message.type() !== 'error') return;
  if (/Failed to load resource.*400/.test(message.text())) expectedConsoleErrors.push(message.text());
  else errors.push(message.text());
});
page.on('websocket', socket => socket.on('framesent', ({payload}) => {
  try { const event = JSON.parse(String(payload)); if (event.type === 'session.update') sessionUpdates.push(event.session); }
  catch { /* App errors are independently observed. */ }
}));
await page.addInitScript(() => {
  const qa = window.__voiceSettingsQA = {started: 0, stopped: 0, active: 0};
  const contexts = window.__voiceSettingsContexts = [];
  const original = BaseAudioContext.prototype.createBufferSource;
  BaseAudioContext.prototype.createBufferSource = function (...args) {
    if (!contexts.includes(this)) contexts.push(this);
    const source = original.apply(this, args), start = source.start, stop = source.stop;
    let active = false;
    source.start = function (...values) { active = true; qa.started++; qa.active++; return start.apply(this, values); };
    source.stop = function (...values) {
      const result = stop.apply(this, values);
      qa.stopped++;
      // AudioContext.close() can suppress queued ended events. Observe the
      // successful native stop and separately assert that its context closed.
      if (active) { active = false; qa.active--; }
      return result;
    };
    source.addEventListener('ended', () => { if (active) { active = false; qa.active--; } });
    return source;
  };
});
const state = value => page.waitForFunction(expected => document.querySelector('#voice-app').dataset.state === expected, value, {timeout: 12000});
const connection = value => page.waitForFunction(expected => document.querySelector('#voice-app').dataset.connection === expected, value, {timeout: 12000});
const history = () => page.locator('#history-list li').evaluateAll(rows => rows.map(row => ({
  role: row.dataset.role, text: row.querySelector('.message-text').textContent,
  status: row.querySelector('.message-status').textContent,
})));

try {
  await page.goto(baseUrl, {waitUntil: 'networkidle'});
  assert.equal(await page.title(), 'HER Voice');
  await page.waitForFunction(() => document.querySelector('#voice-app').dataset.mode === 'demo');
  await page.locator('#settings-button').click();
  await page.waitForFunction(() => document.querySelector('#voice-current').textContent.includes('Drama 3'));
  assert.equal(await page.locator('#voice-apply-button').isDisabled(), true);
  const initial = await api('/api/tts');
  assert.equal(initial.status, 200); assert.equal(initial.body.reference_ready, false);
  assert.deepEqual(initial.body.backends.map(item => item.id), ['drama3', 'qwen3']);
  checks.push('Real Python GET /api/tts and /api/voices expose Drama 3 + Qwen3-TTS Base with an empty voice library');

  await page.locator('#voice-name').fill('Joyboi <中文 English>');
  await page.locator('#voice-transcript').fill('你好，我是 Joyboi。Hello, I am Joyboi.');
  await page.locator('#voice-file').setInputFiles({name: 'invalid.wav', mimeType: 'audio/wav', buffer: Buffer.from('RIFF invalid WAV')});
  await page.locator('#voice-import-button').click();
  await page.waitForFunction(() => !document.querySelector('#voice-admin-error').hidden);
  assert.match(await page.locator('#voice-admin-error').innerText(), /WAV/);
  assert.equal(await page.locator('#voice-name').inputValue(), 'Joyboi <中文 English>');
  assert.equal((await api('/api/voices')).body.voices.length, 0);
  checks.push('Actual multipart parser rejects malformed WAV with HTTP 400, keeps the form, and writes no voice');

  const uploaded = page.waitForResponse(response => response.url().endsWith('/api/voices') && response.request().method() === 'POST');
  await page.locator('#voice-file').setInputFiles({name: 'synthetic-stereo.wav', mimeType: 'audio/wav', buffer: syntheticWav()});
  await page.locator('#voice-import-button').click();
  const uploadResponse = await uploaded;
  assert.equal(uploadResponse.status(), 201); voice = (await uploadResponse.json()).voice;
  assert.match(voice.id, /^[a-f0-9]{32}$/);
  assert.equal(voice.channels, 1); assert.equal(voice.sample_rate, 16000); assert.equal(voice.duration_seconds, 2);
  await page.waitForFunction(id => document.querySelector('#voice-select').value === id, voice.id);
  assert.equal(await page.locator('#voice-select option').last().textContent(), 'Joyboi <中文 English>');
  assert.equal((await api('/api/tts')).body.voice_id, null);
  checks.push('Actual PCM16 stereo upload is normalized to mono; Unicode text survives; saving a voice does not select it');

  await page.locator('#voice-apply-button').click();
  await page.waitForFunction(() => document.querySelector('#voice-admin-message').textContent.includes('声音设置已应用'));
  let config = (await api('/api/tts')).body;
  assert.equal(config.backend, 'drama3'); assert.equal(config.voice_id, voice.id); assert.equal(config.reference_ready, true);
  await connection('disconnected');
  assert.equal((await api('/api/status')).body.engine.ready, false);
  checks.push('Explicit PUT selects Drama 3 and the saved reference voice without loading models or reconnecting');

  await page.keyboard.press('Escape');
  await page.locator('#call-button').click(); await connection('connected');
  await page.locator('#keyboard-button').click();
  await page.locator('#message-input').fill('你好，Please preserve this completed turn.');
  await page.locator('#send-button').click(); await state('speaking'); await state('listening');
  historyBefore = await history();
  assert.equal(historyBefore.length, 2); assert.ok(historyBefore.every(row => row.status === ''));
  await page.locator('#message-input').fill('Change the selected voice backend while this demo audio is playing.');
  await page.locator('#send-button').click(); await state('speaking');
  await page.locator('#settings-button').click();
  await page.waitForFunction(() => !document.querySelector('#voice-backend').disabled);
  await page.locator('#voice-backend').selectOption('qwen3');
  await page.locator('#voice-apply-button').click();
  await page.waitForFunction(() => document.querySelector('#voice-current').textContent.startsWith('Qwen3-TTS Base'));
  await connection('disconnected');
  await page.waitForFunction(() => window.__voiceSettingsQA.active === 0);
  assert.ok(await page.evaluate(() => window.__voiceSettingsContexts.length > 0 && window.__voiceSettingsContexts.every(context => context.state === 'closed')),
    'Applying a backend must close every native playback AudioContext.');
  audioAfterSwitch = await page.evaluate(() => ({...window.__voiceSettingsQA}));
  assert.ok(audioAfterSwitch.stopped > 0, 'Switching while speaking must stop the native audio sources.');
  assert.deepEqual((await history()).slice(0, historyBefore.length), historyBefore);
  await page.waitForTimeout(400);
  assert.deepEqual(await page.evaluate(() => ({...window.__voiceSettingsQA})), audioAfterSwitch, 'No late demo audio may resume playback.');
  finalConfig = (await api('/api/tts')).body;
  assert.equal(finalConfig.backend, 'qwen3'); assert.equal(finalConfig.voice_id, voice.id);
  assert.equal((await api('/api/voices')).body.selected_voice_id, voice.id);
  checks.push('Applying Qwen3-TTS Base while speaking closes the real demo WebSocket, stops native audio, and retains completed text history');

  const rejected = await api('/api/tts', {method: 'PUT', json: {backend: 'unsupported', voice_id: voice.id}});
  assert.equal(rejected.status, 400);
  assert.equal((await api('/api/tts', {method: 'PUT', authorized: false, json: {backend: 'drama3', voice_id: voice.id}})).status, 403);
  assert.deepEqual((await api('/api/tts')).body, finalConfig);
  checks.push('Actual API rejects an unsupported backend and missing token without changing the saved selection');

  await page.reload({waitUntil: 'networkidle'});
  await page.locator('#settings-button').click();
  await page.waitForFunction(() => document.querySelector('#voice-current').textContent.startsWith('Qwen3-TTS Base'));
  assert.equal(await page.locator('#voice-backend').inputValue(), 'qwen3');
  assert.equal(await page.locator('#voice-select').inputValue(), voice.id);
  assert.match(await page.locator('#voice-details').innerText(), /你好，我是 Joyboi。Hello, I am Joyboi\./);
  await connection('disconnected');
  await page.screenshot({path: path.join(output, 'real-api-qwen-selected.png')});
  checks.push('A fresh page reload obtains the persisted Qwen3 backend and reference recording from the real server');

  assert.ok(sessionUpdates.length > 0);
  for (const session of sessionUpdates) assert.equal(Object.hasOwn(session.audio.output, 'voice'), false);
  assert.equal(apiRequests.filter(item => item.path === '/api/engine/start').length, 0);
  assert.deepEqual(externalRequests, []); assert.deepEqual(errors, []);
  assert.equal((await api('/api/status')).body.engine.ready, false);
  checks.push('Session updates inherit the reference voice; zero engine-start requests, external browser requests, or unexpected page errors');

  const report = {result: 'PASS', real_python_api: true, browser: await browser.version(),
    scope: 'Real create_app API, VoiceStore normalization/persistence, frontend and demo WebSocket; synthetic WAV/tone only, no ASR/TTS/GPU/provider calls.',
    checks, voice, finalConfig, completedHistoryRows: historyBefore.length, audioAfterSwitch,
    apiRequests, sessionUpdates, errors, expectedConsoleErrors, externalRequests, output};
  await writeFile(path.join(output, 'results.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} catch (error) {
  await page.screenshot({path: path.join(output, 'failure.png'), fullPage: true});
  const report = {result: 'FAIL', real_python_api: true, checks, errors, message: error.message, output};
  await writeFile(path.join(output, 'results.json'), JSON.stringify(report, null, 2));
  console.error(JSON.stringify(report, null, 2)); process.exitCode = 1;
} finally { await browser.close(); }
