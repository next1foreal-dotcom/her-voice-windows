// Stateful sample-rate conversion. Weighted sample integration provides a
// small anti-alias filter when downsampling and preserves fractional carry.
export class StreamingResampler {
  constructor(inputRate, outputRate = 24000) {
    if (!(inputRate > 0 && outputRate > 0)) throw new RangeError('Invalid sample rate');
    this.ratio = inputRate / outputRate;
    this.remaining = this.ratio;
    this.sum = 0;
  }

  push(input) {
    // A fixed output buffer avoids per-sample growth/boxing on the audio thread.
    const out = new Float32Array(Math.ceil(input.length / this.ratio) + 1);
    let length = 0;
    for (const sample of input) {
      let available = 1;
      while (available > 1e-9) {
        const take = Math.min(available, this.remaining);
        this.sum += sample * take;
        this.remaining -= take;
        available -= take;
        if (this.remaining < 1e-9) {
          out[length++] = this.sum / this.ratio;
          this.remaining = this.ratio;
          this.sum = 0;
        }
      }
    }
    return out.subarray(0, length);
  }
}

export function floatToPCM16(samples) {
  const buffer = new ArrayBuffer(samples.length * 2);
  const view = new DataView(buffer);
  samples.forEach((sample, i) => {
    const clipped = Math.max(-1, Math.min(1, Number.isFinite(sample) ? sample : 0));
    view.setInt16(i * 2, Math.round(clipped * (clipped < 0 ? 32768 : 32767)), true);
  });
  return buffer;
}

export function pcm16ToFloat(bytes) {
  if (bytes.byteLength % 2) throw new RangeError('PCM16 byte count must be even');
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const result = new Float32Array(bytes.byteLength / 2);
  for (let i = 0; i < result.length; i++) result[i] = view.getInt16(i * 2, true) / 32768;
  return result;
}

export function rms(samples) {
  if (!samples.length) return 0;
  let sum = 0;
  for (const sample of samples) sum += sample * sample;
  return Math.sqrt(sum / samples.length);
}
