import {ConversationState, sessionUpdate, TurnLatency} from './protocol.js';
import {VoiceAudio, encodeBase64} from './audio.js';

const abortError = () => Object.assign(new Error('Voice operation was cancelled.'), {name: 'AbortError'});
const asError = value => value instanceof Error ? value : new Error(String(value));
function freeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}
function settingsPatch(update) {
  if (!update || typeof update !== 'object' || Array.isArray(update)) throw new TypeError('Voice settings must be an object.');
  const result = {};
  for (const [key, value] of Object.entries(update)) {
    const expected = ['captions', 'bargeIn'].includes(key) ? 'boolean'
      : ['micDeviceId', 'outputDeviceId'].includes(key) ? 'string' : null;
    if (!expected || typeof value !== expected) throw new TypeError(`Invalid voice setting: ${key}.`);
    result[key] = value;
  }
  return result;
}
function wait(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(abortError()); return; }
    const timer = setTimeout(() => { signal?.removeEventListener('abort', cancelled); resolve(); }, ms);
    function cancelled() { clearTimeout(timer); signal.removeEventListener('abort', cancelled); reject(abortError()); }
    signal?.addEventListener('abort', cancelled, {once: true});
  });
}

/**
 * DOM-free, per-instance voice session. No UI, storage or product identity.
 * baseUrl is a directory prefix of a same-origin backend proxy, not an LLM URL.
 * Browsers always validate against their real location; location may only be
 * injected when no global location exists (Node/test adapters).
 * fetch, WebSocket, audioFactory and mediaDevices support dependency injection.
 * State snapshots are detached and frozen. Listener methods return unsubscribe.
 * Commands reject errors after publishing state.error and onError(Error).
 * Cancelled/stale connect and session operations resolve false instead.
 */
export class VoiceClient {
  constructor(options = {}) {
    const currentLocation = globalThis.location?.href ?? options.location?.href ?? options.location;
    if (!currentLocation) throw new TypeError('VoiceClient requires the current page location.');
    const page = new URL(String(currentLocation));
    const base = new URL(options.baseUrl ?? '/', page);
    if (!['http:', 'https:'].includes(base.protocol) || base.origin !== page.origin || base.username || base.password) {
      throw new TypeError('VoiceClient baseUrl must use the current page origin. Use your application backend as a same-origin proxy.');
    }
    if (base.search || base.hash) throw new TypeError('VoiceClient baseUrl must be a directory URL without a query or fragment.');
    if (!base.pathname.endsWith('/')) base.pathname += '/';
    const mode = options.mode ?? 'live';
    if (!['live', 'demo'].includes(mode)) throw new TypeError('Voice mode must be live or demo.');
    this._baseUrl = base;
    this._fetch = options.fetch ?? globalThis.fetch?.bind(globalThis);
    this._WebSocket = options.WebSocket ?? globalThis.WebSocket;
    this._mediaDevices = options.mediaDevices ?? globalThis.navigator?.mediaDevices;
    if (typeof this._fetch !== 'function' || typeof this._WebSocket !== 'function') throw new TypeError('VoiceClient needs fetch and WebSocket.');
    this._model = new ConversationState(mode);
    this._timing = new TurnLatency();
    Object.assign(this._model.snapshot.settings, settingsPatch(options.settings ?? {}));
    this._stateListeners = new Set(); this._levelListeners = new Set(); this._errorListeners = new Set();
    this._levels = {input: 0, output: 0};
    this._socket = null; this._token = ''; this._epoch = 0; this._operation = null;
    this._connectionAbort = null; this._bootstrapAbort = null; this._configuredWaiter = null;
    this._itemWaiters = new Map();
    this._initialized = false; this._initializePromise = null; this._connectPromise = null;
    this._destroyPromise = null; this._destroyed = false; this._stoppedAt = null; this._lastReportedError = null;
    this._voiceAdminAbort = null; this._voiceAdminPromise = null;
    this._model.snapshot.voiceAdmin = {loading: false, busy: false, config: null, voices: [], error: '', message: ''};
    const audioFactory = options.audioFactory ?? (callbacks => new VoiceAudio(callbacks));
    this._audio = audioFactory({
      onFrame: pcm => {
        if (this._destroyed || !this._isOpen() || this._model.snapshot.connection !== 'connected' || this._model.snapshot.paused || this._model.snapshot.mode !== 'live') return;
        if (this._socket.bufferedAmount > 256_000) {
          this._report(new Error('網路傳送落後，已結束這次連線；請檢查服務後重新開始。'));
          this._disconnect(false).catch(error => this._report(error));
          return;
        }
        this._send({type: 'input_audio_buffer.append', audio: encodeBase64(pcm)});
      },
      onLevel: (input, output) => this._setLevels(input, output),
      onStart: (id, actualOnset) => {
        if (this._destroyed) return;
        const previousState = this._model.snapshot.state;
        this._model.audioStarted(id);
        if (actualOnset) this._timing.playback(id);
        if (actualOnset && this._stoppedAt !== null && id === this._model.currentResponseId && !this._model.cancelled.has(id)) {
          this._model.snapshot.metrics.firstAudioMs = Math.round(performance.now() - this._stoppedAt);
          this._stoppedAt = null;
        }
        if (actualOnset || previousState !== this._model.snapshot.state) this._emit();
      },
      onSchedule: detail => this._timing.scheduled(detail),
      onOutputFallback: ({requestedDeviceId, deviceId}) => {
        if (this._destroyed || this._model.snapshot.settings.outputDeviceId !== requestedDeviceId) return;
        this._model.snapshot.settings.outputDeviceId = deviceId;
        this._emit();
      },
      onIdle: () => { if (!this._destroyed) { this._model.audioIdle(); this._emit(); } },
      onError: error => {
        if (this._destroyed) return;
        this._report(error);
        this._disconnect(false).catch(failure => this._report(failure));
      },
    });
    this._deviceChange = () => {
      this._refreshDevices().then(async () => {
        if (this._destroyed) return;
        const output = this._model.snapshot.settings.outputDeviceId;
        if (output) await this._audio.setOutput(output);
        this._emit();
      }).catch(error => this._report(error));
    };
    this._mediaDevices?.addEventListener?.('devicechange', this._deviceChange);
  }

