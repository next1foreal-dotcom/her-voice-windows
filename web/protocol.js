// Pure protocol state: shared by the real client, demo, and Node tests.
export const SNAPSHOT_EXTENSION = 'speech_to_speech.input_audio_transcription.snapshot';

export function sessionUpdate({paused = false, bargeIn = true} = {}) {
  return {
    type: 'session.update',
    session: {
      type: 'realtime',
      output_modalities: ['audio'],
      audio: {
        input: {
          format: {type: 'audio/pcm', rate: 24000},
          // Explicit null clears any per-session language override upstream.
          // Omitting this field or sending "auto" does not have that meaning.
          transcription: {language: null},
          turn_detection: {type: 'server_vad', create_response: !paused, interrupt_response: bargeIn},
        },
        // Voice identity belongs to the server's saved reference profile.
        // Reconnects and pause/settings updates must never replace it with a
        // built-in speaker (or leak a model-specific voice field across backends).
        output: {format: {type: 'audio/pcm', rate: 24000}},
      },
      extensions: [SNAPSHOT_EXTENSION],
    },
  };
}

export class ConversationState {
  constructor(mode = 'live') {
    this.snapshot = {
      mode, connection: 'disconnected', state: 'idle',
      caption: {user: '', assistant: ''}, messages: [],
      metrics: {firstAudioMs: null},
      settings: {micDeviceId: '', outputDeviceId: '', captions: true, bargeIn: true},
      devices: [], paused: false, error: '', health: {},
    };
    this.currentResponseId = null;
    this.generationDone = true;
    this.playbackActive = false;
    this.cancelled = new Set();
    this.snapshots = new Set();
    this.inputItemId = null;
  }

  message(id, role) {
    let item = this.snapshot.messages.find(m => m.id === id);
    if (!item) {
      item = {id, role, text: '', status: 'streaming'};
      this.snapshot.messages.push(item);
      // UI is intentionally bounded; a new session resets upstream history too.
      if (this.snapshot.messages.length > 100) this.snapshot.messages.shift();
    }
    return item;
  }

  addUser(text, id) {
    Object.assign(this.message(id, 'user'), {text, status: 'complete'});
    this.snapshot.caption.user = text;
    this.snapshot.caption.assistant = '';
    this.snapshot.state = 'thinking';
    this.snapshot.error = '';
  }

  interrupt() {
    const id = this.currentResponseId;
    if (id) {
      this.cancelled.add(id);
      if (this.cancelled.size > 200) this.cancelled.delete(this.cancelled.values().next().value);
      const message = this.snapshot.messages.find(m => m.id === id);
      if (message) message.status = 'interrupted';
    }
    this.currentResponseId = null;
    this.generationDone = true;
    this.playbackActive = false;
    this.snapshot.state = this.snapshot.paused ? 'paused' : 'interrupted';
    return id;
  }

  setPaused(paused) {
    this.snapshot.paused = paused;
    const id = paused ? this.interrupt() : null;
    this.snapshot.state = paused ? 'paused' : 'listening';
    return id;
  }

  audioStarted(id) {
    if (this.snapshot.paused || this.cancelled.has(id) || id !== this.currentResponseId) return;
    this.playbackActive = true;
    this.snapshot.state = 'speaking';
  }

  audioIdle() {
    this.playbackActive = false;
    if (this.generationDone) this.currentResponseId = null;
    if (!this.snapshot.paused && this.snapshot.connection === 'connected') {
      this.snapshot.state = this.generationDone ? 'listening' : 'thinking';
    }
  }

  handle(event) {
    const result = {};
    const s = this.snapshot;
    const kind = event.type;
    if (kind === 'session.updated') {
      result.configured = true;
      if (!s.paused && s.state === 'idle') s.state = 'listening';
    } else if (kind === 'input_audio_buffer.speech_started') {
      this.inputItemId = event.item_id;
      if (s.settings.bargeIn && (this.currentResponseId || this.playbackActive)) {
        result.cancelId = this.interrupt();
        result.clearAudio = true;
      }
      if (!s.paused) {
        s.state = 'listening';
        s.caption.user = '';
      }
    } else if (kind === 'input_audio_buffer.speech_stopped') {
      if (!s.paused) s.state = 'thinking';
      result.userStopped = true;
    } else if (kind === SNAPSHOT_EXTENSION || kind.startsWith('conversation.item.input_audio_transcription.')) {
      const id = event.item_id || this.inputItemId;
      if (!id) return result;
      const message = this.message(id, 'user');
      if (!this.inputItemId) this.inputItemId = id;
      if (kind === SNAPSHOT_EXTENSION) {
        this.snapshots.add(id);
        message.text = event.transcript ?? '';
      } else if (kind.endsWith('.delta')) {
        if (!this.snapshots.has(id)) message.text += event.delta ?? '';
      } else if (kind.endsWith('.completed')) {
        message.text = event.transcript ?? '';
        message.status = 'complete';
      } else if (kind.endsWith('.failed')) {
        message.status = 'failed';
        s.error = '这段语音未能辨识，请重试。';
      }
      if (this.snapshots.size > 200) this.snapshots.delete(this.snapshots.values().next().value);
      if (id === this.inputItemId) s.caption.user = message.text;
    } else if (kind === 'response.created') {
      const id = event.response?.id;
      if (!id) return result;
      this.currentResponseId = id;
      this.generationDone = false;
      if (s.paused) {
        result.cancelId = this.interrupt();
        result.clearAudio = true;
        return result;
      }
      s.caption.assistant = '';
      s.state = 'thinking';
      this.message(id, 'assistant');
    } else if (kind === 'response.output_audio.delta' || kind === 'response.output_audio_transcript.delta' || kind === 'response.output_audio_transcript.done') {
      const id = event.response_id;
      if (s.paused || this.cancelled.has(id) || id !== this.currentResponseId) return result;
      if (kind === 'response.output_audio.delta') {
        result.audio = {id, base64: event.delta};
      } else {
        const message = this.message(id, 'assistant');
        if (kind.endsWith('.delta')) message.text += event.delta ?? '';
        else message.text = event.transcript ?? message.text;
        s.caption.assistant = message.text;
      }
    } else if (kind === 'response.done') {
      const response = event.response || {};
      const id = response.id;
      if (this.cancelled.has(id) || id !== this.currentResponseId) return result;
      this.generationDone = true;
      this.message(id, 'assistant').status = response.status === 'completed' ? 'complete' : response.status;
      if (response.status === 'cancelled' || response.status === 'failed') {
        result.clearAudio = true;
        this.playbackActive = false;
        if (response.status === 'failed') s.error = '回复未完成；请检查模型、API 或连接状态。';
      }
      if (response.status === 'incomplete') s.error = '回复达到长度限制或未完整生成；可请我接着说。';
      if (!this.playbackActive && !s.paused) s.state = 'listening';
      if (!this.playbackActive) this.currentResponseId = null;
    } else if (kind === 'error') {
      s.error = typeof event.error?.message === 'string' ? event.error.message.slice(0, 350) : '语音服务发生错误。';
      if (!this.playbackActive && !s.paused) s.state = 'listening';
    }
    return result;
  }
}

