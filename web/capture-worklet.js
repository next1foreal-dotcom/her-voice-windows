import {StreamingResampler, floatToPCM16, rms} from './audio-dsp.js';

class MicrophoneCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.resampler = new StreamingResampler(sampleRate, 24000);
    this.frame = new Float32Array(960); // 40 ms at 24 kHz
    this.offset = 0;
  }

  process(inputs, outputs) {
    // The node is connected to keep the worklet alive, but never monitors mic.
    for (const output of outputs) for (const channel of output) channel.fill(0);
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    const converted = this.resampler.push(channel);
    for (const sample of converted) {
      this.frame[this.offset++] = sample;
      if (this.offset === this.frame.length) {
        const pcm = floatToPCM16(this.frame);
        this.port.postMessage({pcm, level: rms(this.frame)}, [pcm]);
        this.offset = 0;
      }
    }
    return true;
  }
}

registerProcessor('microphone-capture', MicrophoneCapture);
