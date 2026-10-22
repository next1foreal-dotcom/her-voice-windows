import {pcm16ToFloat, rms} from './audio-dsp.js';

export function encodeBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return btoa(binary);
}

function decodeBase64(value) {
  const binary = atob(value);
  return Uint8Array.from(binary, c => c.charCodeAt(0));
}

export class VoiceAudio {
  constructor({onFrame, onLevel, onStart, onIdle, onError, onSchedule}) {
    Object.assign(this, {onFrame, onLevel, onStart, onIdle, onError, onSchedule});
    this.context = null;
    this.stream = null;
    this.pendingStream = null;
    this.worklet = null;
    this.source = null;
    this.sources = new Set();
    this.timers = new Set();
    this.nextTime = 0;
    this.epoch = 0;
    this.enabled = false;
    this.capturePaused = false;
    this.started = new Set();
    this.captureEpoch = 0;
  }

  async open(outputDeviceId = '') {
    if (!this.context || this.context.state === 'closed') this.context = new AudioContext({latencyHint: 'interactive'});
    const context = this.context;
    await context.resume();
    if (context !== this.context || context.state === 'closed') return false;
    if (outputDeviceId) {
      if (typeof context.setSinkId !== 'function') throw new Error('這個瀏覽器不支援指定輸出裝置，請使用系統預設輸出。');
      await context.setSinkId(outputDeviceId);
    }
    return context === this.context && context.state !== 'closed';
  }

  async setOutput(deviceId) {
    if (!this.context) return;
    if (typeof this.context.setSinkId !== 'function') {
      if (deviceId) throw new Error('這個瀏覽器不支援指定輸出裝置，請使用 Windows 預設輸出。');
      return;
    }
    await this.context.setSinkId(deviceId || '');
  }

  async capture(deviceId = '') {
    this.stopCapture();
    const ticket = ++this.captureEpoch;
    const context = this.context;
    if (!globalThis.navigator?.mediaDevices?.getUserMedia) throw new Error('請使用可信任的 HTTPS 或 loopback 網址，以及新版 Edge 或 Chrome。');
    const permission = navigator.mediaDevices.getUserMedia({audio: {
      channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      ...(deviceId ? {deviceId: {exact: deviceId}} : {}),
    }});
    let timer, expired = false, stream;
    // Browsers may leave getUserMedia pending when a permission prompt is ignored.
    // A late grant after timeout must release its tracks, never reopen capture.
    permission.then(value => { if (expired) value.getTracks().forEach(track => track.stop()); }, () => {});
    try {
      stream = await Promise.race([permission, new Promise((_, reject) => {
        timer = setTimeout(() => {
          expired = true;
          reject(new Error('語音服務已就緒，但瀏覽器未完成麥克風啟用。請確認選定的輸入裝置與頁面權限，再點「開始對話」；模型會保持載入。'));
        }, 30_000);
      })]);
    } finally { clearTimeout(timer); }
    const stale = () => ticket !== this.captureEpoch || context !== this.context || !context || context.state === 'closed';
    if (stale()) {
      stream.getTracks().forEach(t => t.stop());
      return false;
    }
    // Permission or worklet loading can finish after pause(). Keep the desired
    // pause state, and let pause/stop also reach a stream awaiting its worklet.
    this.pendingStream = stream;
    stream.getAudioTracks().forEach(track => { track.enabled = !this.capturePaused; });
    try { await context.audioWorklet.addModule(new URL('./capture-worklet.js', import.meta.url).href); }
    catch (error) {
      if (this.pendingStream === stream) this.pendingStream = null;
      stream.getTracks().forEach(t => t.stop()); if (stale()) return false; throw error;
    }
    if (this.pendingStream === stream) this.pendingStream = null;
    if (stale()) { stream.getTracks().forEach(t => t.stop()); return false; }
    this.stream = stream;
    this.source = context.createMediaStreamSource(stream);
    this.worklet = new AudioWorkletNode(context, 'microphone-capture');
    this.worklet.port.onmessage = ({data}) => {
      this.onLevel?.(this.enabled ? data.level : 0, undefined);
      if (this.enabled) this.onFrame?.(data.pcm);
    };
    this.stream.getAudioTracks().forEach(track => {
      track.enabled = !this.capturePaused;
      track.addEventListener('ended', () => { if (ticket === this.captureEpoch) this.onError?.(new Error('麥克風已中斷連線，請重新開始。')); }, {once: true});
    });
    this.source.connect(this.worklet);
    this.worklet.connect(this.context.destination);
    this.enabled = !this.capturePaused;
    return true;
  }

