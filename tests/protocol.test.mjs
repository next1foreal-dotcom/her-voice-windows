import {test} from 'node:test';
import assert from 'node:assert/strict';
import {ConversationState, sessionUpdate, SNAPSHOT_EXTENSION} from '../web/protocol.js';

function connected() {
  const state = new ConversationState();
  state.snapshot.connection = 'connected';
  return state;
}
const created = id => ({type: 'response.created', response: {id}});
const done = (id, status = 'completed') => ({type: 'response.done', response: {id, status}});
const audio = id => ({type: 'response.output_audio.delta', response_id: id, delta: 'AAAA'});
const text = (id, delta) => ({type: 'response.output_audio_transcript.delta', response_id: id, delta});

test('GA session uses 24k PCM, automatic bilingual language and cumulative transcript snapshots', () => {
  const event = sessionUpdate({paused: true, bargeIn: false});
  assert.equal(event.session.audio.input.format.rate, 24000);
  assert.equal(event.session.audio.output.format.type, 'audio/pcm');
  assert.equal(event.session.audio.input.turn_detection.create_response, false);
  assert.equal(event.session.audio.input.turn_detection.interrupt_response, false);
  assert.ok(event.session.extensions.includes(SNAPSHOT_EXTENSION));
  assert.equal(event.session.input_audio_format, undefined);
  const wire = JSON.parse(JSON.stringify(event));
  assert.equal(wire.session.audio.input.transcription.language, null, 'Null must survive JSON serialization to clear upstream language overrides.');
  assert.equal(Object.hasOwn(wire.session.audio.output, 'voice'), false, 'Session updates preserve the server-selected reference voice.');
});

for (const example of [
  {name: 'English', partial: 'Could you explain', snapshot: 'Could you explain GPU memory',
    final: 'Could you explain GPU memory in English?', reply: ['Sure. GPU memory ', 'holds the model weights.'],
    replyFinal: 'Sure. GPU memory holds the model weights and working data.'},
  {name: 'Chinese-English mixed', partial: '請用 En', snapshot: '請用 English 解釋 GPU memory',
    final: '請用 English 解釋 GPU memory，再用中文總結。', reply: ['GPU memory stores 模型權重', '和工作資料。'],
    replyFinal: 'GPU memory stores model weights. 中文總結：顯示記憶體儲存模型權重和工作資料。'},
]) {
  test(`${example.name} captions preserve snapshots and authoritative final text`, () => {
    const state = connected();
    state.handle({type: 'input_audio_buffer.speech_started', item_id: 'bilingual-user'});
    state.handle({type: 'conversation.item.input_audio_transcription.delta', item_id: 'bilingual-user', delta: example.partial});
    state.handle({type: SNAPSHOT_EXTENSION, item_id: 'bilingual-user', transcript: example.snapshot});
    state.handle({type: 'conversation.item.input_audio_transcription.delta', item_id: 'bilingual-user', delta: 'duplicated delta'});
    assert.equal(state.snapshot.caption.user, example.snapshot);
    state.handle({type: 'conversation.item.input_audio_transcription.completed', item_id: 'bilingual-user', transcript: example.final});
    assert.equal(state.snapshot.caption.user, example.final);
    assert.deepEqual(state.snapshot.messages[0], {id: 'bilingual-user', role: 'user', text: example.final, status: 'complete'});
    state.handle(created('bilingual-reply'));
    for (const delta of example.reply) state.handle(text('bilingual-reply', delta));
    assert.equal(state.snapshot.caption.assistant, example.reply.join(''));
    state.handle({type: 'response.output_audio_transcript.done', response_id: 'bilingual-reply', transcript: example.replyFinal});
    state.handle(done('bilingual-reply'));
    assert.equal(state.snapshot.caption.assistant, example.replyFinal);
    assert.deepEqual(state.snapshot.messages[1], {id: 'bilingual-reply', role: 'assistant', text: example.replyFinal, status: 'complete'});
  });
}

