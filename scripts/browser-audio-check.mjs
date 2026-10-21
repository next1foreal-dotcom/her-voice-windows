// Real Chromium microphone/worklet/playback pipeline, synthetic media only.
// Starts a local fake-engine fixture on OS-assigned ports; no provider request.
// Optional env: VOICE_PYTHON, VOICE_CHROMIUM, VOICE_QA_OUTPUT.
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir, writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import path from 'node:path';

const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const {chromium} = require(require.resolve('playwright', {
  paths: [root, process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES || root],
}));
const output = path.resolve(process.env.VOICE_QA_OUTPUT || path.join(root, '.runtime/browser-audio-qa'));
await mkdir(output, {recursive: true});
const fixture = spawn(process.env.VOICE_PYTHON || 'python', [
  path.join(root, 'tests/audio_browser_fixture.py'), '--app-port', '0', '--engine-port', '0',
], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']});
let fixtureStderr = '';
fixture.stderr.on('data', data => { fixtureStderr = (fixtureStderr + data).slice(-6000); });
const ready = new Promise((resolve, reject) => {
  let pending = '';
  const timeout = setTimeout(() => reject(new Error('Audio fixture startup timed out.')), 12000);
  fixture.on('error', error => { clearTimeout(timeout); reject(error); });
  fixture.on('exit', code => { clearTimeout(timeout); reject(new Error(`Audio fixture exited ${code}: ${fixtureStderr}`)); });
  fixture.stdout.on('data', data => {
    pending += data;
    for (;;) {
      const newline = pending.indexOf('\n');
      if (newline < 0) break;
      const line = pending.slice(0, newline);
      pending = pending.slice(newline + 1);
      try {
        const value = JSON.parse(line);
        if (value.fixture === 'ready') { clearTimeout(timeout); resolve(value); }
      } catch { /* Only the structured ready record is needed. */ }
    }
  });
});
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const checks = [];
const errors = [];
const externalRequests = [];
const receivedEvents = [];
let browser;
let page;
let control;
let finalStats;
let audioMetrics;

async function stats() {
  const response = await fetch(control + '/test/stats');
  assert.equal(response.status, 200);
  return response.json();
}
async function action(name) {
  const response = await fetch(control + '/test/action/' + name, {method: 'POST'});
  assert.equal(response.status, 200, await response.text());
}
async function waitForStats(predicate, description) {
  const deadline = Date.now() + 12000;
  while (Date.now() < deadline) {
    const current = await stats();
    if (predicate(current)) return current;
    await sleep(50);
  }
  throw new Error('Timed out waiting for ' + description);
}
const state = value => page.waitForFunction(expected => document.querySelector('#voice-app').dataset.state === expected, value, {timeout: 12000});
const connection = value => page.waitForFunction(expected => document.querySelector('#voice-app').dataset.connection === expected, value, {timeout: 12000});

try {
  const fixtureReady = await ready;
  control = fixtureReady.control_url;
  browser = await chromium.launch({headless: true,
    ...(process.env.VOICE_CHROMIUM ? {executablePath: process.env.VOICE_CHROMIUM} : {}),
    args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', '--autoplay-policy=no-user-gesture-required'],
  });
  const context = await browser.newContext({permissions: ['microphone'], viewport: {width: 1366, height: 768}});
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (['127.0.0.1', 'localhost'].includes(url.hostname)) return route.continue();
    externalRequests.push(url.origin);
    return route.abort();
  });
  page = await context.newPage();
  page.on('websocket', socket => socket.on('framereceived', ({payload}) => {
    try {
      const event = JSON.parse(String(payload));
      // Retain only event types/IDs, never PCM or transcript payloads.
      receivedEvents.push({type: event.type, id: event.response?.id || event.response_id});
    } catch { /* Protocol parse errors are independently caught by the app. */ }
  }));
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await page.addInitScript(() => {
    // Observers delegate to the original native APIs; no audio API is mocked.
    const qa = window.__voiceAudioQA = {gum: [], modules: [], started: [], stops: [], ended: 0, active: 0};
    const tracks = window.__voiceAudioTracks = [];
    const getUserMedia = MediaDevices.prototype.getUserMedia;
    MediaDevices.prototype.getUserMedia = async function (constraints) {
      const stream = await getUserMedia.call(this, constraints);
      qa.gum.push({constraints, settings: stream.getAudioTracks().map(track => track.getSettings())});
      tracks.push(...stream.getTracks());
      return stream;
    };
    const addModule = AudioWorklet.prototype.addModule;
    AudioWorklet.prototype.addModule = function (url, ...args) {
      qa.modules.push(String(url));
      return addModule.call(this, url, ...args);
    };
    const createBufferSource = BaseAudioContext.prototype.createBufferSource;
    BaseAudioContext.prototype.createBufferSource = function (...args) {
      const node = createBufferSource.apply(this, args);
      const start = node.start;
      const stop = node.stop;
      let active = false;
      node.start = function (...startArgs) {
        const buffer = node.buffer;
        let peak = 0;
        for (const value of buffer.getChannelData(0)) peak = Math.max(peak, Math.abs(value));
        qa.started.push({length: buffer.length, sampleRate: buffer.sampleRate,
          duration: buffer.duration, channels: buffer.numberOfChannels, peak});
        active = true;
        qa.active++;
        return start.apply(node, startArgs);
      };
      node.stop = function (...stopArgs) {
        qa.stops.push({at: performance.now()});
        return stop.apply(node, stopArgs);
      };
      node.addEventListener('ended', () => {
        qa.ended++;
        if (active) { active = false; qa.active--; }
      });
      return node;
    };
  });
  await page.goto(fixtureReady.app_url, {waitUntil: 'networkidle'});
  await page.locator('#call-button').click();
  await connection('connected');
  const captured = await waitForStats(value => value.frames >= 12 && value.nonzero_frames > 0, 'actual microphone/worklet PCM frames');
  assert.equal(captured.invalid_frames, 0);
  assert.deepEqual(Object.keys(captured.frame_lengths), ['1920']);
  assert.ok(captured.peak_pcm16 > 0);
  assert.equal(captured.session_updates[0].audio.input.format.rate, 24000);
  assert.equal(captured.session_updates[0].audio.input.transcription.language, null);
  assert.equal(Object.hasOwn(captured.session_updates[0].audio.output, 'voice'), false,
    'Browser session updates must inherit the saved server reference voice.');
  const captureMetrics = await page.evaluate(() => ({gum: window.__voiceAudioQA.gum, modules: window.__voiceAudioQA.modules}));
  assert.equal(captureMetrics.gum.length, 1);
  assert.ok(captureMetrics.modules.some(url => url.endsWith('capture-worklet.js')));
  checks.push('Native getUserMedia + AudioWorklet sends nonzero PCM16: every frame is 1,920 bytes = 40 ms at 24 kHz mono');

  await action('reply');
  await state('speaking');
  await page.waitForFunction(() => window.__voiceAudioQA.active === 1);
  const beforeBarge = await page.evaluate(() => ({started: window.__voiceAudioQA.started, stops: window.__voiceAudioQA.stops.length}));
  assert.equal(beforeBarge.started.length, 1);
  assert.equal(beforeBarge.started[0].sampleRate, 24000);
  assert.equal(beforeBarge.started[0].duration, 5);
  assert.ok(beforeBarge.started[0].peak > 0);
  assert.equal(beforeBarge.stops, 0);
  checks.push('Captured synthetic PCM traverses the real WebSocket bridge and starts a native 24 kHz WebAudio source');

  const framesBeforeBarge = (await stats()).frames;
  await action('barge-in');
  await state('listening');
  await page.waitForFunction(() => window.__voiceAudioQA.active === 0);
  const cancelled = await waitForStats(value => value.cancels.includes('browser-fixture-old') && value.late_audio_sent === 1, 'scoped natural barge-in cancel');
  assert.equal(cancelled.cancels.filter(id => id === 'browser-fixture-old').length, 1);
  await sleep(350);
  const afterBarge = await page.evaluate(() => ({started: window.__voiceAudioQA.started.length, stopped: window.__voiceAudioQA.stops.length,
    active: window.__voiceAudioQA.active, state: document.querySelector('#voice-app').dataset.state}));
  assert.deepEqual(afterBarge, {started: 1, stopped: 1, active: 0, state: 'listening'});
  assert.doesNotMatch(await page.locator('#main-caption').innerText(), /STALE_AUDIO_SHOULD_BE_IGNORED/);
  assert.ok((await stats()).frames > framesBeforeBarge, 'Microphone capture should continue during natural barge-in.');
  checks.push('Server speech_started stops the live source and sends exactly one scoped cancel; delayed old PCM/text never creates another source');

  await action('complete');
  await state('speaking');
  await page.waitForFunction(() => window.__voiceAudioQA.started.length === 2);
  const doneDeadline = Date.now() + 1000;
  while (!receivedEvents.some(event => event.type === 'response.done' && event.id === 'browser-fixture-new') && Date.now() < doneDeadline) await sleep(10);
  assert.ok(receivedEvents.some(event => event.type === 'response.done' && event.id === 'browser-fixture-new'));
  await sleep(100);
  const afterDone = await page.evaluate(() => ({state: document.querySelector('#voice-app').dataset.state, active: window.__voiceAudioQA.active}));
  assert.deepEqual(afterDone, {state: 'speaking', active: 1}, 'response.done must not end speaking while native audio remains.');
  await state('listening');
  await page.waitForFunction(() => window.__voiceAudioQA.active === 0);
  assert.equal(await page.locator('#main-caption').innerText(), '合成音訊完成測試。Synthetic audio complete.');
  checks.push('A new response plays after interruption; immediate response.done waits for the 800 ms native playback to drain');
  await page.locator('#call-button').click();
  await connection('disconnected');
  finalStats = await waitForStats(value => value.active_sockets === 0 && value.sessions_closed === 1, 'session cleanup');
  audioMetrics = await page.evaluate(() => ({...window.__voiceAudioQA,
    trackStates: window.__voiceAudioTracks.map(track => track.readyState)}));
  assert.ok(audioMetrics.trackStates.length > 0 && audioMetrics.trackStates.every(state => state === 'ended'));
  assert.equal(audioMetrics.started.length, 2);
  assert.equal(audioMetrics.stops.length, 1);
  assert.equal(audioMetrics.active, 0);
  assert.equal(finalStats.provider_requests, 0);
  assert.equal(finalStats.engine_starts, 0);
  assert.deepEqual(externalRequests, []);
  assert.deepEqual(errors, []);
  checks.push('Stopping releases the real capture track and both WebSocket ends; zero external or provider requests');
  const report = {result: 'PASS', browser: await browser.version(), scope: fixtureReady.scope,
    checks, audioMetrics, afterDone, receivedEvents, fixture: finalStats, errors, externalRequests};
  await writeFile(path.join(output, 'results.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} catch (error) {
  const report = {result: 'FAIL', checks, errors, externalRequests, message: error.message,
    fixtureStderr, ...(control ? {fixture: await stats().catch(() => null)} : {})};
  await writeFile(path.join(output, 'results.json'), JSON.stringify(report, null, 2));
  console.error(JSON.stringify(report, null, 2));
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  fixture.kill('SIGTERM');
  await Promise.race([new Promise(resolve => fixture.once('exit', resolve)), sleep(3000)]);
  if (fixture.exitCode === null && fixture.signalCode === null) fixture.kill('SIGKILL');
}
