import {test} from 'node:test';
import assert from 'node:assert/strict';
import {startupCopy} from '../web/ui.js';

test('loaded Drama reports preparation and elapsed wait instead of suggesting a download', () => {
  const [heading, supporting] = startupCopy({state: 'starting', tts_backend: 'drama3',
    startup_wait_seconds: 135, tts_process: {ready: true, pool: {model_loaded: true}}});
  assert.match(heading, /預熱/);
  assert.match(supporting, /已從本機載入/);
  assert.match(supporting, /135 秒/);
  assert.doesNotMatch(supporting, /下載/);
});

test('unloaded native service must not claim the model is loaded', () => {
  const [, supporting] = startupCopy({tts_backend: 'drama3', detail: '正在準備音色',
    tts_process: {ready: true, pool: {model_loaded: false}}});
  assert.match(supporting, /正在準備音色/);
  assert.doesNotMatch(supporting, /已從本機載入/);
});

test('other startup stages display actual server detail without inventing a download', () => {
  const [, supporting] = startupCopy({detail: '正在初始化辨識引擎', startup_wait_seconds: 3});
  assert.match(supporting, /正在初始化辨識引擎/);
  assert.match(supporting, /3 秒/);
  assert.doesNotMatch(supporting, /下載/);
});
test('loaded Drama retains the current backend preparation phase', () => {
  const [, supporting] = startupCopy({tts_backend: 'drama3',
    detail: '正在編碼完整參考音色並預熱 Drama 3 解碼器。',
    tts_process: {pool: {model_loaded: true}}});
  assert.match(supporting, /編碼完整參考音色/);
});