test('snapshots replace partial Chinese text and final transcript is authoritative', () => {
  const state = connected();
  const event = {item_id: 'u1'};
  state.handle({type: 'input_audio_buffer.speech_started', ...event});
  state.handle({type: 'conversation.item.input_audio_transcription.delta', ...event, delta: '您好'});
  state.handle({type: SNAPSHOT_EXTENSION, ...event, transcript: '你好世界'});
  state.handle({type: 'conversation.item.input_audio_transcription.delta', ...event, delta: '世界'});
  assert.equal(state.snapshot.caption.user, '你好世界');
  state.handle({type: 'conversation.item.input_audio_transcription.completed', ...event, transcript: '你好，世界。'});
  assert.equal(state.snapshot.messages[0].text, '你好，世界。');
  state.handle({type: 'conversation.item.input_audio_transcription.completed', ...event, transcript: ''});
  assert.equal(state.snapshot.messages[0].text, '');
});

test('a late final updates history without overwriting a new utterance caption', () => {
  const state = connected();
  state.handle({type: 'input_audio_buffer.speech_started', item_id: 'u1'});
  state.handle({type: SNAPSHOT_EXTENSION, item_id: 'u1', transcript: '舊'});
  state.handle({type: 'input_audio_buffer.speech_started', item_id: 'u2'});
  state.handle({type: SNAPSHOT_EXTENSION, item_id: 'u2', transcript: '新的問題'});
  state.handle({type: 'conversation.item.input_audio_transcription.completed', item_id: 'u1', transcript: '舊的句子'});
  assert.equal(state.snapshot.caption.user, '新的問題');
  assert.equal(state.snapshot.messages[0].text, '舊的句子');
});

test('barge-in clears audio and quarantines late output from the cancelled response', () => {
  const state = connected();
  state.handle(created('a'));
  state.audioStarted('a');
  state.handle(text('a', '舊回答'));
  const effect = state.handle({type: 'input_audio_buffer.speech_started', item_id: 'u2'});
  assert.deepEqual(effect, {cancelId: 'a', clearAudio: true});
  assert.deepEqual(state.handle(audio('a')), {});
  state.handle(text('a', '不能再出現'));
  state.handle(done('a'));
  assert.equal(state.snapshot.caption.assistant, '舊回答');
  assert.equal(state.snapshot.messages[0].status, 'interrupted');
  assert.equal(state.snapshot.state, 'listening');
});

test('old response.done cannot finish or clear a newer response', () => {
  const state = connected();
  state.handle(created('a'));
  state.interrupt();
  state.handle(created('b'));
  state.audioStarted('b');
  state.handle(done('a'));
  assert.equal(state.snapshot.state, 'speaking');
  assert.equal(state.currentResponseId, 'b');
});

test('response.done waits for audio playback to finish before showing listening', () => {
  const state = connected();
  state.handle(created('a'));
  state.audioStarted('a');
  state.handle(done('a'));
  assert.equal(state.snapshot.state, 'speaking');
  state.audioIdle();
  assert.equal(state.snapshot.state, 'listening');
  assert.equal(state.currentResponseId, null);
  state.interrupt();
  assert.equal(state.snapshot.messages[0].status, 'complete');
});

test('an audio underrun before generation finishes shows thinking', () => {
  const state = connected();
  state.handle(created('a'));
  state.audioStarted('a');
  state.audioIdle();
  assert.equal(state.snapshot.state, 'thinking');
  assert.equal(state.currentResponseId, 'a');
});

test('paused state rejects output but still finalizes already known user transcripts', () => {
  const state = connected();
  state.handle({type: 'input_audio_buffer.speech_started', item_id: 'u'});
  state.setPaused(true);
  state.handle({type: 'conversation.item.input_audio_transcription.completed', item_id: 'u', transcript: '完成'});
  assert.equal(state.snapshot.messages[0].text, '完成');
  assert.equal(state.handle(created('late')).cancelId, 'late');
  assert.deepEqual(state.handle(audio('late')), {});
  assert.equal(state.snapshot.state, 'paused');
});

test('disabled barge-in does not locally cancel the response', () => {
  const state = connected();
  state.snapshot.settings.bargeIn = false;
  state.handle(created('a'));
  const effect = state.handle({type: 'input_audio_buffer.speech_started', item_id: 'u'});
  assert.equal(effect.clearAudio, undefined);
  assert.equal(state.currentResponseId, 'a');
});

test('failed and incomplete responses visibly report completion limits', () => {
  const state = connected();
  state.handle(created('a'));
  assert.equal(state.handle(done('a', 'failed')).clearAudio, true);
  assert.ok(state.snapshot.error);
  state.handle(created('b'));
  state.handle(done('b', 'incomplete'));
  assert.match(state.snapshot.error, /未完整/);
  assert.equal(state.snapshot.messages[1].status, 'incomplete');
});
