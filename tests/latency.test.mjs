import {test} from 'node:test';
import assert from 'node:assert/strict';
import {TurnLatency, serverTimings} from '../web/protocol.js';
const meta = value => ({'speech_to_speech.turn_latency': JSON.stringify(value)});
test('server timings whitelist finite durations; metadata text is never retained', () => {
  const value = serverTimings(meta({version: 2, stt_s: 0.12, llm_s: 1.1, tts_ttfa_s: 1.8,
    e2e_s: 3.2, hold_s: null, vad_decision_s: -1, smart_status: 'complete', transcript: 'private'}));
  assert.equal(value.sttMs, 120); assert.equal(value.tts_ttfaMs, 1800);
  assert.equal(value.holdMs, null); assert.equal(value.vad_decisionMs, null);
  assert.equal(value.transcript, undefined);
  assert.equal(serverTimings(meta({version: 3})), null);
  assert.equal(serverTimings({'speech_to_speech.turn_latency': 'broken'}), null);
});
test('timings distinguish stop notification, first packet, scheduling and playback; metadata is not additive', () => {
  let now = 100; const clock = new TurnLatency(() => now);
  clock.observe({type: 'input_audio_buffer.speech_stopped'}, {userStopped: true});
  now = 120; clock.observe({type: 'response.created', response: {id: 'r'}});
  now = 800; clock.observe({type: 'response.output_audio_transcript.delta', response_id: 'r', delta: 'secret reply'});
  now = 2100; clock.observe({type: 'response.output_audio.delta', response_id: 'r'}, {audio: {}});
  clock.scheduled({id: 'r', durationMs: 100, queueDelayMs: 35, gapMs: 0});
  now = 2135; clock.playback('r');
  assert.equal(clock.metrics.firstAudioMs, 2035); assert.equal(clock.metrics.firstPacketMs, 2000);
  assert.equal(clock.metrics.playbackQueueMs, 35); assert.equal(clock.metrics.firstTranscriptMs, 700);
  clock.scheduled({id: 'r', durationMs: 100, queueDelayMs: 35, gapMs: 40});
  clock.observe({type: 'response.done', response: {id: 'r', status: 'completed', metadata: meta({version: 2, e2e_s: 3.2})}});
  assert.equal(clock.metrics.server.e2eMs, 3200); assert.equal(clock.metrics.gapCount, 1);
  assert.equal(clock.metrics.audioMs, 200); assert.equal(clock.records.length, 1);
  assert.ok(!JSON.stringify(clock.snapshot()).includes('secret reply'));
});
test('cancelled and stale responses cannot overwrite a newer turn; duplicate terminals do not accumulate', () => {
  let now = 0; const clock = new TurnLatency(() => now);
  clock.begin('speech'); clock.observe({type: 'response.created', response: {id: 'old'}});
  clock.observe({type: 'input_audio_buffer.speech_started'});
  now = 100; clock.observe({type: 'input_audio_buffer.speech_stopped'}, {userStopped: true});
  clock.observe({type: 'response.created', response: {id: 'new'}});
  now = 600; clock.playback('old');
  clock.observe({type: 'response.done', response: {id: 'old', status: 'completed'}});
  assert.equal(clock.metrics.firstAudioMs, null); assert.equal(clock.records.length, 0);
  clock.playback('new'); assert.equal(clock.metrics.firstAudioMs, 500);
  const done = {type: 'response.done', response: {id: 'new', status: 'completed'}};
  clock.observe(done); clock.observe(done); assert.equal(clock.records.length, 1);
  for (let i = 0; i < 30; i++) {clock.begin('text'); clock.observe({type: 'response.created', response: {id: `r${i}`}}); clock.observe({type: 'response.done', response: {id: `r${i}`, status: 'completed'}});}
  assert.equal(clock.records.length, 20);
});
