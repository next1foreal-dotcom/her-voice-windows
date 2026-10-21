import {test} from 'node:test';
import assert from 'node:assert/strict';
import {StreamingResampler, floatToPCM16, pcm16ToFloat} from '../web/audio-dsp.js';
import {VoiceAudio, encodeBase64} from '../web/audio.js';

for (const rate of [44100, 48000, 16000]) {
  test(`resampling ${rate} Hz preserves duration and values across arbitrary chunks`, () => {
    const input = Float32Array.from({length: rate}, (_, i) => 0.4 * Math.sin(i * 0.07));
    const whole = new StreamingResampler(rate).push(input);
    const converter = new StreamingResampler(rate);
    const parts = [];
    for (let i = 0; i < input.length; i += 127) parts.push(...converter.push(input.subarray(i, i + 127)));
    assert.equal(whole.length, 24000);
    assert.deepEqual(Float32Array.from(parts), whole);
  });
}

test('PCM16 is clipped, little endian, finite, and reversible at quantization precision', () => {
  const pcm = floatToPCM16(Float32Array.from([-2, -1, 0, 0.5, 1, 2, NaN]));
  assert.deepEqual([...new Uint8Array(pcm).slice(0, 4)], [0, 128, 0, 128]);
  const values = pcm16ToFloat(new Uint8Array(pcm));
  assert.equal(values[0], -1);
  assert.equal(values[2], 0);
  assert.equal(values[3], 0.5);
  assert.equal(values[4], 32767 / 32768);
  assert.equal(values[6], 0);
  assert.throws(() => pcm16ToFloat(new Uint8Array(3)), RangeError);
});

class FakeContext {
  currentTime = 0;
  state = 'running';
  destination = {};
  nodes = [];
  audioWorklet = {addModule: async () => {}};
  createBuffer(channels, length, rate) { return {duration: length / rate, copyToChannel() {}}; }
  createBufferSource() {
    const node = {stopped: false, disconnected: false, connect() {}, start(when) {this.when = when;}, stop() {this.stopped = true;}, disconnect() {this.disconnected = true;}};
    this.nodes.push(node);
    return node;
  }
  createMediaStreamSource() { return {connect() {}, disconnect() {}}; }
  async resume() { this.state = 'running'; }
  async close() { this.state = 'closed'; }
}

test('queued audio schedules contiguously; clear stops current and future nodes immediately', () => {
  let idle = 0;
  const output = new VoiceAudio({onIdle: () => idle++});
  output.context = new FakeContext();
  const pcm = encodeBase64(floatToPCM16(new Float32Array(2400)));
  output.enqueue('a', pcm);
  output.enqueue('a', pcm);
  const [first, second] = output.context.nodes;
  assert.ok(Math.abs(second.when - first.when - 0.1) < 1e-6);
  output.clear();
  assert.ok(first.stopped && second.stopped && first.disconnected && second.disconnected);
  assert.equal(output.sources.size, 0);
  assert.equal(output.timers.size, 0);
  assert.equal(idle, 0, 'cancel must not trigger a stale listening transition');
});

test('normal playback becomes idle only after the final queued node ends', () => {
  let idle = 0;
  const output = new VoiceAudio({onIdle: () => idle++});
  output.context = new FakeContext();
  const pcm = encodeBase64(floatToPCM16(new Float32Array(240)));
  output.enqueue('a', pcm); output.enqueue('a', pcm);
  const [first, second] = output.context.nodes;
  first.onended();
  assert.equal(idle, 0);
  second.onended();
  assert.equal(idle, 1);
  output.clear();
});

test('a late microphone permission result cannot replace or stop a newer capture', async () => {
  const oldNavigator = globalThis.navigator;
  const oldWorklet = globalThis.AudioWorkletNode;
  let resolveOld;
  let calls = 0;
  const makeStream = () => {
    const track = {enabled: true, stopped: false, stop() {this.stopped = true;}, addEventListener() {}};
    return {track, getTracks: () => [track], getAudioTracks: () => [track]};
  };
  const oldStream = makeStream(), newStream = makeStream();
  Object.defineProperty(globalThis, 'navigator', {value: {mediaDevices: {getUserMedia: () => ++calls === 1 ? new Promise(r => {resolveOld = r;}) : Promise.resolve(newStream)}}, configurable: true});
  globalThis.AudioWorkletNode = class {port = {}; connect() {} disconnect() {}};
  try {
    const audio = new VoiceAudio({});
    audio.context = new FakeContext();
    const stale = audio.capture('old');
    await audio.stopCapture();
    assert.equal(await audio.capture('new'), true);
    resolveOld(oldStream);
    assert.equal(await stale, false);
    assert.equal(audio.stream, newStream);
    assert.equal(oldStream.track.stopped, true);
    assert.equal(newStream.track.stopped, false);
    await audio.close();
  } finally {
    Object.defineProperty(globalThis, 'navigator', {value: oldNavigator, configurable: true});
    globalThis.AudioWorkletNode = oldWorklet;
  }
});