  get state() { this._model.snapshot.metrics = this._timing.metrics; return freeze(structuredClone(this._model.snapshot)); }
  get diagnostics() { return freeze(this._timing.snapshot()); }
  subscribe(listener) { return this._subscribe(this._stateListeners, listener, this.state); }
  onLevels(listener) { return this._subscribe(this._levelListeners, listener, Object.freeze({...this._levels})); }
  onError(listener) { return this._subscribe(this._errorListeners, listener); }
  _subscribe(listeners, listener, initial) {
    this._assertAlive();
    if (typeof listener !== 'function') throw new TypeError('Voice listener must be a function.');
    listeners.add(listener);
    if (initial !== undefined) listener(initial);
    return () => listeners.delete(listener);
  }
  _notify(listeners, value) {
    if (this._destroyed) return;
    for (const listener of [...listeners]) {
      if (this._destroyed) break;
      try { listener(value); }
      catch (error) { queueMicrotask(() => { throw error; }); } // Host errors must not corrupt transport state.
    }
  }
  _emit() { if (!this._destroyed) this._notify(this._stateListeners, this.state); }
  _assertAlive() { if (this._destroyed) throw new Error('VoiceClient has been destroyed.'); }
  _current(epoch) { return !this._destroyed && epoch === this._epoch; }
  _isOpen() { return this._socket?.readyState === this._WebSocket.OPEN; }
  _setLevels(input, output) {
    if (this._destroyed) return;
    for (const [key, value] of [['input', input], ['output', output]]) {
      if (value !== undefined) this._levels[key] = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;
    }
    this._notify(this._levelListeners, Object.freeze({...this._levels}));
  }
  _report(value) {
    const error = asError(value);
    if (!this._destroyed) {
      const alreadyReported = this._lastReportedError === error;
      this._lastReportedError = error;
      this._model.snapshot.error = error.message;
      this._emit();
      if (!alreadyReported) this._notify(this._errorListeners, error);
    }
    return error;
  }
  _send(event) {
    if (!this._isOpen()) return false;
    this._socket.send(JSON.stringify(event));
    return true;
  }
  _cancelRemote(id) { if (id) this._send({type: 'response.cancel', response_id: id}); }
  async _api(path, {method = 'GET', signal, json, form} = {}) {
    const headers = {'X-Voice-Token': this._token};
    if (json !== undefined) headers['Content-Type'] = 'application/json';
    const response = await this._fetch(new URL(path, this._baseUrl).href, {
      method, signal, cache: 'no-store', credentials: 'same-origin', redirect: 'error',
      headers, ...(json !== undefined ? {body: JSON.stringify(json)} : form ? {body: form} : {}),
    });
    let body;
    try { body = await response.json(); }
    catch { throw new Error(`本機服務回傳無效 JSON（HTTP ${response.status}）。`); }
    if (!response.ok) throw new Error(body.error || `本機服務錯誤：HTTP ${response.status}`);
    return body;
  }

