import {test} from 'node:test';
import assert from 'node:assert/strict';
import {VoiceClient} from '../web/client.js';

const later = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; }
function fixture() {
  const h = {requests: [], sockets: [], audio: null, updateWait: null, updateError: '', importError: '',
    config: {backend: 'drama3', voice_id: 'original'},
    voices: [{id: 'original', name: '原始音色', transcript: 'Hello，你好。', duration_seconds: 8, sample_rate: 24000}],
  };
  const health = {demo_only: false, provider_ready: true, engine: {state: 'ready', ready: true, pool: {in_use: 0, units: [{state: 'idle'}]}}};
  class Socket {
    static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
    readyState = 0; sent = []; bufferedAmount = 0;
    constructor(url) { this.url = url; h.sockets.push(this); queueMicrotask(() => { this.readyState = 1; this.onopen?.(); }); }
    send(raw) {
      const event = JSON.parse(raw); this.sent.push(event);
      if (event.type === 'session.update') queueMicrotask(() => this.emit({type: 'session.updated', session: event.session}));
      if (event.type === 'conversation.item.create') queueMicrotask(() => this.emit({type: 'conversation.item.created', item: event.item}));
    }
    emit(event) { this.onmessage?.({data: JSON.stringify(event)}); }
    close() { this.readyState = 3; queueMicrotask(() => this.onclose?.({})); }
  }
  h.client = new VoiceClient({baseUrl: '/joyboi/voice/', location: 'https://local.test/app', WebSocket: Socket,
    mediaDevices: {async enumerateDevices() { return []; }},
    audioFactory(callbacks) {
      return h.audio = {stream: null, queue: [], closes: 0, opens: 0,
        async open() { this.opens++; }, async capture() { this.stream = {}; }, pauseCapture() {},
        clear() { this.queue = []; }, enqueue(id, data) { this.queue.push(data); callbacks.onStart(id, false); },
        drain() { this.queue = []; callbacks.onIdle(); },
        async close() { this.closes++; this.clear(); this.stream = null; },
      };
    },
    async fetch(url, init) {
      const request = {path: new URL(url).pathname, ...init}; h.requests.push(request);
      assert.ok(request.path.startsWith('/joyboi/voice/'));
      let body;
      if (request.path.endsWith('/api/bootstrap')) body = {token: 'browser-token', health: structuredClone(health)};
      else {
        assert.equal(init.headers['X-Voice-Token'], 'browser-token');
        if (request.path.endsWith('/api/status')) body = health;
        else if (request.path.endsWith('/api/tts') && init.method === 'PUT') {
          assert.equal(h.audio.stream, null, 'Microphone must be released before applying a backend.');
          assert.equal(h.audio.queue.length, 0, 'Queued audio must be cleared before applying a backend.');
          assert.ok(h.sockets.every(socket => socket.readyState === 3), 'All old sessions must be closed first.');
          if (h.updateWait) await h.updateWait.promise;
          if (h.updateError) return {ok: false, status: 409, json: async () => ({error: h.updateError})};
          h.config = JSON.parse(init.body); body = h.config;
        } else if (request.path.endsWith('/api/tts')) body = h.config;
        else if (request.path.endsWith('/api/voices') && init.method === 'POST') {
          assert.ok(init.body instanceof FormData);
          assert.equal(init.headers['Content-Type'], undefined, 'Browser must set the multipart boundary.');
          if (h.importError) return {ok: false, status: 400, json: async () => ({error: h.importError})};
          const voice = {id: 'new-voice', name: init.body.get('name'), transcript: init.body.get('transcript'), duration_seconds: 6.5, sample_rate: 44100};
          h.voices.push(voice); body = {voice};
        } else if (request.path.endsWith('/api/voices')) body = {voices: h.voices, selected_voice_id: h.config.voice_id};
        else throw new Error('Unexpected request: ' + request.path);
      }
      return {ok: true, status: 200, json: async () => structuredClone(body)};
    },
  });
  h.reply = (id, text, done = true) => {
    const socket = h.sockets.at(-1);
    socket.emit({type: 'response.created', response: {id, status: 'in_progress'}});
    socket.emit({type: 'response.output_audio_transcript.delta', response_id: id, delta: text});
    socket.emit({type: 'response.output_audio.delta', response_id: id, delta: 'AQACAA=='});
    if (done) socket.emit({type: 'response.done', response: {id, status: 'completed'}});
  };
  return h;
}
function wav() { const audio = new Blob(['RIFF fixture'], {type: 'audio/wav'}); Object.defineProperty(audio, 'name', {value: 'reference.wav'}); return audio; }

