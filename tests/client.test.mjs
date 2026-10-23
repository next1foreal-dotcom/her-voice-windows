import {test} from 'node:test';
import assert from 'node:assert/strict';
import {VoiceClient} from '../web/client.js';
import {VoiceAudio} from '../web/audio.js';
import {SNAPSHOT_EXTENSION} from '../web/protocol.js';

const turn = () => new Promise(resolve => setImmediate(resolve));
async function until(predicate) {
  for (let i = 0; i < 100; i++) { if (predicate()) return; await turn(); }
  assert.fail('Expected client condition was not reached.');
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

function harness(name = 'her', extra = {}) {
  const h = {name, sockets: [], requests: [], audio: null, acknowledgeSession: true,
    acknowledgeItems: true, deviceListeners: new Set(), bootstrapWait: null,
    health: {demo_only: false, provider_ready: true, api_key_configured: false,
      engine: {state: 'ready', ready: true, pool: {in_use: 0, units: [{state: 'idle'}]}}},
  };
  class Transport {
    static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
    readyState = 0; bufferedAmount = 0; sent = [];
    constructor(url) {
      this.url = new URL(url); h.sockets.push(this);
      queueMicrotask(() => { if (this.readyState === 0) { this.readyState = 1; this.onopen?.(); } });
    }
    send(value) {
      assert.equal(this.readyState, Transport.OPEN);
      const event = JSON.parse(value); this.sent.push(event);
      if (event.type === 'session.update' && h.acknowledgeSession) queueMicrotask(() => this.emit({type: 'session.updated', session: event.session}));
      if (event.type === 'conversation.item.create' && h.acknowledgeItems) queueMicrotask(() => this.emit({type: 'conversation.item.created', item: event.item}));
    }
    emit(event) { this.onmessage?.({data: JSON.stringify(event)}); }
    close() { this.readyState = Transport.CLOSED; queueMicrotask(() => this.onclose?.({})); }
  }
  const mediaDevices = {
    async enumerateDevices() { return [{deviceId: name + '-mic', groupId: name, kind: 'audioinput', label: 'Synthetic ' + name}]; },
    addEventListener(_kind, listener) { h.deviceListeners.add(listener); },
    removeEventListener(_kind, listener) { h.deviceListeners.delete(listener); },
  };
  const audioFactory = callbacks => {
    const audio = {callbacks, stream: null, enabled: false, queue: [], clears: 0, opens: 0, closes: 0, captureTicket: 0,
      async open() { this.opens++; }, async setOutput(value) { this.output = value; },
      async capture(deviceId) {
        const ticket = ++this.captureTicket;
        if (h.captureWait) await h.captureWait(ticket);
        if (ticket !== this.captureTicket) return false;
        this.stream = {deviceId}; this.enabled = true; return true;
      },
      pauseCapture(paused) { this.enabled = !paused; },
      enqueue(id, base64) { this.queue.push({id, base64}); callbacks.onStart(id, false); },
      clear() { this.clears++; this.queue = []; },
      drain() { this.queue = []; callbacks.onIdle(); },
      async close() { this.captureTicket++; this.closes++; this.clear(); this.stream = null; this.enabled = false; },
    };
    h.audio = audio;
    return audio;
  };
  const options = {baseUrl: `/${name}/voice`, location: 'https://local.test/product/page',
    WebSocket: Transport, audioFactory, mediaDevices,
    async fetch(url, init) {
      h.requests.push({url: new URL(url), ...init});
      assert.ok(new URL(url).pathname.startsWith(`/${name}/voice/`));
      if (url.endsWith('/api/bootstrap')) {
        if (h.bootstrapWait) await h.bootstrapWait.promise;
        if (h.bootstrapFailure) return {ok: false, status: 503, json: async () => ({error: h.bootstrapFailure})};
        return {ok: true, status: 200, json: async () => ({token: name + '-token', health: structuredClone(h.health)})};
      }
      return {ok: true, status: 200, json: async () => structuredClone(h.health)};
    }, ...extra};
  h.client = new VoiceClient(options);
  h.latest = () => h.sockets.at(-1);
  h.reply = (id, text, {done = true} = {}) => {
    const socket = h.latest();
    socket.emit({type: 'response.created', response: {id, status: 'in_progress'}});
    socket.emit({type: 'response.output_audio_transcript.delta', response_id: id, delta: text});
    socket.emit({type: 'response.output_audio.delta', response_id: id, delta: 'AQACAA=='});
    if (done) socket.emit({type: 'response.done', response: {id, status: 'completed'}});
  };
  h.items = () => h.latest().sent.filter(event => event.type === 'conversation.item.create').map(event => event.item);
  return h;
}

test('same-origin prefixes drive HTTP and secure WS; generic keyless readiness works', async t => {
  const h = harness('joyboi'); t.after(() => h.client.destroy());
  assert.equal(await h.client.connect(), true);
  assert.ok(h.requests.every(request => request.url.origin === 'https://local.test'));
  assert.ok(h.requests.every(request => request.credentials === 'same-origin' && request.redirect === 'error'));
  assert.equal(h.requests[0].url.pathname, '/joyboi/voice/api/bootstrap');
  assert.equal(h.latest().url.protocol, 'wss:');
  assert.equal(h.latest().url.pathname, '/joyboi/voice/ws/realtime');
  assert.equal(h.latest().url.searchParams.get('token'), 'joyboi-token');
  assert.equal(h.latest().url.searchParams.get('mode'), 'live');
  assert.equal(h.client.state.health.api_key_configured, false);
  assert.equal(h.client.state.connection, 'connected');
});

test('cross-origin bootstrap, URL credentials and product-as-mode are rejected before transport', () => {
  for (const baseUrl of ['https://remote.test/voice/', 'http://local.test/voice/', 'https://name:secret@local.test/voice/', '/voice?tenant=other']) {
    assert.throws(() => new VoiceClient({baseUrl, location: 'https://local.test/'}), TypeError);
  }
  assert.throws(() => new VoiceClient({mode: 'joyboi', location: 'https://local.test/'}), /live or demo/);
  const original = Object.getOwnPropertyDescriptor(globalThis, 'location');
  Object.defineProperty(globalThis, 'location', {value: {href: 'https://actual-page.test/'}, configurable: true});
  try {
    assert.throws(() => new VoiceClient({baseUrl: 'https://remote.test/', location: 'https://remote.test/'}), /current page origin/);
  } finally {
    if (original) Object.defineProperty(globalThis, 'location', original); else delete globalThis.location;
  }
});

test('two clients keep history, cancellation, settings, levels and sockets independent', async t => {
  const a = harness('her'), b = harness('joyboi');
  t.after(() => Promise.all([a.client.destroy(), b.client.destroy()]));
  const aStates = [], bLevels = [];
  a.client.subscribe(state => aStates.push(state));
  const unsubLevels = b.client.onLevels(levels => bLevels.push(levels));
  await Promise.all([a.client.connect(), b.client.connect()]);
  await Promise.all([a.client.sendText('HER private history'), b.client.sendText('Joyboi private history')]);
  a.reply('same-server-id', 'HER partial reply', {done: false});
  b.reply('same-server-id', 'Joyboi full reply');
  assert.equal(b.client.state.state, 'speaking');
  assert.equal(await a.client.interrupt(), true);
  assert.equal(a.client.state.state, 'interrupted');
  assert.equal(b.client.state.state, 'speaking');
  assert.equal(b.audio.queue.length, 1);
  assert.equal(b.latest().sent.filter(event => event.type === 'response.cancel').length, 0);
  assert.equal(a.sockets.at(-2).sent.filter(event => event.type === 'response.cancel').length, 1);
  assert.deepEqual(a.items().map(item => item.content[0].text), ['HER private history']);
  assert.ok(!JSON.stringify(a.client.state.messages).includes('Joyboi'));
  assert.ok(!JSON.stringify(b.client.state.messages).includes('HER'));
  assert.throws(() => a.client.state.messages.push({role: 'user', text: 'external mutation'}), TypeError);
  assert.throws(() => { a.client.state.settings.bargeIn = false; }, TypeError);
  assert.equal(aStates[0].messages.length, 0, 'Old emitted snapshots must remain detached.');
  await a.client.updateSettings({bargeIn: false});
  assert.equal(b.client.state.settings.bargeIn, true);
  a.audio.callbacks.onLevel(0.8, 0.3);
  assert.deepEqual(bLevels, [{input: 0, output: 0}]);
  b.audio.callbacks.onLevel(0.2, 0.4);
  assert.deepEqual(bLevels.at(-1), {input: 0.2, output: 0.4});
  unsubLevels();
  b.audio.callbacks.onLevel(0.7, 0.7);
  assert.equal(bLevels.length, 2);
  await a.client.disconnect();
  assert.equal(b.latest().readyState, 1);
  assert.notEqual(b.audio.stream, null);
});

test('pause/resume reseeds complete text, excludes partial output, and clearHistory resets the session', async t => {
  const h = harness(); t.after(() => h.client.destroy());
  await h.client.connect();
  await h.client.sendText('Question one'); h.reply('done-one', 'Complete answer'); h.audio.drain();
  h.reply('partial-two', 'Unfinished answer', {done: false});
  await h.client.pause();
  assert.equal(h.client.state.state, 'paused'); assert.equal(h.audio.enabled, false);
  assert.equal(h.latest().readyState, 3);
  const pausedSocket = h.latest();
  h.acknowledgeItems = false;
  const resuming = h.client.resume();
  await until(() => h.latest() !== pausedSocket && h.items().length === 1);
  h.audio.callbacks.onFrame(new ArrayBuffer(1920));
  assert.equal(h.latest().sent.filter(event => event.type === 'input_audio_buffer.append').length, 0, 'Do not upload audio before history has been acknowledged.');
  h.acknowledgeItems = true;
  h.latest().emit({type: 'conversation.item.created', item: h.items()[0]});
  await resuming;
  assert.deepEqual(h.items().map(item => [item.role, item.content[0].text]), [['user', 'Question one'], ['assistant', 'Complete answer']]);
  assert.equal(h.audio.enabled, true);
  const previous = h.latest();
  await h.client.clearHistory();
  assert.equal(h.client.state.connection, 'disconnected'); assert.deepEqual(h.client.state.messages, []);
  previous.emit({type: 'response.created', response: {id: 'late-after-clear'}});
  previous.emit({type: 'response.output_audio_transcript.delta', response_id: 'late-after-clear', delta: 'Must not return'});
  assert.deepEqual(h.client.state.messages, []);
  await h.client.connect();
  assert.deepEqual(h.items(), []);
});

test('natural barge-in rejects old PCM, while response.done waits for actual audio drain', async t => {
  const h = harness(); t.after(() => h.client.destroy());
  await h.client.connect(); h.reply('old', 'First', {done: false});
  const socket = h.latest();
  socket.emit({type: 'input_audio_buffer.speech_started', item_id: 'second-turn'});
  assert.deepEqual(socket.sent.filter(event => event.type === 'response.cancel'), [{type: 'response.cancel', response_id: 'old'}]);
  assert.equal(h.audio.queue.length, 0);
  socket.emit({type: 'response.output_audio.delta', response_id: 'old', delta: 'AQACAA=='});
  socket.emit({type: 'response.output_audio_transcript.delta', response_id: 'old', delta: 'STALE'});
  assert.equal(h.audio.queue.length, 0);
  assert.ok(!h.client.state.caption.assistant.includes('STALE'));
  h.reply('new', 'Second');
  assert.equal(h.client.state.messages.at(-1).status, 'complete');
  assert.equal(h.client.state.state, 'speaking', 'Generation done alone must not end playback state.');
  h.audio.drain();
  assert.equal(h.client.state.state, 'listening');
});

test('bilingual history survives reconnect and interruption with automatic language and stale-stream isolation', async t => {
  const h = harness('joyboi'); t.after(() => h.client.destroy());
  await h.client.connect();
  const question = 'Can you 解釋 GPU memory？';
  const answer = 'Sure，GPU memory 是顯示記憶體。';
  h.latest().emit({type: 'input_audio_buffer.speech_started', item_id: 'mixed-user'});
  h.latest().emit({type: SNAPSHOT_EXTENSION, item_id: 'mixed-user', transcript: 'Can you 解釋 GPU'});
  h.latest().emit({type: 'conversation.item.input_audio_transcription.completed', item_id: 'mixed-user', transcript: question});
  h.reply('mixed-answer', answer); h.audio.drain();
  await h.client.disconnect();
  await h.client.connect();
  const history = [['user', question], ['assistant', answer]];
  assert.deepEqual(h.items().map(item => [item.role, item.content[0].text]), history);
  h.reply('unfinished-answer', 'Here is more，尚未說完', {done: false});
  const old = h.latest();
  h.acknowledgeItems = false;
  const interrupting = h.client.interrupt();
  await until(() => h.latest() !== old && h.items().length === 1);
  h.audio.callbacks.onFrame(new ArrayBuffer(1920));
  assert.equal(h.latest().sent.filter(event => event.type === 'input_audio_buffer.append').length, 0, 'Mixed-language history must be acknowledged before capture resumes.');
  old.emit({type: SNAPSHOT_EXTENSION, item_id: 'mixed-user', transcript: 'Stale 舊字幕'});
  old.emit({type: 'conversation.item.input_audio_transcription.completed', item_id: 'mixed-user', transcript: 'Stale final 舊字幕'});
  h.acknowledgeItems = true;
  h.latest().emit({type: 'conversation.item.created', item: h.items()[0]});
  assert.equal(await interrupting, true);
  assert.deepEqual(h.items().map(item => [item.role, item.content[0].text]), history);
  assert.equal(h.client.state.messages.find(item => item.id === 'mixed-user').text, question);
  assert.equal(h.client.state.messages.find(item => item.id === 'unfinished-answer').status, 'interrupted');
  assert.equal(h.audio.enabled, true);
  await h.client.updateSettings({bargeIn: false});
  h.reply('english-answer', 'We can continue in English.');
  old.emit({type: 'response.output_audio.delta', response_id: 'unfinished-answer', delta: 'AQACAA=='});
  old.emit({type: 'response.output_audio_transcript.done', response_id: 'unfinished-answer', transcript: 'Stale reply 舊回答'});
  old.emit({type: 'response.done', response: {id: 'unfinished-answer', status: 'completed'}});
  assert.equal(h.client.state.caption.assistant, 'We can continue in English.');
  assert.deepEqual(h.audio.queue.map(chunk => chunk.id), ['english-answer']);
  assert.equal(h.client.state.messages.find(item => item.id === 'unfinished-answer').text, 'Here is more，尚未說完');
  for (const socket of h.sockets) {
    const updates = socket.sent.filter(event => event.type === 'session.update');
    assert.ok(updates.length > 0);
    for (const event of updates) {
      assert.equal(event.session.audio.input.transcription.language, null);
      assert.equal(Object.hasOwn(event.session.audio.output, 'voice'), false, 'Chinese, English and mixed-language reconnects never replace the server voice.');
    }
  }
});

test('disconnect settles a pending handshake and late old-socket callbacks cannot affect reconnect', async t => {
  const h = harness(); t.after(() => h.client.destroy());
  h.acknowledgeSession = false;
  const first = h.client.connect();
  await until(() => h.sockets.length === 1 && h.latest().sent.length > 0);
  const old = h.latest();
  await h.client.disconnect();
  assert.equal(await first, false);
  h.acknowledgeSession = true;
  assert.equal(await h.client.connect(), true);
  const fresh = h.latest();
  old.emit({type: 'session.updated'});
  old.emit({type: 'response.created', response: {id: 'old-generation'}});
  old.emit({type: 'response.output_audio.delta', response_id: 'old-generation', delta: 'AQACAA=='});
  old.onclose?.({});
  assert.equal(h.client.state.connection, 'connected');
  assert.equal(fresh.readyState, 1); assert.deepEqual(h.client.state.messages, []); assert.equal(h.audio.queue.length, 0);
});

test('late microphone setup cannot mark an obsolete connect ready or replace the newer capture', async t => {
  const h = harness(); t.after(() => h.client.destroy());
  const permission = deferred(); let captures = 0;
  h.captureWait = () => ++captures === 1 ? permission.promise : Promise.resolve();
  const oldConnect = h.client.connect();
  await until(() => captures === 1);
  await h.client.disconnect();
  assert.equal(await h.client.connect(), true);
  const newerStream = h.audio.stream;
  permission.resolve();
  assert.equal(await oldConnect, false);
  assert.equal(h.audio.stream, newerStream);
  assert.equal(h.client.state.connection, 'connected');
});

test('destroy aborts bootstrap and releases listeners without late state or error events', async () => {
  const h = harness(); const pending = deferred(); h.bootstrapWait = pending;
  const states = [], errors = [], levels = [];
  h.client.subscribe(value => states.push(value)); h.client.onError(error => errors.push(error)); h.client.onLevels(value => levels.push(value));
  const connecting = h.client.connect();
  await until(() => h.requests.length > 0);
  const stateCount = states.length, levelCount = levels.length;
  await h.client.destroy();
  assert.equal(h.requests[0].signal.aborted, true);
  assert.equal(h.deviceListeners.size, 0);
  pending.resolve();
  assert.equal(await connecting, false);
  h.audio.callbacks.onFrame(new ArrayBuffer(1920)); h.audio.callbacks.onLevel(1, 1); h.audio.callbacks.onError(new Error('late audio failure'));
  assert.equal(states.length, stateCount); assert.equal(levels.length, levelCount); assert.deepEqual(errors, []);
  assert.equal(h.audio.stream, null); assert.equal(h.sockets.length, 0);
  await assert.rejects(h.client.connect(), /destroyed/);
  await h.client.destroy();
});

test('provider_ready=false overrides legacy key presence and errors remain product neutral', async t => {
  const h = harness(); t.after(() => h.client.destroy());
  h.health.provider_ready = false; h.health.api_key_configured = true;
  const errors = []; h.client.onError(error => errors.push(error));
  await assert.rejects(h.client.connect(), /语言模型／Agent/);
  assert.equal(h.sockets.length, 0); assert.equal(h.client.state.connection, 'error');
  assert.equal(errors.length, 1); assert.ok(!errors[0].message.includes('DeepSeek'));
});

test('bootstrap failures reject and publish one error; they never open a socket', async t => {
  const h = harness(); t.after(() => h.client.destroy()); h.bootstrapFailure = 'Fixture bootstrap unavailable';
  const errors = []; h.client.onError(error => errors.push(error));
  await assert.rejects(h.client.connect(), /Fixture bootstrap unavailable/);
  assert.equal(errors.length, 1);
  assert.equal(h.client.state.connection, 'error'); assert.equal(h.sockets.length, 0); assert.equal(h.audio.stream, null);
});

test('capture backpressure closes only its own session without sending more frames', async t => {
  const a = harness('her'), b = harness('joyboi'); t.after(() => Promise.all([a.client.destroy(), b.client.destroy()]));
  await Promise.all([a.client.connect(), b.client.connect()]);
  a.latest().bufferedAmount = 256001;
  a.audio.callbacks.onFrame(new ArrayBuffer(1920));
  await turn();
  assert.equal(a.client.state.connection, 'disconnected'); assert.match(a.client.state.error, /发送落后/);
  assert.equal(a.latest().sent.filter(event => event.type === 'input_audio_buffer.append').length, 0);
  b.audio.callbacks.onFrame(new ArrayBuffer(1920));
  assert.equal(b.latest().sent.at(-1).type, 'input_audio_buffer.append'); assert.equal(b.client.state.connection, 'connected');
});
test('preparing an already starting engine publishes elapsed wait and cancellation does not restart it', async t => {
  const h = harness(); t.after(() => h.client.destroy());
  h.health.engine = {state: 'starting', ready: false, detail: 'TTS warmup', tts_backend: 'drama3',
    tts_process: {pool: {model_loaded: true}}};
  const connecting = h.client.connect();
  await until(() => Number.isFinite(h.client.state.health?.engine?.startup_wait_seconds));
  assert.equal(h.client.state.connection, 'connecting');
  assert.equal(h.client.state.health.engine.detail, 'TTS warmup');
  assert.ok(h.client.state.health.engine.startup_wait_seconds >= 0);
  assert.ok(h.requests.every(request => !request.url.pathname.endsWith('/api/engine/start')));
  await h.client.disconnect();
  await connecting;
  assert.equal(h.client.state.connection, 'disconnected');
});

test('startup failure reaches the error state without opening capture or a socket', async t => {
  const h = harness();
  t.after(() => h.client.destroy());
  h.health.engine = {state: 'starting', ready: false};
  let statuses = 0;
  const originalFetch = h.client._fetch;
  h.client._fetch = async (...args) => {
    if (new URL(args[0]).pathname.endsWith('/api/status') && ++statuses >= 2) {
      h.health.engine = {state: 'failed', ready: false, detail: 'Drama 3 語音預熱失敗或逾時'};
    }
    return originalFetch(...args);
  };
  await assert.rejects(h.client.connect(), /語音預熱失敗/);
  assert.equal(h.client.state.connection, 'error');
  assert.match(h.client.state.error, /語音預熱失敗/);
  assert.equal(h.audio.stream, null);
  assert.equal(h.sockets.length, 0);
});

test('streaming audio avoids repeated full-state notifications while preserving onset, timings and completion', async t => {
  const h = harness(); t.after(() => h.client.destroy());
  await h.client.connect();
  let notifications = 0; h.client.subscribe(() => notifications++);
  const socket = h.latest();
  socket.emit({type: 'input_audio_buffer.speech_stopped', item_id: 'u'});
  socket.emit({type: 'response.created', response: {id: 'r'}});
  const before = notifications;
  for (let i = 0; i < 100; i++) socket.emit({type: 'response.output_audio.delta', response_id: 'r', delta: 'AQACAA=='});
  assert.equal(h.audio.queue.length, 100);
  assert.ok(notifications - before <= 1, `Audio chunks caused ${notifications - before} full state notifications`);
  h.audio.callbacks.onStart('r', true);
  assert.equal(h.client.state.state, 'speaking');
  assert.ok(Number.isFinite(h.client.state.metrics.firstAudioMs));
  socket.emit({type: 'response.done', response: {id: 'r', status: 'completed', metadata: {
    'speech_to_speech.turn_latency': JSON.stringify({version: 2, stt_s: 0.12, tts_ttfa_s: 1.8})}}});
  assert.equal(h.client.state.metrics.server.sttMs, 120);
  assert.equal(h.client.diagnostics.recent.length, 1);
  assert.ok(Object.isFrozen(h.client.diagnostics));
  h.audio.drain(); assert.equal(h.client.state.state, 'listening');
});
test('a missing saved speaker connects, resets settings, and recovers on device removal', async t => {
  const sinks = [];
  let audio, missing = 'expired';
  const h = harness('sink-recovery', {
    settings: {outputDeviceId: 'expired', micDeviceId: 'nuroum'},
    audioFactory(callbacks) {
      audio = new VoiceAudio(callbacks);
      audio.context = {state: 'running', resume: async () => {}, close: async () => {},
        setSinkId: async id => {
          sinks.push(id);
          if (id === missing) throw Object.assign(new Error('device not found'), {name: 'NotFoundError'});
        }};
      audio.capture = async id => { audio.capturedId = id; return true; };
      return audio;
    },
  });
  t.after(() => h.client.destroy());
  const snapshots = [];
  h.client.subscribe(state => snapshots.push(state));
  assert.equal(await h.client.connect(), true);
  assert.equal(h.client.state.connection, 'connected');
  assert.equal(h.client.state.error, '');
  assert.equal(h.client.state.settings.outputDeviceId, '');
  assert.equal(audio.capturedId, 'nuroum');
  assert.equal(h.client.state.settings.micDeviceId, 'nuroum');
  assert.ok(snapshots.some(state => state.settings.outputDeviceId === ''));
  await h.client.updateSettings({outputDeviceId: 'usb-speaker'});
  missing = 'usb-speaker';
  for (const listener of h.deviceListeners) listener();
  await until(() => h.client.state.settings.outputDeviceId === '');
  assert.equal(h.client.state.connection, 'connected');
  assert.equal(h.sockets.length, 1, 'output recovery must not reconnect the speech engine');
  assert.deepEqual(sinks, ['expired', '', 'usb-speaker', 'usb-speaker', '']);
});

test('an old fallback cannot overwrite a newer output selection', async t => {
  const h = harness('stale-sink', {settings: {outputDeviceId: 'new-speaker'}});
  t.after(() => h.client.destroy());
  h.audio.callbacks.onOutputFallback({requestedDeviceId: 'old-speaker', deviceId: ''});
  assert.equal(h.client.state.settings.outputDeviceId, 'new-speaker');
});