  /** Load local model/voice settings without starting any model or provider. */
  async loadVoiceSettings() {
    this._assertAlive();
    if (this._voiceAdminPromise) return this._voiceAdminPromise;
    const controller = new AbortController(); this._voiceAdminAbort = controller;
    Object.assign(this._model.snapshot.voiceAdmin, {loading: true, error: ''}); this._emit();
    const pending = (async () => {
      try {
        await this.initialize();
        const [config, data] = await Promise.all([
          this._api('api/tts', {signal: controller.signal}),
          this._api('api/voices', {signal: controller.signal}),
        ]);
        if (this._destroyed || controller.signal.aborted) return false;
        if (!['drama3', 'qwen3'].includes(config.backend) || !Array.isArray(data.voices)) throw new Error('本机音色设置数据不完整。');
        Object.assign(this._model.snapshot.voiceAdmin, {config, voices: data.voices,
          error: typeof config.error === 'string' ? config.error : ''});
        return true;
      } catch (error) {
        if (!this._destroyed && !controller.signal.aborted) {
          this._model.snapshot.voiceAdmin.error = asError(error).message;
          throw this._report(error);
        }
        return false;
      } finally {
        if (this._voiceAdminAbort === controller) this._voiceAdminAbort = null;
        if (!this._destroyed) { this._model.snapshot.voiceAdmin.loading = false; this._emit(); }
      }
    })();
    this._voiceAdminPromise = pending;
    try { return await pending; }
    finally { if (this._voiceAdminPromise === pending) this._voiceAdminPromise = null; }
  }

  /** Import a WAV once; this does not select it or run speech inference. */
  async importVoice({name, transcript, audio}) {
    this._assertAlive();
    if (this._voiceAdminPromise) return false;
    if (typeof name !== 'string' || !name.trim() || typeof transcript !== 'string' || !transcript.trim()) {
      throw this._voiceAdminError('请填写音色名称和录音对应的完整文字。');
    }
    if (!(audio instanceof Blob) || !audio.size || !/\.wav$/i.test(audio.name || '')) {
      throw this._voiceAdminError('请选择一段 WAV 参考录音。');
    }
    if (name.trim().length > 80 || transcript.trim().length > 2000) throw this._voiceAdminError('音色名称最多 80 字，录音文字最多 2000 字。');
    if (audio.size > 12 * 1024 * 1024) throw this._voiceAdminError('参考录音不能超过 12 MiB。');
    const controller = new AbortController(); this._voiceAdminAbort = controller;
    Object.assign(this._model.snapshot.voiceAdmin, {busy: true, error: '', message: '正在导入参考音色…'}); this._emit();
    const pending = (async () => {
      try {
        await this.initialize();
        const form = new FormData(); form.append('name', name.trim()); form.append('transcript', transcript.trim()); form.append('audio', audio, audio.name);
        const result = await this._api('api/voices', {method: 'POST', form, signal: controller.signal});
        if (this._destroyed || controller.signal.aborted) return false;
        if (!result.voice || typeof result.voice.id !== 'string') throw new Error('本机服务没有返回有效的音色。');
        const admin = this._model.snapshot.voiceAdmin;
        admin.voices = [...admin.voices.filter(voice => voice.id !== result.voice.id), result.voice];
        admin.importedVoiceId = result.voice.id;
        admin.message = '音色已保存。选中后点击“应用声音设置”生效。';
        return result.voice;
      } catch (error) {
        if (!this._destroyed && !controller.signal.aborted) {
          Object.assign(this._model.snapshot.voiceAdmin, {error: asError(error).message, message: ''});
          throw this._report(error);
        }
        return false;
      } finally {
        if (this._voiceAdminAbort === controller) this._voiceAdminAbort = null;
        if (!this._destroyed) { this._model.snapshot.voiceAdmin.busy = false; this._emit(); }
      }
    })();
    this._voiceAdminPromise = pending;
    try { return await pending; }
    finally { if (this._voiceAdminPromise === pending) this._voiceAdminPromise = null; }
  }