test('worklet URL follows the audio module, not the host page path', async () => {
  const oldNavigator = globalThis.navigator, oldWorklet = globalThis.AudioWorkletNode;
  const track = {stop() {}, addEventListener() {}};
  Object.defineProperty(globalThis, 'navigator', {value: {mediaDevices: {getUserMedia: async () => ({getTracks: () => [track], getAudioTracks: () => [track]})}}, configurable: true});
  globalThis.AudioWorkletNode = class {port = {}; connect() {} disconnect() {}};
  try {
    const audio = new VoiceAudio({}); audio.context = new FakeContext();
    let moduleURL;
    audio.context.audioWorklet.addModule = async url => { moduleURL = url; };
    await audio.capture();
    assert.equal(moduleURL, new URL('../web/capture-worklet.js', import.meta.url).href);
    await audio.close();
  } finally {
    Object.defineProperty(globalThis, 'navigator', {value: oldNavigator, configurable: true});
    globalThis.AudioWorkletNode = oldWorklet;
  }
});

test('an old asynchronous close never closes a new context opened during cleanup', async () => {
  const oldAudioContext = globalThis.AudioContext;
  let finishClose;
  const oldContext = new FakeContext();
  oldContext.close = () => new Promise(resolve => { finishClose = () => { oldContext.state = 'closed'; resolve(); }; });
  globalThis.AudioContext = FakeContext;
  try {
    const audio = new VoiceAudio({}); audio.context = oldContext;
    const closing = audio.close();
    assert.equal(audio.context, null);
    await audio.open();
    const newContext = audio.context;
    assert.notEqual(newContext, oldContext);
    finishClose(); await closing;
    assert.equal(audio.context, newContext); assert.equal(newContext.state, 'running');
    await audio.close();
  } finally { globalThis.AudioContext = oldAudioContext; }
});

test('pause survives late permission and worklet completion without re-enabling capture', async () => {
  const oldNavigator = globalThis.navigator, oldWorklet = globalThis.AudioWorkletNode;
  let permit, finishModule;
  const permission = new Promise(resolve => { permit = resolve; });
  const module = new Promise(resolve => { finishModule = resolve; });
  const track = {enabled: true, stopped: false, stop() { this.stopped = true; }, addEventListener() {}};
  const stream = {getTracks: () => [track], getAudioTracks: () => [track]};
  Object.defineProperty(globalThis, 'navigator', {value: {mediaDevices: {getUserMedia: () => permission}}, configurable: true});
  globalThis.AudioWorkletNode = class {port = {}; connect() {} disconnect() {}};
  try {
    const audio = new VoiceAudio({}); audio.context = new FakeContext();
    audio.context.audioWorklet.addModule = () => module;
    const capturing = audio.capture('changed-microphone');
    audio.pauseCapture(true);
    permit(stream); await new Promise(resolve => setImmediate(resolve));
    assert.equal(track.enabled, false, 'A late permission result must respect an earlier pause.');
    audio.pauseCapture(false); assert.equal(track.enabled, true);
    audio.pauseCapture(true); assert.equal(track.enabled, false, 'Pause must reach a stream still loading its worklet.');
    finishModule(); assert.equal(await capturing, true);
    assert.equal(audio.enabled, false); assert.equal(audio.stream, stream); assert.equal(track.enabled, false);
    await audio.close();
    assert.equal(track.stopped, true); assert.equal(audio.pendingStream, null);
  } finally {
    Object.defineProperty(globalThis, 'navigator', {value: oldNavigator, configurable: true});
    globalThis.AudioWorkletNode = oldWorklet;
  }
});

test('ignored microphone permission times out and releases a late grant', async t => {
  const oldNavigator = globalThis.navigator;
  let grant;
  const permission = new Promise(resolve => { grant = resolve; });
  Object.defineProperty(globalThis, 'navigator', {value: {mediaDevices: {getUserMedia: () => permission}}, configurable: true});
  t.after(() => Object.defineProperty(globalThis, 'navigator', {value: oldNavigator, configurable: true}));
  t.mock.timers.enable({apis: ['setTimeout']});
  const audio = new VoiceAudio({}); audio.context = new FakeContext();
  const pending = audio.capture();
  const rejected = assert.rejects(pending, /麥克風啟用/);
  t.mock.timers.tick(30001);
  await rejected;
  const track = {stopped: false, stop() { this.stopped = true; }};
  grant({getTracks: () => [track]});
  await Promise.resolve();
  assert.equal(track.stopped, true);
  assert.equal(audio.stream, null);
  await audio.close();
});