test('voice settings are same-origin and authenticated; reading them does not load models or open microphone', async t => {
  const h = fixture(); t.after(() => h.client.destroy());
  assert.equal(await h.client.loadVoiceSettings(), true);
  assert.equal(h.client.state.voiceAdmin.config.backend, 'drama3');
  assert.equal(h.client.state.voiceAdmin.voices[0].transcript, 'Hello，你好。');
  assert.equal(h.audio.opens, 0);
  assert.equal(h.sockets.length, 0);
  assert.equal(h.requests.length, 3);
  assert.ok(h.requests.every(request => request.credentials === 'same-origin' && request.redirect === 'error'));
  assert.throws(() => { h.client.state.voiceAdmin.config.backend = 'qwen3'; }, TypeError);
});

test('WAV import keeps the actual FormData, Unicode transcript, and current active voice', async t => {
  const h = fixture(); t.after(() => h.client.destroy());
  await h.client.loadVoiceSettings();
  const imported = await h.client.importVoice({name: ' Joyboi 中文音色 ', transcript: ' Hello，我是 Joyboi。 ', audio: wav()});
  assert.equal(imported.name, 'Joyboi 中文音色');
  assert.equal(imported.transcript, 'Hello，我是 Joyboi。');
  assert.equal(h.client.state.voiceAdmin.importedVoiceId, 'new-voice');
  assert.equal(h.client.state.voiceAdmin.config.voice_id, 'original', 'Importing is not selecting.');
  assert.equal(h.client.state.voiceAdmin.voices.length, 2);
  assert.equal(h.audio.opens, 0);
  assert.equal(h.requests.filter(request => request.method === 'PUT').length, 0);
});

test('a damaged saved voice selection is shown as a repairable server diagnostic', async t => {
  const h = fixture(); t.after(() => h.client.destroy());
  h.config = {backend: 'drama3', voice_id: null, reference_ready: false,
    error: '已保存的语音设置不可用，请重新选择后端和音色并点击应用。'};
  assert.equal(await h.client.loadVoiceSettings(), true);
  assert.equal(h.client.state.voiceAdmin.error, h.config.error);
  assert.equal(h.client.state.voiceAdmin.config.voice_id, null);
  assert.equal(h.client.state.voiceAdmin.voices.length, 1, 'Existing references remain available for repair.');
  assert.equal(h.audio.opens, 0); assert.equal(h.sockets.length, 0);
});

test('switch cancels active response, clears playback before PUT, retains completed history, and never reconnects automatically', async t => {
  const h = fixture(); t.after(() => h.client.destroy());
  await h.client.loadVoiceSettings(); await h.client.connect();
  await h.client.sendText('你好，please keep my history.'); h.reply('complete', 'I will remember this conversation.'); h.audio.drain();
  h.reply('partial', 'This reply is still streaming', false);
  const old = h.sockets.at(-1); const count = h.sockets.length;
  assert.equal(await h.client.applyVoiceSettings({backend: 'qwen3', voice_id: 'original'}), true);
  assert.equal(h.client.state.connection, 'disconnected');
  assert.equal(h.client.state.voiceAdmin.config.backend, 'qwen3');
  assert.equal(h.client.state.health.engine.state, 'stopped');
  assert.equal(h.sockets.length, count);
  assert.ok(old.sent.some(event => event.type === 'response.cancel' && event.response_id === 'partial'));
  assert.equal(h.client.state.messages.find(item => item.id === 'complete').status, 'complete');
  assert.equal(h.client.state.messages.find(item => item.id === 'partial').status, 'interrupted');
  old.emit({type: 'response.output_audio.delta', response_id: 'partial', delta: 'AQACAA=='});
  assert.equal(h.audio.queue.length, 0, 'Old response cannot restart playback after the switch.');
  assert.deepEqual(JSON.parse(h.requests.find(request => request.method === 'PUT').body), {backend: 'qwen3', voice_id: 'original'});
});