  pauseCapture(paused) {
    this.capturePaused = paused;
    this.enabled = !paused;
    for (const stream of new Set([this.stream, this.pendingStream])) {
      stream?.getAudioTracks().forEach(track => { track.enabled = !paused; });
    }
    if (paused) this.onLevel?.(0, undefined);
  }

  enqueue(id, base64) {
    if (!this.context || this.context.state === 'closed') return;
    const values = pcm16ToFloat(decodeBase64(base64));
    if (!values.length) return;
    const context = this.context;
    if (this.nextTime - context.currentTime > 20) throw new Error('語音播放佇列過長，已停止這輪回覆。');
    const audioBuffer = context.createBuffer(1, values.length, 24000);
    audioBuffer.copyToChannel(values, 0);
    const node = context.createBufferSource();
    node.buffer = audioBuffer;
    node.connect(context.destination);
    // Preserve a queued boundary even when transport jitter uses some lead.
    // Reapplying startup lead to every packet inserts artificial silence.
    const when = this.nextTime > context.currentTime ? this.nextTime : context.currentTime + 0.035;
    const gapMs = this.nextTime > 0 ? Math.max(0, when - this.nextTime) * 1000 : 0;
    this.nextTime = when + audioBuffer.duration;
    this.onSchedule?.({id, durationMs: audioBuffer.duration * 1000, queueDelayMs: (when - context.currentTime) * 1000, gapMs});
    const epoch = this.epoch;
    this.sources.add(node);
    node.onended = () => {
      node.disconnect();
      this.sources.delete(node);
      if (epoch === this.epoch && this.sources.size === 0) {
        this.onLevel?.(undefined, 0);
        this.onIdle?.();
      }
    };
    // Keep speaking state until onended, even if the server already sent done.
    this.onStart?.(id, false);
    const timer = setTimeout(() => {
      this.timers.delete(timer);
      if (epoch !== this.epoch) return;
      this.onLevel?.(undefined, rms(values));
      if (!this.started.has(id)) {
        this.started.add(id);
        this.onStart?.(id, true);
      }
    }, Math.max(0, (when - context.currentTime) * 1000));
    this.timers.add(timer);
    node.start(when);
  }

  clear() {
    this.epoch++;
    for (const timer of this.timers) clearTimeout(timer);
    this.timers.clear();
    for (const node of this.sources) {
      node.onended = null;
      try { node.stop(); } catch { /* already ended */ }
      node.disconnect();
    }
    this.sources.clear();
    this.started.clear();
    this.nextTime = 0;
    this.onLevel?.(undefined, 0);
  }

  async stopCapture() {
    this.captureEpoch++;
    this.enabled = false;
    if (this.worklet) {
      this.worklet.port.onmessage = null;
      this.worklet.disconnect();
      this.worklet = null;
    }
    this.source?.disconnect();
    this.source = null;
    for (const stream of new Set([this.stream, this.pendingStream])) stream?.getTracks().forEach(track => track.stop());
    this.stream = null;
    this.pendingStream = null;
  }

  async close() {
    this.clear();
    // Detach before any await, so an old close cannot close a newly opened context.
    this.stopCapture();
    this.capturePaused = false;
    const context = this.context;
    this.context = null;
    if (context && context.state !== 'closed') await context.close();
  }
}
