/**
 * Presentational UI only. Audio, protocol, model calls and demo orchestration
 * belong to app.js. All user/server text is inserted as text, never HTML.
 */

const INITIAL = {
  mode: "live",
  connection: "disconnected",
  state: "idle",
  caption: { user: "", assistant: "" },
  messages: [],
  metrics: { firstAudioMs: null },
  settings: { micDeviceId: "", outputDeviceId: "", captions: true, bargeIn: true },
  devices: [],
  paused: false,
  error: "",
  health: null,
};

const STATE_COPY = {
  idle: ["準備好聽你說。", "從一個念頭，開始一段對話。"],
  listening: ["我在聽，請說。", "可以自然說話，也可以隨時插話。"],
  thinking: ["讓我想一想。", "正在生成回覆。"],
  speaking: ["正在回覆你。", "你可以直接開口打斷我。"],
  paused: ["對話已暫停。", "已完成的對話會保留；未說完的內容，恢復後請重說。"],
  interrupted: ["我在聽，你繼續。", "已停止播放，請繼續說。"],
};

function text(value) {
  return typeof value === "string" ? value : "";
}

export function startupCopy(engine = {}) {
  const loadedDrama = engine.tts_backend === 'drama3' && engine.tts_process?.pool?.model_loaded === true;
  const heading = loadedDrama ? '正在預熱語音引擎。' : '正在準備語音服務。';
  let supporting = text(engine.detail) || (loadedDrama
    ? 'Drama 3 已從本機載入，正在準備首段語音與會話。'
    : '正在初始化本機語音引擎。');
  if (Number.isFinite(engine.startup_wait_seconds)) {
    supporting += ` 已等待 ${Math.max(0, Math.floor(engine.startup_wait_seconds))} 秒。`;
  }
  return [heading, supporting];
}
function finiteLevel(value) {
  return Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;
}

function setIcon(button, icon) {
  button.querySelector("use")?.setAttribute("href", `#icon-${icon}`);
}

function isEditableOrControl(target) {
  return target instanceof Element && Boolean(target.closest("input, textarea, select, button, a, [contenteditable='true'], [role='textbox']"));
}

/**
 * @param {object} actions Application-owned callbacks.
 * @returns {{render: (snapshot: object) => void, setLevels: (input: number, output: number) => void, destroy: () => void}}
 */