test('an in-flight switch disables reconnect and duplicate mutations', async t => {
  const h = fixture(); t.after(() => h.client.destroy());
  await h.client.loadVoiceSettings(); h.updateWait = deferred();
  const pending = h.client.applyVoiceSettings({backend: 'qwen3', voice_id: 'original'});
  await later();
  assert.equal(h.client.state.voiceAdmin.busy, true);
  assert.equal(await h.client.connect(), false);
  assert.equal(await h.client.applyVoiceSettings({backend: 'drama3', voice_id: 'original'}), false);
  assert.equal(await h.client.importVoice({name: 'ignored', transcript: 'ignored', audio: wav()}), false);
  h.updateWait.resolve(); await pending;
  assert.equal(h.client.state.voiceAdmin.busy, false);
  assert.equal(h.requests.filter(request => request.method === 'PUT').length, 1);
});

test('failed switch leaves confirmed backend unchanged and surfaces the actual server failure', async t => {
  const h = fixture(); t.after(() => h.client.destroy());
  await h.client.loadVoiceSettings(); await h.client.connect();
  h.updateError = '原引擎尚未退出，请查看本机诊断。';
  await assert.rejects(h.client.applyVoiceSettings({backend: 'qwen3', voice_id: 'original'}), /原引擎尚未退出/);
  assert.equal(h.client.state.voiceAdmin.config.backend, 'drama3');
  assert.equal(h.client.state.voiceAdmin.busy, false);
  assert.equal(h.client.state.voiceAdmin.error, h.updateError);
  assert.equal(h.client.state.connection, 'disconnected');
  assert.equal(h.client.state.error, h.updateError);
});

test('invalid WAV rejected by the server remains a visible error without appending a fake voice', async t => {
  const h = fixture(); t.after(() => h.client.destroy());
  await h.client.loadVoiceSettings(); h.importError = '参考录音必须为有效的 PCM WAV。';
  await assert.rejects(h.client.importVoice({name: 'Broken', transcript: '测试', audio: wav()}), /PCM WAV/);
  assert.equal(h.client.state.voiceAdmin.voices.length, 1);
  assert.equal(h.client.state.voiceAdmin.error, h.importError);
  assert.equal(h.client.state.voiceAdmin.busy, false);
});

test('invalid model and empty reference cannot start a configuration mutation', async t => {
  const h = fixture(); t.after(() => h.client.destroy());
  for (const selection of [{backend: 'cosyvoice', voice_id: 'original'}, {backend: 'drama3', voice_id: null}]) {
    await assert.rejects(h.client.applyVoiceSettings(selection), /请选择/);
  }
  assert.equal(h.requests.length, 0);
});

test('destroy aborts outstanding backend mutation and ignores its late result', async () => {
  const h = fixture(); await h.client.loadVoiceSettings(); h.updateWait = deferred();
  const pending = h.client.applyVoiceSettings({backend: 'qwen3', voice_id: 'original'}); await later();
  const request = h.requests.find(request => request.method === 'PUT');
  await h.client.destroy(); assert.equal(request.signal.aborted, true);
  h.updateWait.resolve(); assert.equal(await pending, false);
  assert.equal(h.client.state.voiceAdmin.config.backend, 'drama3');
});

test('applying the same confirmed voice preserves microphone, playback, session and ready engine', async t => {
  const h = fixture(); t.after(() => h.client.destroy());
  await h.client.loadVoiceSettings(); await h.client.connect();
  h.reply('playing', 'Still speaking.', false);
  const socket = h.sockets.at(-1), closes = h.audio.closes, queued = h.audio.queue.length;
  assert.equal(await h.client.applyVoiceSettings({backend: 'drama3', voice_id: 'original'}), true);
  assert.equal(h.client.state.connection, 'connected');
  assert.equal(h.client.state.health.engine.ready, true);
  assert.equal(h.audio.closes, closes);
  assert.equal(h.audio.queue.length, queued);
  assert.equal(socket.readyState, 1);
  assert.equal(h.requests.filter(r => r.method === 'PUT').length, 0);
});

test('voice no-op checks fresh server selection instead of trusting a stale panel', async t => {
  const h = fixture(); t.after(() => h.client.destroy());
  await h.client.loadVoiceSettings(); await h.client.connect();
  h.config = {backend: 'qwen3', voice_id: 'original'};
  await h.client.applyVoiceSettings({backend: 'drama3', voice_id: 'original'});
  assert.equal(h.client.state.connection, 'disconnected');
  assert.equal(h.requests.filter(r => r.method === 'PUT').length, 1);
});