  /** Explicit switch: release capture/playback first, retain completed history,
   * then ask the owned server to unload the old engine. Never auto-reconnect. */
  async applyVoiceSettings({backend, voice_id}) {
    this._assertAlive();
    if (this._voiceAdminPromise) return false;
    if (!['drama3', 'qwen3'].includes(backend) || typeof voice_id !== 'string' || !voice_id) {
      throw this._voiceAdminError('请选择语音后端和已导入的参考音色。');
    }
    const controller = new AbortController(); this._voiceAdminAbort = controller;
    Object.assign(this._model.snapshot.voiceAdmin, {busy: true, error: '', message: '正在检查并应用声音设置…'}); this._emit();
    const pending = (async () => {
      try {
        await this.initialize();
        const current = await this._api('api/tts', {signal: controller.signal});
        if (this._destroyed || controller.signal.aborted) return false;
        if (!current.error && current.backend === backend && current.voice_id === voice_id) {
          Object.assign(this._model.snapshot.voiceAdmin, {config: current, message: '声音设置未变，当前对话和已加载模型继续保留。'});
          return true;
        }
        await this.disconnect();
        if (this._destroyed || controller.signal.aborted) return false;
        const config = await this._api('api/tts', {method: 'PUT', json: {backend, voice_id}, signal: controller.signal});
        if (this._destroyed || controller.signal.aborted) return false;
        if (!['drama3', 'qwen3'].includes(config.backend)) throw new Error('本机服务没有确认声音设置。');
        Object.assign(this._model.snapshot.voiceAdmin, {config, message: '声音设置已应用。点击“开始对话”加载所选后端；已完成的对话仍然保留。'});
        if (this._model.snapshot.health?.engine) {
          Object.assign(this._model.snapshot.health.engine, {state: 'stopped', ready: false, running: false});
        }
        return true;
      } catch (error) {
        if (!this._destroyed && !controller.signal.aborted) {
          Object.assign(this._model.snapshot.voiceAdmin, {error: asError(error).message, message: ''});
          throw this._report(error);
        }
        return false;
      } finally {
        if (this._voiceAdminAbort === controller) this._voiceAdminAbort = null;
        if (!this._destroyed) { this._model.snapshot.voiceAdmin.busy = false; this._emit(); }
      }
    })();
    this._voiceAdminPromise = pending;
    try { return await pending; }
    finally { if (this._voiceAdminPromise === pending) this._voiceAdminPromise = null; }
  }

  _voiceAdminError(message) {
    Object.assign(this._model.snapshot.voiceAdmin, {error: message, message: ''});
    return this._report(new Error(message));
  }
  async _refreshDevices() {
    if (!this._mediaDevices?.enumerateDevices || this._destroyed) return;
    try {
      const devices = await this._mediaDevices.enumerateDevices();
      if (!this._destroyed) this._model.snapshot.devices = devices.map(({deviceId, groupId, kind, label}) => ({deviceId, groupId, kind, label}));
    } catch { /* Enumeration never requests capture permission. */ }
  }

