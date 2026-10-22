import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {StreamingResampler, floatToPCM16, rms} from '../web/audio-dsp.js';

const source = (await readFile(new URL('../web/capture-worklet.js', import.meta.url), 'utf8'))
  .replace(/^import .*?;\r?\n/, '');

for (const rate of [16000, 24000, 44100, 48000]) {
  test(`actual capture worklet preserves PCM, 40 ms framing and silent monitoring at ${rate} Hz`, () => {
    let Processor;
    const emitted = [];
    const context = vm.createContext({sampleRate: rate, StreamingResampler, floatToPCM16, rms,
      AudioWorkletProcessor: class { port = {postMessage: (data, transfers) => {
        assert.equal(transfers[0], data.pcm);
        emitted.push(data);
      }}; },
      registerProcessor: (name, value) => { assert.equal(name, 'microphone-capture'); Processor = value; },
    });
    vm.runInContext(source, context);
    const worklet = new Processor();
    const input = Float32Array.from({length: rate}, (_, i) => 0.4 * Math.sin(i * 0.071));
    for (let offset = 0; offset < input.length; offset += 128) {
      const output = new Float32Array(128).fill(1);
      assert.equal(worklet.process([[input.subarray(offset, offset + 128)]], [[output]]), true);
      assert.ok(output.every(value => value === 0), 'microphone must not monitor to the speaker');
    }
    assert.equal(emitted.length, 25);
    const expected = new StreamingResampler(rate).push(input);
    for (let index = 0; index < emitted.length; index++) {
      const frame = expected.subarray(index * 960, (index + 1) * 960);
      assert.equal(emitted[index].pcm.byteLength, 1920);
      assert.deepEqual(new Uint8Array(emitted[index].pcm), new Uint8Array(floatToPCM16(frame)));
      assert.equal(emitted[index].level, rms(frame));
    }
    assert.equal(worklet.process([[]], [[new Float32Array(128)]]), true);
    assert.equal(emitted.length, 25);
  });
}