// Durations only. No audio, transcript, provider URL or credentials are retained.
const KEY = 'speech_to_speech.turn_latency';
const SERVER_FIELDS = ['stt_s', 'llm_ttft_s', 'llm_s', 'tts_ttfa_s', 'e2e_s', 'vad_decision_s', 'hold_s'];
const milliseconds = value => Number.isFinite(value) && value >= 0 ? Math.round(value * 1000) : null;
export function serverTimings(metadata) {
  const raw = metadata?.[KEY];
  if (typeof raw !== 'string' || raw.length > 2048) return null;
  let value;
  try { value = JSON.parse(raw); } catch { return null; }
  if (!value || ![1, 2].includes(value.version)) return null;
  const result = {};
  for (const name of SERVER_FIELDS) result[name.replace(/_s$/, 'Ms')] = milliseconds(value[name]);
  result.smartStatus = ['complete', 'incomplete', 'failed', 'disabled'].includes(value.smart_status) ? value.smart_status : null;
  return result;
}

export class TurnLatency {
  constructor(now = () => performance.now()) {
    this.now = now;
    this.records = [];
    this.begin(null);
  }
  begin(source, at = this.now()) {
    this.source = source;
    this.anchor = source ? at : null;
    this.id = null;
    this.packetAt = null;
    this.metrics = {source, firstAudioMs: null, firstPacketMs: null, firstTranscriptMs: null,
      playbackQueueMs: null, audioMs: 0, gapCount: 0, gapMs: 0, maxQueueMs: 0, server: null, status: null};
  }
  cancel() { this.id = null; this.anchor = null; }
  observe(event, effect = {}) {
    if (event.type === 'input_audio_buffer.speech_started') this.cancel();
    if (effect.userStopped) this.begin('speech');
    if (event.type === 'response.created' && !effect.clearAudio) {
      if (event.response?.id === this.id) return;
      if (this.id !== null || this.anchor === null) this.begin(null);
      this.id = event.response?.id ?? null;
    }
    const id = event.response_id ?? event.response?.id;
    if (!this.id || id !== this.id) return;
    if (event.type === 'response.output_audio.delta' && effect.audio && this.packetAt === null) {
      this.packetAt = this.now();
      this.metrics.firstPacketMs = this.elapsed();
    }
    if (event.type === 'response.output_audio_transcript.delta' && event.delta?.trim() && this.metrics.firstTranscriptMs === null) {
      this.metrics.firstTranscriptMs = this.elapsed();
    }
    if (event.type === 'response.done' && this.metrics.status === null) {
      this.metrics.server = serverTimings(event.response?.metadata);
      this.metrics.status = ['completed', 'cancelled', 'failed', 'incomplete'].includes(event.response?.status) ? event.response.status : 'unknown';
      this.records.push(this.metrics);
      if (this.records.length > 20) this.records.shift();
    }
    if (effect.clearAudio) this.cancel();
  }
  elapsed() { return this.anchor === null ? null : Math.max(0, Math.round(this.now() - this.anchor)); }
  playback(id) {
    if (id !== this.id || this.metrics.firstAudioMs !== null) return;
    this.metrics.firstAudioMs = this.elapsed();
    this.metrics.playbackQueueMs = this.packetAt === null ? null : Math.max(0, Math.round(this.now() - this.packetAt));
  }
  scheduled({id, durationMs, queueDelayMs, gapMs}) {
    if (id !== this.id) return;
    this.metrics.audioMs += durationMs;
    this.metrics.maxQueueMs = Math.max(this.metrics.maxQueueMs, queueDelayMs);
    if (gapMs > 2) { this.metrics.gapCount++; this.metrics.gapMs += gapMs; }
  }
  snapshot() { return structuredClone({current: this.metrics, recent: this.records}); }
}