  async initialize() {
    this._assertAlive();
    if (this._initialized) return this.state;
    if (this._initializePromise) return this._initializePromise;
    const controller = new AbortController();
    this._bootstrapAbort = controller;
    const pending = (async () => {
      try {
        const data = await this._api('api/bootstrap', {signal: controller.signal});
        if (this._destroyed || controller.signal.aborted) return this.state;
        if (typeof data.token !== 'string' || !data.health || typeof data.health !== 'object') throw new Error('本機服務的初始化資料不完整。');
        this._token = data.token; this._model.snapshot.health = data.health;
        if (data.health.demo_only) this._model.snapshot.mode = 'demo';
        await this._refreshDevices();
        if (!this._destroyed && !controller.signal.aborted) { this._initialized = true; this._emit(); }
        return this.state;
      } catch (error) {
        if (!this._destroyed && !controller.signal.aborted) this._report(error);
        throw error;
      } finally { if (this._bootstrapAbort === controller) this._bootstrapAbort = null; }
    })();
    this._initializePromise = pending;
    try { return await pending; }
    finally { if (this._initializePromise === pending) this._initializePromise = null; }
  }
  _beginCycle() {
    this._timing.cancel();
    this._epoch++; this._connectionAbort?.abort(); this._connectionAbort = new AbortController(); this._lastReportedError = null;
    return this._epoch;
  }
  async refreshHealth() {
    this._assertAlive();
    await this.initialize();
    const health = await this._api('api/status');
    if (this._destroyed) return false;
    this._model.snapshot.health = health;
    this._emit();
    return true;
  }
  async _prepareEngine(epoch) {
    const initial = this._model.snapshot.health;
    const ready = typeof initial.provider_ready === 'boolean' ? initial.provider_ready : initial.api_key_configured;
    if (!ready) throw new Error('請先設定語言模型／Agent 接口，再重新啟動本機服務。可切換「協定演練」測試介面。');
    const signal = this._connectionAbort.signal;
    let health = await this._api('api/status', {signal});
    if (!this._current(epoch)) return;
    if (!['ready', 'starting'].includes(health.engine.state)) await this._api('api/engine/start', {method: 'POST', signal});
    const preparationStarted = performance.now();
    const deadline = preparationStarted + 2_800_000;
    const drainDeadline = performance.now() + 45_000;
    while (this._current(epoch)) {
      health = await this._api('api/status', {signal});
      if (!this._current(epoch)) return;
      if (health.engine.state === 'starting') health.engine.startup_wait_seconds = (performance.now() - preparationStarted) / 1000;
      this._model.snapshot.health = health; this._emit();
      if (health.engine.ready && health.engine.pool?.in_use === 0 && health.engine.pool?.units?.some(unit => unit.state === 'idle')) return;
      if (health.engine.pool?.units?.some(unit => unit.state === 'stuck')) throw new Error('上一工作階段未能釋放。請結束啟動器，再重新啟動語音引擎。');
      if (health.engine.ready && performance.now() > drainDeadline) throw new Error('上一工作階段仍在釋放資源；請稍後重試，持續無法恢復時重啟啟動器。');
      if (['failed', 'stopped'].includes(health.engine.state)) throw new Error(health.engine.detail || '語音模型啟動失敗，請執行 doctor.py 檢查。');
      if (performance.now() > deadline) throw new Error('語音服務啟動逾時，請查看本機引擎狀態與日誌。');
      await wait(1000, signal);
    }
  }
  _rejectItems(error) {
    for (const waiter of [...this._itemWaiters.values()]) waiter.reject(error);
    this._itemWaiters.clear();
  }
  async _openSocket(epoch) {
    const url = new URL('ws/realtime', this._baseUrl);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    url.searchParams.set('token', this._token); url.searchParams.set('mode', this._model.snapshot.mode);
    const socket = new this._WebSocket(url.href);
    this._socket = socket;
    await new Promise((resolve, reject) => {
      let settled = false;
      const finish = error => {
        if (settled) return;
        settled = true; clearTimeout(timeout);
        if (this._configuredWaiter === waiter) this._configuredWaiter = null;
        if (error) reject(error); else resolve();
      };
      const waiter = {reject: error => finish(error)};
      const timeout = setTimeout(() => finish(new Error('語音連線逾時，請重新開始。')), 12_000);
      this._configuredWaiter = waiter;
      const current = () => this._current(epoch) && socket === this._socket;
      socket.onopen = () => {
        if (!current()) { socket.close(); return; }
        socket.send(JSON.stringify(sessionUpdate({paused: false, bargeIn: this._model.snapshot.settings.bargeIn})));
      };
      socket.onmessage = ({data}) => {
        if (!current()) return;
        try {
          const event = JSON.parse(data);
          if (!event || typeof event !== 'object' || Array.isArray(event) || typeof event.type !== 'string') throw new Error('語音服務傳回無效事件。');
          if (event.type === 'conversation.item.created') this._itemWaiters.get(event.item?.id)?.resolve();
          if (event.type === 'error') {
            const error = new Error(typeof event.error?.message === 'string' ? event.error.message.slice(0, 350) : '語音服務發生錯誤。');
            this._rejectItems(error);
            if (!settled) { finish(error); return; }
            this._report(error);
          }
          const effect = this._model.handle(event);
          this._timing.observe(event, effect);
          if (effect.configured) finish();
          if (effect.clearAudio) this._audio.clear();
          if (effect.cancelId) this._cancelRemote(effect.cancelId);
          if (effect.userStopped) { this._stoppedAt = performance.now(); this._model.snapshot.metrics.firstAudioMs = null; }
          if (effect.audio) this._audio.enqueue(effect.audio.id, effect.audio.base64);
          // Audio has a separate level channel; publish state at onset/idle, not twice per PCM block.
          if (!effect.audio) this._emit();
        } catch (error) {
          if (!settled) finish(error);
          else { this._report(error); this.interrupt().catch(() => {}); }
        }
      };
      socket.onerror = () => {
        const error = new Error('無法連接語音服務；請確認模型載入完成。');
        if (!settled) finish(error); else if (current()) this._unexpectedClose(error);
      };
      socket.onclose = () => {
        finish(new Error('語音連線已關閉。'));
        if (current()) this._unexpectedClose(new Error('語音服務連線已中斷。已完成的對話保留，請重新開始。'));
      };
    });
  }
  _unexpectedClose(error) { this._report(error); this._disconnect(false).catch(failure => this._report(failure)); }