export function createUI(actions = {}) {
  const byId = (id) => {
    const element = document.getElementById(id);
    if (!element) throw new Error(`HER Voice UI element is missing: ${id}`);
    return element;
  };
  const el = Object.fromEntries([
    "voice-app", "connection-label", "history-button", "settings-button",
    "mode-notice", "error-notice", "error-message", "error-settings",
    "sound-canvas", "waveform", "user-caption", "user-caption-text",
    "main-caption", "supporting-caption", "interrupt-button", "latency-label",
    "text-composer", "message-input", "send-button", "composer-hint",
    "mic-button", "mic-label", "captions-button", "call-button", "call-label",
    "sound-button", "keyboard-button", "architecture-label", "mode-button",
    "mode-button-label", "settings-dialog", "history-dialog", "mic-device",
    "output-device", "device-help", "captions-setting", "barge-in-setting",
    "service-mode", "service-engine", "service-profile", "service-key", "history-scroll",
    "history-empty", "history-list", "clear-history", "announcer",
    "voice-backend", "voice-select", "voice-name", "voice-transcript", "voice-file",
    "voice-import-form", "voice-import-button", "voice-apply-button", "voice-refresh-button",
    "voice-admin-message", "voice-admin-error", "voice-current", "voice-details", "voice-import-details",
  ].map((id) => [id, byId(id)]));

  const controller = new AbortController();
  const signal = controller.signal;
  let snapshot = {
    ...INITIAL,
    caption: { ...INITIAL.caption },
    metrics: { ...INITIAL.metrics },
    settings: { ...INITIAL.settings },
  };
  let lastAnnouncement = "";
  let deviceSignature = "";
  let historySignature = "";
  let voiceConfigSignature = "";
  let voiceListSignature = "";
  let importedVoiceId = "";
  let composerOpen = false;
  let restoreFocus = null;
  let layoutSpace = "";
  const messageNodes = new Map();
  const captionStage = el["main-caption"].closest(".caption-stage");

  const invoke = (name, ...args) => {
    if (typeof actions[name] === "function") return actions[name](...args);
    return undefined;
  };
  const listen = (target, name, handler) => target.addEventListener(name, handler, { signal });
  const connected = () => snapshot.connection === "connected";

  function announce(value) {
    if (value && value !== lastAnnouncement) {
      el.announcer.textContent = value;
      lastAnnouncement = value;
    }
  }

  function closePanels() {
    for (const panel of [el["settings-dialog"], el["history-dialog"]]) {
      if (panel.open) panel.close();
    }
  }

  function openPanel(panel, focusTarget) {
    if (panel.open) return;
    closePanels();
    restoreFocus = document.activeElement;
    panel.showModal();
    if (panel === el["settings-dialog"]) invoke("onLoadVoiceSettings");
    if (focusTarget) focusTarget.focus({ preventScroll: true });
  }

  function updateComposer() {
    const hasText = el["message-input"].value.trim().length > 0;
    el["send-button"].disabled = !connected() || snapshot.paused || !hasText;
    el["composer-hint"].textContent = snapshot.paused
      ? "先恢復對話，再送出訊息"
      : connected() ? "Enter 送出 · Shift + Enter 換行" : "開始對話後即可送出訊息";
    const input = el["message-input"];
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 110)}px`;
    syncLayoutSpace();
  }

  function syncLayoutSpace() {
    const composer = el["text-composer"];
    const composerSpace = composerOpen
      ? composer.getBoundingClientRect().height + (parseFloat(getComputedStyle(composer).marginBottom) || 0)
      : 0;
    const captionMinimum = parseFloat(getComputedStyle(captionStage).minHeight) || 0;
    const captionOverflow = Math.max(0, captionStage.getBoundingClientRect().height - captionMinimum);
    const signature = `${Math.ceil(composerSpace)}:${Math.ceil(captionOverflow)}`;
    if (signature === layoutSpace) return;
    layoutSpace = signature;
    el["voice-app"].style.setProperty("--composer-space", `${Math.ceil(composerSpace)}px`);
    el["voice-app"].style.setProperty("--caption-overflow", `${Math.ceil(captionOverflow)}px`);
  }

  function setComposer(open) {
    composerOpen = open;
    el["text-composer"].hidden = !open;
    el["voice-app"].dataset.composer = String(open);
    el["keyboard-button"].setAttribute("aria-expanded", String(open));
    el["keyboard-button"].setAttribute("aria-label", open ? "收起文字輸入" : "顯示文字輸入");
    el["interrupt-button"].querySelector(".key-hint").hidden = open;
    if (open) {
      updateComposer();
      el["message-input"].focus({ preventScroll: true });
      if (el["text-composer"].getBoundingClientRect().bottom > innerHeight - 12) {
        el["text-composer"].scrollIntoView({ block: "nearest", behavior: "auto" });
      }
    } else {
      syncLayoutSpace();
      el["keyboard-button"].focus({ preventScroll: true });
    }
  }

  const layoutObserver = new ResizeObserver(syncLayoutSpace);
  layoutObserver.observe(captionStage);
  layoutObserver.observe(el["text-composer"]);

  function submitMessage(event) {
    event?.preventDefault();
    const value = el["message-input"].value.trim();
    if (!value) return;
    if (!connected()) {
      announce("請先開始對話，再送出訊息。");
      return;
    }
    if (snapshot.paused) {
      announce("目前已暫停，請先恢復對話再送出訊息。");
      return;
    }
    invoke("onSend", value);
    el["message-input"].value = "";
    updateComposer();
  }

  listen(el["call-button"], "click", () => {
    invoke(snapshot.connection === "connected" || snapshot.connection === "connecting" ? "onStop" : "onStart");
  });
  listen(el["mic-button"], "click", () => invoke("onPause"));
  listen(el["captions-button"], "click", () => invoke("onSettingsChange", { captions: !snapshot.settings.captions }));
  listen(el["keyboard-button"], "click", () => setComposer(!composerOpen));
  listen(el["history-button"], "click", () => openPanel(el["history-dialog"]));
  listen(el["settings-button"], "click", () => openPanel(el["settings-dialog"]));
  listen(el["sound-button"], "click", () => openPanel(el["settings-dialog"], el["output-device"]));
  listen(el["error-settings"], "click", () => openPanel(el["settings-dialog"]));
  listen(el["interrupt-button"], "click", () => invoke("onInterrupt"));
  listen(el["clear-history"], "click", () => invoke("onClearHistory"));
  listen(el["mode-button"], "click", () => invoke("onModeChange", snapshot.mode === "demo" ? "live" : "demo"));
  listen(el["captions-setting"], "change", (event) => invoke("onSettingsChange", { captions: event.target.checked }));
  listen(el["barge-in-setting"], "change", (event) => invoke("onSettingsChange", { bargeIn: event.target.checked }));
  listen(el["mic-device"], "change", (event) => invoke("onSettingsChange", { micDeviceId: event.target.value }));
  listen(el["output-device"], "change", (event) => invoke("onSettingsChange", { outputDeviceId: event.target.value }));
  listen(el["text-composer"], "submit", submitMessage);
  listen(el["voice-refresh-button"], "click", () => invoke("onLoadVoiceSettings"));
  listen(el["voice-select"], "change", () => renderVoiceDetails());
  listen(el["voice-apply-button"], "click", () => invoke("onApplyVoiceSettings", {
    backend: el["voice-backend"].value, voice_id: el["voice-select"].value,
  }));
  listen(el["voice-import-form"], "submit", event => {
    event.preventDefault();
    invoke("onImportVoice", {
      name: el["voice-name"].value,
      transcript: el["voice-transcript"].value,
      audio: el["voice-file"].files?.[0],
    });
  });
  listen(el["message-input"], "input", updateComposer);
  listen(el["message-input"], "keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) submitMessage(event);
  });

  for (const panel of [el["settings-dialog"], el["history-dialog"]]) {
    panel.querySelectorAll("[data-close-dialog]").forEach((button) => listen(button, "click", () => panel.close()));
    listen(panel, "click", (event) => {
      if (event.target !== panel) return;
      const box = panel.getBoundingClientRect();
      if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) panel.close();
    });
    listen(panel, "close", () => {
      if (restoreFocus instanceof HTMLElement && restoreFocus.isConnected) restoreFocus.focus({ preventScroll: true });
      restoreFocus = null;
    });
  }

  listen(document, "keydown", (event) => {
    if (event.repeat || event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
    if (el["settings-dialog"].open || el["history-dialog"].open) return;
    if (event.key === "Escape") {
      if (composerOpen) {
        event.preventDefault();
        setComposer(false);
      } else if (connected() && ["thinking", "speaking"].includes(snapshot.state)) {
        event.preventDefault();
        invoke("onInterrupt");
      }
    } else if (event.code === "Space" && connected() && !isEditableOrControl(event.target)) {
      event.preventDefault();
      invoke("onPause");
    }
  });

  function renderDevices() {
    const devices = snapshot.devices.filter((device) => device && typeof device.deviceId === "string");
    const signature = JSON.stringify(devices.map(({ deviceId, kind, label }) => [deviceId, kind, label]));
    if (signature !== deviceSignature) {
      deviceSignature = signature;
      for (const [kind, id, defaultLabel] of [
        ["audioinput", "mic-device", "系統預設麥克風"],
        ["audiooutput", "output-device", "系統預設揚聲器"],
      ]) {
        const options = [new Option(defaultLabel, "")];
        let number = 0;
        for (const device of devices.filter((item) => item.kind === kind && item.deviceId && item.deviceId !== "default")) {
          number += 1;
          options.push(new Option(text(device.label) || `${kind === "audioinput" ? "麥克風" : "播放裝置"} ${number}`, device.deviceId));
        }
        el[id].replaceChildren(...options);
      }
    }
    for (const [key, id] of [["micDeviceId", "mic-device"], ["outputDeviceId", "output-device"]]) {
      const value = text(snapshot.settings[key]);
      if (value && !Array.from(el[id].options).some((option) => option.value === value)) {
        el[id].add(new Option("所選裝置（目前未列出）", value));
      }
      el[id].value = value;
    }
    el["device-help"].textContent = snapshot.mode === "demo"
      ? "示範模式不會要求麥克風權限。真實連線時會列出可用裝置。"
      : devices.length ? "裝置由目前瀏覽器提供；變更會套用至目前對話。" : "開始對話並允許麥克風後，會顯示可用裝置。";
  }

  function renderHistory() {
    const messages = snapshot.messages.filter((message) => message && ["user", "assistant"].includes(message.role));
    const signature = JSON.stringify(messages.map((message) => [message.id, message.role, message.text, message.status]));
    if (signature === historySignature) return;
    historySignature = signature;
    const scroll = el["history-scroll"];
    const wasNearBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 80;
    const fragment = document.createDocumentFragment();
    const currentKeys = new Set();
    messages.forEach((message, index) => {
      const key = `${message.id ?? index}:${message.role}`;
      currentKeys.add(key);
      let row = messageNodes.get(key);
      if (!row) {
        row = document.createElement("li");
        row.className = "message";
        const header = document.createElement("div");
        header.className = "message-header";
        const role = document.createElement("span");
        role.className = "message-role";
        const status = document.createElement("span");
        status.className = "message-status";
        const content = document.createElement("p");
        content.className = "message-text";
        header.append(role, status);
        row.append(header, content);
        messageNodes.set(key, row);
      }
      row.dataset.role = message.role;
      row.querySelector(".message-role").textContent = message.role === "user" ? "你" : "HER";
      const statusLabels = { interrupted: "已打斷", cancelled: "已停止", failed: "未完成", streaming: message.role === "user" ? "辨識中" : "回覆中", partial: "辨識中" };
      row.querySelector(".message-status").textContent = statusLabels[message.status] || "";
      row.querySelector(".message-text").textContent = text(message.text);
      fragment.append(row);
    });
    for (const key of messageNodes.keys()) if (!currentKeys.has(key)) messageNodes.delete(key);
    el["history-list"].replaceChildren(fragment);
    el["history-empty"].hidden = messages.length > 0;
    el["clear-history"].disabled = messages.length === 0;
    if (wasNearBottom) scroll.scrollTop = scroll.scrollHeight;
  }

  function renderVoiceDetails() {
    const admin = snapshot.voiceAdmin || {};
    const voice = (admin.voices || []).find(item => item.id === el["voice-select"].value);
    const duration = Number.isFinite(voice?.duration_seconds) ? `${voice.duration_seconds.toFixed(1)} 秒` : "";
    const sampleRate = Number.isFinite(voice?.sample_rate) ? `${(voice.sample_rate / 1000).toFixed(1)} kHz` : "";
    el["voice-details"].textContent = voice
      ? [duration, sampleRate, text(voice.transcript)].filter(Boolean).join(" · ")
      : "请导入参考录音，或选择已保存的音色。";
    el["voice-apply-button"].disabled = Boolean(admin.loading || admin.busy || !admin.config || !voice);
  }

  function renderVoiceSettings() {
    const admin = snapshot.voiceAdmin || {};
    const config = admin.config;
    const voices = Array.isArray(admin.voices) ? admin.voices : [];
    const configSignature = config ? JSON.stringify([config.backend, config.voice_id]) : "";
    const listSignature = JSON.stringify(voices.map(voice => [voice.id, voice.name, voice.transcript, voice.duration_seconds, voice.sample_rate]));
    if (listSignature !== voiceListSignature) {
      voiceListSignature = listSignature;
      const selected = el["voice-select"].value;
      const options = [new Option(voices.length ? "请选择参考音色" : "请先导入一段参考录音", "")];
      for (const voice of voices) options.push(new Option(text(voice.name) || voice.id, voice.id));
      el["voice-select"].replaceChildren(...options);
      el["voice-select"].value = voices.some(voice => voice.id === selected) ? selected : (config?.voice_id || "");
    }
    if (configSignature !== voiceConfigSignature) {
      voiceConfigSignature = configSignature;
      el["voice-backend"].value = config?.backend || "drama3";
      el["voice-select"].value = config?.voice_id || "";
      if (!voices.length) el["voice-import-details"].open = true;
    }
    if (admin.importedVoiceId && admin.importedVoiceId !== importedVoiceId) {
      importedVoiceId = admin.importedVoiceId;
      el["voice-select"].value = importedVoiceId;
      el["voice-import-form"].reset();
      el["voice-import-details"].open = false;
    }
    const selected = voices.find(voice => voice.id === config?.voice_id);
    const label = config?.backend === "qwen3" ? "Qwen3-TTS Base" : "Drama 3";
    el["voice-current"].textContent = config
      ? `${label} · ${selected ? text(selected.name) : "尚未选择参考音色"}`
      : admin.loading ? "正在读取本机声音设置…" : "声音设置尚未读取";
    const busy = Boolean(admin.loading || admin.busy);
    el["voice-current"].setAttribute("aria-busy", String(busy));
    for (const id of ["voice-backend", "voice-select", "voice-name", "voice-transcript", "voice-file", "voice-import-button", "voice-refresh-button"]) el[id].disabled = busy;
    el["voice-import-button"].textContent = admin.busy ? "正在处理…" : "保存参考音色";
    el["voice-apply-button"].textContent = admin.busy ? "正在处理…" : "应用声音设置";
    el["voice-admin-message"].textContent = text(admin.message);
    el["voice-admin-message"].hidden = !text(admin.message);
    el["voice-admin-error"].textContent = text(admin.error);
    el["voice-admin-error"].hidden = !text(admin.error);
    renderVoiceDetails();
  }

  const sound = createSoundField(el["sound-canvas"], el.waveform);

  function render(next = {}) {
    snapshot = {
      ...snapshot,
      ...next,
      mode: next.mode === "demo" ? "demo" : next.mode === "live" ? "live" : snapshot.mode,
      caption: { ...snapshot.caption, ...(next.caption || {}) },
      metrics: { ...snapshot.metrics, ...(next.metrics || {}) },
      settings: { ...snapshot.settings, ...(next.settings || {}) },
      messages: Array.isArray(next.messages) ? next.messages : snapshot.messages,
      devices: Array.isArray(next.devices) ? next.devices : snapshot.devices,
    };
    if (!["disconnected", "connecting", "connected", "error"].includes(snapshot.connection)) snapshot.connection = "disconnected";
    if (!Object.hasOwn(STATE_COPY, snapshot.state)) snapshot.state = "idle";
    const demo = snapshot.mode === "demo";
    const paused = Boolean(snapshot.paused || snapshot.state === "paused");
    const connection = snapshot.connection;
    const isConnected = connected();
    const voiceState = paused && isConnected ? "paused" : snapshot.state;
    el["voice-app"].dataset.mode = snapshot.mode;
    el["voice-app"].dataset.connection = connection;
    el["voice-app"].dataset.state = voiceState;
    const connectionLabels = demo
      ? { disconnected: "示範待命", connecting: "準備示範", connected: "示範演練", error: "示範中斷" }
      : { disconnected: "未連線", connecting: "連線中", connected: "已連線", error: "連線中斷" };
    const connectionLabel = paused && isConnected ? demo ? "示範已暫停" : "已暫停" : connectionLabels[connection];
    el["connection-label"].textContent = connectionLabel;
    el["mode-notice"].hidden = !demo;
    el["mode-notice"].textContent = "示範模式 · 協定演練／測試音，不使用麥克風或 API";
    el["mode-button-label"].textContent = demo ? "切回真實連線" : "試用示範";
    el["mode-button"].setAttribute("aria-label", demo ? "切換至真實語音連線" : "切換至示範模式，不使用麥克風或 API");
    const applicationName = text(snapshot.health?.app_name) || "HER Voice";
    const genericProvider = snapshot.health?.provider === "openai-compatible";
    document.title = applicationName;
    const wordmark = document.querySelector(".wordmark");
    if (wordmark) {
      wordmark.setAttribute("aria-label", `${applicationName} 首頁`);
      wordmark.querySelector(".wordmark-her").textContent = applicationName === "HER Voice" ? "HER" : applicationName;
      wordmark.querySelector(".wordmark-voice").hidden = applicationName !== "HER Voice";
      wordmark.querySelector(".wordmark-divider").hidden = applicationName !== "HER Voice";
    }
    document.querySelectorAll(".panel-context").forEach(element => { element.textContent = applicationName; });
    el["architecture-label"].textContent = demo ? "示範演練 · 測試音訊" : genericProvider ? "本機聽說 · 應用 Agent 對話" : "本機聽說 · DeepSeek 對話";
    el["error-notice"].hidden = !text(snapshot.error);
    el["error-message"].textContent = text(snapshot.error);

    let [heading, supporting] = STATE_COPY[voiceState];
    if (connection === "connecting") {
      heading = demo ? "正在準備示範。" : "正在連接語音服務。";
      supporting = demo ? "這是協定演練，不會辨識你的聲音。" : "若瀏覽器詢問，請允許麥克風。";
      if (!demo && snapshot.startupStage === 'microphone') {
        heading = '語音服務已就緒。';
        supporting = '正在啟用選定的麥克風，請確認輸入裝置與頁面權限。';
      } else if (!demo && snapshot.health?.engine?.state === "starting") {
        [heading, supporting] = startupCopy(snapshot.health.engine);
      }
    } else if (connection === "error") {
      heading = "暫時無法連線。";
      supporting = "檢查本機服務後，重新開始對話。";
    } else if (!isConnected) {
      heading = demo ? "先感受對話的節奏。" : STATE_COPY.idle[0];
      supporting = demo ? "示範會顯示字幕並播放測試音。" : STATE_COPY.idle[1];
    } else if (!snapshot.settings.bargeIn && voiceState === "speaking") {
      supporting = "正在播放回覆。";
    } else if (!snapshot.settings.bargeIn && voiceState === "listening") {
      supporting = "說出你的想法，我會在你說完後回應。";
    }
    if (demo && isConnected) {
      const demoCopy = {
        idle: ["示範已準備好。", "輸入文字，體驗字幕與測試音訊。"],
        listening: ["輸入一句話，開始演練。", "這裡只演練字幕、播放與打斷流程。"],
        thinking: ["正在準備示範回覆。", "示範內容與測試音不來自語音模型。"],
        speaking: ["正在播放示範。", "點擊「打斷回覆」，試試中途接話。"],
        interrupted: ["示範已打斷。", "可輸入下一句，或繼續操作控制列。"],
        paused: ["示範已暫停。", "已完成的文字會保留；恢復後可以繼續輸入。"],
      };
      [heading, supporting] = demoCopy[voiceState] || demoCopy.idle;
    }
    const showTranscript = Boolean(snapshot.settings.captions && isConnected && text(snapshot.caption.assistant) && !paused && voiceState !== "interrupted");
    el["main-caption"].textContent = showTranscript ? text(snapshot.caption.assistant) : heading;
    el["main-caption"].classList.toggle("has-transcript", showTranscript);
    el["supporting-caption"].textContent = supporting;
    const showUser = Boolean(snapshot.settings.captions && isConnected && text(snapshot.caption.user));
    el["user-caption"].hidden = !showUser;
    el["user-caption-text"].textContent = text(snapshot.caption.user);
    el["user-caption-text"].title = text(snapshot.caption.user);
    el["interrupt-button"].hidden = !isConnected || !["thinking", "speaking"].includes(voiceState);
    const firstAudio = snapshot.metrics.firstAudioMs;
    const timing = snapshot.metrics.server;
    const timingParts = timing ? [['辨識', timing.sttMs], ['回答首字', timing.llm_ttftMs], ['回答生成全程', timing.llmMs], ['語音合成首段', timing.tts_ttfaMs],
      ['停頓判定', timing.vad_decisionMs], ['等待續說', timing.holdMs], ['伺服器首音', timing.e2eMs]] : [];
    el["latency-label"].title = timingParts.filter(([, ms]) => Number.isFinite(ms)).map(([name, ms]) => `${name} ${(ms / 1000).toFixed(2)} 秒`).join('；');
    el["latency-label"].hidden = !isConnected || !Number.isFinite(firstAudio) || firstAudio < 0;
    el["latency-label"].textContent = Number.isFinite(firstAudio) && firstAudio >= 0 ? `事件至播放約 ${(firstAudio / 1000).toFixed(2)} 秒` : "";

    el["mic-button"].disabled = !isConnected;
    el["mic-button"].setAttribute("aria-pressed", String(paused));
    el["mic-button"].setAttribute("aria-label", paused ? "恢復麥克風" : "暫停麥克風");
    el["mic-label"].textContent = paused ? "已暫停" : "麥克風";
    setIcon(el["mic-button"], paused ? "mic-off" : "mic");
    el["captions-button"].setAttribute("aria-pressed", String(Boolean(snapshot.settings.captions)));
    el["captions-button"].setAttribute("aria-label", snapshot.settings.captions ? "隱藏字幕" : "顯示字幕");
    el["call-button"].classList.toggle("is-active", isConnected);
    el["call-button"].setAttribute("aria-busy", String(connection === "connecting"));
    el["call-button"].disabled = Boolean(snapshot.voiceAdmin?.busy);
    el["call-label"].textContent = connection === "connecting" ? "取消連線" : isConnected ? "結束對話" : demo ? "開始示範" : "開始對話";
    setIcon(el["call-button"], isConnected || connection === "connecting" ? "end" : "phone");
    el["captions-setting"].checked = Boolean(snapshot.settings.captions);
    el["barge-in-setting"].checked = Boolean(snapshot.settings.bargeIn);
    el["service-mode"].textContent = demo ? "示範協定演練" : "真實連線";
    const engineLabels = { ready: "已就緒", starting: "載入中", failed: "啟動失敗", stopped: "尚未啟動", stopping: "停止中" };
    el["service-engine"].textContent = engineLabels[snapshot.health?.engine?.state] || "尚未檢查";
    el["service-profile"].textContent = text(snapshot.health?.profile) || "尚未檢查";
    const providerReady = snapshot.health?.provider_ready ?? snapshot.health?.api_key_configured;
    el["service-key"].textContent = providerReady === true ? "設定已齊備，待實際連線" : providerReady === false ? "尚未設定" : "尚未檢查";
    renderDevices();
    renderHistory();
    renderVoiceSettings();
    updateComposer();
    sound.setState(isConnected ? voiceState : connection === "connecting" ? "thinking" : "idle", isConnected);
    announce(`${demo ? "示範模式，" : ""}${connectionLabel}。${heading}`);
  }

  render();
  return {
    render,
    setLevels(input = 0, output = 0) { sound.setLevels(finiteLevel(input), finiteLevel(output)); },
    destroy() {
      controller.abort();
      layoutObserver.disconnect();
      sound.destroy();
      closePanels();
    },
  };
}

/** Live, procedural sound visualization. No inferred/synthetic mic readings. */
function createSoundField(canvas, waveform) {
  const context = canvas.getContext("2d");
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const bars = Array.from({ length: 33 }, () => {
    const bar = document.createElement("span");
    waveform.append(bar);
    return bar;
  });
  let width = 1;
  let height = 1;
  let ratio = 1;
  let frame = 0;
  let lastFrame = 0;
  let input = 0;
  let output = 0;
  let envelope = 0;
  let state = "idle";
  let active = false;
  let destroyed = false;

  function drawBars(time) {
    const level = state === "paused" ? 0 : active ? Math.max(input, output) : 0;
    for (let i = 0; i < bars.length; i += 1) {
      const distance = Math.abs(i - 16) / 16;
      const falloff = Math.pow(Math.max(0, 1 - distance), 1.4);
      // The disconnected, static motif is visual identity, never a mic meter.
      const idleShape = !active ? (Math.sin(i * 1.61) ** 2) * falloff * 19 : 0;
      const liveShape = level * falloff * (13 + 31 * Math.abs(Math.sin(i * .9 + time * 4)));
      const barHeight = Math.min(44, 2 + idleShape + liveShape);
      bars[i].style.height = `${barHeight.toFixed(1)}px`;
      bars[i].style.opacity = `${(.35 + .55 * falloff).toFixed(2)}`;
    }
  }

  function draw(milliseconds = 0) {
    if (!context || destroyed) return;
    const time = reducedMotion.matches ? 0 : milliseconds / 1000;
    const target = state === "paused" ? 0 : active ? Math.max(input, output) : 0;
    envelope += (target - envelope) * .13;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    const radius = Math.min(width, height) * .425;
    const centerX = width / 2;
    const centerY = height / 2;
    const motion = reducedMotion.matches ? 0 : time * (state === "thinking" ? .23 : .11);
    const tilt = -.66 + Math.sin(motion * .4) * .035;
    const cosTilt = Math.cos(tilt);
    const sinTilt = Math.sin(tilt);
    const rotation = .48;
    const cosRotation = Math.cos(rotation);
    const sinRotation = Math.sin(rotation);
    const amplitude = reducedMotion.matches ? .035 : .035 + envelope * .065;
    const bright = state === "paused" ? [174, 181, 174] : state === "thinking" ? [195, 216, 232] : [195, 234, 236];
    const lineCount = width < 320 ? 49 : 65;
    const points = 140;

    for (const [x, y] of [[-.49, -.65], [.56, .58]]) {
      const glowX = centerX + x * radius;
      const glowY = centerY + y * radius;
      const glow = context.createRadialGradient(glowX, glowY, 0, glowX, glowY, radius * .32);
      glow.addColorStop(0, `rgba(${bright.join(",")},.075)`);
      glow.addColorStop(.35, `rgba(${bright.join(",")},.025)`);
      glow.addColorStop(1, `rgba(${bright.join(",")},0)`);
      context.fillStyle = glow;
      context.fillRect(glowX - radius * .32, glowY - radius * .32, radius * .64, radius * .64);
    }
    context.lineWidth = Math.max(.6, Math.min(.82, width / 650));
    context.lineJoin = "round";
    for (let line = 0; line < lineCount; line += 1) {
      const fraction = line / (lineCount - 1);
      const latitude = (fraction - .5) * Math.PI * .96;
      const ring = Math.cos(latitude);
      const pole = Math.sin(latitude);
      const alpha = .43 + .36 * (Math.abs(fraction - .5) * 2) + envelope * .08;
      const gradient = context.createLinearGradient(centerX - radius, centerY - radius, centerX + radius, centerY + radius);
      gradient.addColorStop(0, `rgba(${bright.join(",")},${alpha * .7})`);
      gradient.addColorStop(.22, `rgba(${bright.join(",")},${Math.min(.98, alpha + .28)})`);
      gradient.addColorStop(.49, `rgba(109,156,176,${alpha * .18})`);
      gradient.addColorStop(.76, `rgba(${bright.join(",")},${Math.min(.96, alpha + .25)})`);
      gradient.addColorStop(1, `rgba(133,195,207,${alpha * .85})`);
      context.strokeStyle = gradient;
      context.shadowColor = "rgba(163,222,231,.22)";
      context.shadowBlur = 3;
      context.beginPath();
      let pathStarted = false;
      for (let point = 0; point <= points; point += 1) {
        const angle = (point / points) * Math.PI * 2;
        const wave = Math.sin(angle * 3 + latitude * 2.5 + motion) * amplitude;
        const asymmetry = 1 + Math.sin(angle * 2 - motion * .6 + latitude) * .04;
        let x = ring * Math.cos(angle) * asymmetry;
        let y = pole + Math.sin(angle * 2 + latitude * 1.7 + motion) * .16 * ring * ring;
        let z = ring * Math.sin(angle) * (1 + wave);
        const rotatedY = y * cosRotation - z * sinRotation;
        z = y * sinRotation + z * cosRotation;
        y = rotatedY;
        // A single visible surface avoids the crossed front/back wireframe.
        if (z < -.015) {
          pathStarted = false;
          continue;
        }
        const perspective = 1 + z * .075;
        const screenX = (x * cosTilt - y * sinTilt) * radius * perspective;
        const screenY = (x * sinTilt + y * cosTilt) * radius * perspective;
        const px = centerX + screenX;
        const py = centerY + screenY;
        if (!pathStarted) context.moveTo(px, py);
        else context.lineTo(px, py);
        pathStarted = true;
      }
      context.stroke();
    }
    context.shadowBlur = 0;
    drawBars(time);
  }

  function tick(milliseconds) {
    frame = 0;
    if (destroyed || document.hidden || reducedMotion.matches) return;
    if (milliseconds - lastFrame >= 1000 / 30) {
      lastFrame = milliseconds;
      draw(milliseconds);
    }
    frame = requestAnimationFrame(tick);
  }

  function schedule() {
    if (destroyed) return;
    if (reducedMotion.matches || document.hidden) {
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
      draw();
    } else if (!frame) {
      frame = requestAnimationFrame(tick);
    }
  }

  function resize() {
    const bounds = canvas.getBoundingClientRect();
    width = Math.max(1, bounds.width);
    height = Math.max(1, bounds.height);
    ratio = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    draw();
    schedule();
  }

  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  const motionChange = () => schedule();
  const visibilityChange = () => schedule();
  reducedMotion.addEventListener("change", motionChange);
  document.addEventListener("visibilitychange", visibilityChange);
  resize();

  return {
    setState(next, isActive) {
      state = next;
      active = isActive;
      if (!active) { input = 0; output = 0; }
      if (reducedMotion.matches) draw();
    },
    setLevels(nextInput, nextOutput) {
      input = nextInput;
      output = nextOutput;
      if (reducedMotion.matches) draw();
    },
    destroy() {
      destroyed = true;
      if (frame) cancelAnimationFrame(frame);
      observer.disconnect();
      reducedMotion.removeEventListener("change", motionChange);
      document.removeEventListener("visibilitychange", visibilityChange);
    },
  };
}