  async connect() {
    this._assertAlive();
    if (this._model.snapshot.voiceAdmin.busy) return false;
    if (this._model.snapshot.connection === 'connected') return true;
    if (this._model.snapshot.connection === 'connecting') return this._connectPromise ?? false;
    const epoch = this._beginCycle();
    Object.assign(this._model.snapshot, {error: '', connection: 'connecting', paused: false, startupStage: ""});
    this._model.snapshot.metrics.firstAudioMs = null; this._emit();
    const pending = (async () => {
      try {
        // Resume on the host click, before bootstrap/model waits lose activation.
        await this._audio.open(this._model.snapshot.settings.outputDeviceId);
        if (!this._current(epoch)) return false;
        await this.initialize();
        if (!this._current(epoch)) return false;
        if (this._model.snapshot.mode === 'live') await this._prepareEngine(epoch);
        if (!this._current(epoch)) return false;
        await this._openSocket(epoch);
        if (!this._current(epoch)) return false;
        await this._restoreHistory(epoch);
        if (!this._current(epoch)) return false;
        if (this._model.snapshot.mode === 'live') {
          this._model.snapshot.startupStage = 'microphone'; this._emit();
          await this._audio.capture(this._model.snapshot.settings.micDeviceId);
        }
        if (!this._current(epoch)) return false;
        await this._refreshDevices();
        if (!this._current(epoch)) return false;
        Object.assign(this._model.snapshot, {connection: 'connected', state: 'listening', startupStage: ''}); this._emit();
        return true;
      } catch (error) {
        if (!this._current(epoch)) return false;
        const closing = this._disconnect(false); const errorEpoch = this._epoch;
        try { await closing; } catch { /* Preserve the initiating error. */ }
        if (!this._current(errorEpoch)) return false;
        this._model.snapshot.connection = 'error';
        throw this._report(error);
      }
    })();
    this._connectPromise = pending;
    try { return await pending; }
    finally { if (this._connectPromise === pending) this._connectPromise = null; }
  }
  async _disconnect(clearError = true) {
    this._epoch++; this._connectionAbort?.abort(); this._connectionAbort = null; this._operation = null;
    this._cancelRemote(this._model.interrupt());
    const previous = this._socket; this._socket = null;
    this._configuredWaiter?.reject(abortError()); this._configuredWaiter = null;
    this._rejectItems(abortError());
    if (previous && previous.readyState < this._WebSocket.CLOSING) previous.close();
    const closing = this._audio.close();
    this._stoppedAt = null; this._setLevels(0, 0);
    Object.assign(this._model.snapshot, {connection: 'disconnected', state: 'idle', paused: false});
    if (clearError) this._model.snapshot.error = '';
    this._emit(); await closing;
  }
  async disconnect() { await this._disconnect(); }
  _detachForNewSession() {
    const epoch = this._beginCycle();
    const id = this._model.interrupt();
    this._audio.clear(); this._audio.pauseCapture(true); this._cancelRemote(id);
    const previous = this._socket; this._socket = null;
    this._configuredWaiter?.reject(abortError()); this._configuredWaiter = null;
    previous?.close(); this._rejectItems(abortError());
    for (const message of this._model.snapshot.messages) if (message.status === 'streaming') message.status = 'interrupted';
    this._stoppedAt = null;
    return epoch;
  }
  async _createItem(item, epoch) {
    if (!this._current(epoch)) throw abortError();
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => waiter.reject(new Error('語音服務未確認對話內容。')), 8000);
      const finish = error => {
        clearTimeout(timer);
        if (this._itemWaiters.get(item.id) === waiter) this._itemWaiters.delete(item.id);
        if (error) reject(error); else resolve();
      };
      const waiter = {resolve: () => finish(), reject: error => finish(error)};
      this._itemWaiters.set(item.id, waiter);
      if (!this._send({type: 'conversation.item.create', item})) waiter.reject(new Error('語音連線已中斷。'));
    });
  }
  async _restoreHistory(epoch) {
    const completed = this._model.snapshot.messages.filter(message => message.status === 'complete' && message.text.trim()).slice(-30);
    for (const message of completed) {
      if (!this._current(epoch)) return;
      await this._createItem({id: 'seed_' + crypto.randomUUID().replaceAll('-', ''), type: 'message', role: message.role,
        content: [{type: message.role === 'assistant' ? 'output_text' : 'input_text', text: message.text}]}, epoch);
    }
  }
  async _reconnect(epoch, {capture = true} = {}) {
    Object.assign(this._model.snapshot, {connection: 'connecting', paused: false}); this._emit();
    if (this._model.snapshot.mode === 'live') await this._prepareEngine(epoch);
    if (!this._current(epoch)) return false;
    await this._openSocket(epoch);
    if (!this._current(epoch)) return false;
    await this._restoreHistory(epoch);
    if (!this._current(epoch)) return false;
    if (this._model.snapshot.mode === 'live' && !this._audio.stream) await this._audio.capture(this._model.snapshot.settings.micDeviceId);
    if (!this._current(epoch)) return false;
    this._audio.pauseCapture(!capture);
    Object.assign(this._model.snapshot, {connection: 'connected', state: 'listening', startupStage: ''}); this._emit();
    return true;
  }
  async _operationFailed(error, epoch) {
    if (!this._current(epoch)) return false;
    const closing = this._disconnect(false); const errorEpoch = this._epoch;
    try { await closing; } catch { /* Preserve the initiating error. */ }
    if (!this._current(errorEpoch)) return false;
    throw this._report(error);
  }
  async interrupt() {
    this._assertAlive();
    if (this._operation || this._model.snapshot.connection !== 'connected' || this._model.snapshot.paused) return false;
    const epoch = this._detachForNewSession(); this._operation = epoch; this._emit();
    try {
      if (!await this._reconnect(epoch)) return false;
      this._model.snapshot.state = 'interrupted'; this._emit(); return true;
    } catch (error) { return this._operationFailed(error, epoch); }
    finally { if (this._operation === epoch) this._operation = null; }
  }
  async pause() {
    this._assertAlive();
    if (this._operation || this._model.snapshot.connection !== 'connected' || this._model.snapshot.paused) return false;
    this._detachForNewSession();
    Object.assign(this._model.snapshot, {paused: true, state: 'paused'}); this._emit(); return true;
  }
  async resume() {
    this._assertAlive();
    if (this._operation || !this._model.snapshot.paused || this._model.snapshot.connection !== 'connected') return false;
    const epoch = this._epoch; this._operation = epoch;
    try { return await this._reconnect(epoch); }
    catch (error) { return this._operationFailed(error, epoch); }
    finally { if (this._operation === epoch) this._operation = null; }
  }
  async sendText(value) {
    this._assertAlive();
    const text = String(value ?? '').trim().slice(0, 8000);
    if (!text) return false;
    if (this._model.snapshot.connection !== 'connected') throw this._report(new Error('請先開始連線，再傳送文字。'));
    if (this._model.snapshot.paused) throw this._report(new Error('目前已暫停，請先恢復對話。'));
    if (this._operation) return false;
    const submittedAt = performance.now();
    // Upstream has no input-audio reset/idle ACK: start a fresh session and
    // seed only completed text before each typed turn or manual interruption.
    const epoch = this._detachForNewSession(); this._operation = epoch;
    try {
      if (!await this._reconnect(epoch, {capture: false})) return false;
      const id = 'user_' + crypto.randomUUID().replaceAll('-', '');
      this._model.addUser(text, id);
      this._timing.begin('text', submittedAt); this._stoppedAt = submittedAt;
      this._model.snapshot.metrics.firstAudioMs = null;
      await this._createItem({id, type: 'message', role: 'user', content: [{type: 'input_text', text}]}, epoch);
      if (!this._current(epoch)) return false;
      this._send({type: 'response.create'}); this._audio.pauseCapture(false); this._emit(); return true;
    } catch (error) { return this._operationFailed(error, epoch); }
    finally { if (this._operation === epoch) this._operation = null; }
  }
  async updateSettings(update) {
    this._assertAlive();
    let patch;
    try { patch = settingsPatch(update); } catch (error) { throw this._report(error); }
    const previousMic = this._model.snapshot.settings.micDeviceId;
    Object.assign(this._model.snapshot.settings, patch);
    const epoch = this._epoch;
    try {
      if ('outputDeviceId' in patch) await this._audio.setOutput(patch.outputDeviceId);
      if (!this._current(epoch)) return false;
      if (this._model.snapshot.connection === 'connected') {
        this._send(sessionUpdate({paused: this._model.snapshot.paused, bargeIn: this._model.snapshot.settings.bargeIn}));
        if ('micDeviceId' in patch && patch.micDeviceId !== previousMic && this._model.snapshot.mode === 'live') {
          await this._audio.capture(patch.micDeviceId);
          if (!this._current(epoch)) return false;
          this._audio.pauseCapture(this._model.snapshot.paused); await this._refreshDevices();
        }
      }
    } catch (error) { if (this._current(epoch)) throw this._report(error); return false; }
    this._emit(); return true;
  }
  _resetConversation() {
    const model = this._model;
    model.snapshot.messages = []; model.snapshot.caption = {user: '', assistant: ''}; model.snapshot.metrics.firstAudioMs = null;
    model.currentResponseId = null; model.generationDone = true; model.playbackActive = false;
    model.cancelled.clear(); model.snapshots.clear(); model.inputItemId = null; this._stoppedAt = null;
  }
  async clearHistory() {
    this._assertAlive();
    const closing = this._disconnect();
    this._resetConversation(); this._emit(); await closing;
  }
  async setMode(mode) {
    this._assertAlive();
    if (!['live', 'demo'].includes(mode)) throw this._report(new TypeError('Voice mode must be live or demo.'));
    if (mode === this._model.snapshot.mode) return false;
    if (mode === 'live' && this._model.snapshot.health.demo_only) throw this._report(new Error('目前由演練啟動器開啟。要使用本地模型，請執行 scripts/start.ps1。'));
    const closing = this._disconnect();
    this._resetConversation(); this._model.snapshot.mode = mode; this._emit(); await closing; return true;
  }
  async destroy() {
    if (this._destroyPromise) return this._destroyPromise;
    this._destroyed = true; this._bootstrapAbort?.abort(); this._voiceAdminAbort?.abort();
    this._mediaDevices?.removeEventListener?.('devicechange', this._deviceChange);
    this._stateListeners.clear(); this._levelListeners.clear(); this._errorListeners.clear();
    this._destroyPromise = this._disconnect(); await this._destroyPromise;
  }
}
