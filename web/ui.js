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
  idle: ["把想法，說出來。", "不必整理好才開口。從第一句，慢慢開始。"],
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
    "sound-canvas", "waveform", "orb-button", "orb-hint", "user-caption", "user-caption-text",
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
    "theme-button", "focus-button", "engine-summary", "privacy-copy", "stage-state", "control-hint", "conversation-starters",
    "transcript-list", "transcript-scroll", "transcript-empty", "transcript-count",
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
  const messageNodes = new Map();

  const invoke = (name, ...args) => {
    if (typeof actions[name] === "function") return actions[name](...args);
    return undefined;
  };
  const listen = (target, name, handler) => target.addEventListener(name, handler, { signal });
  const connected = () => snapshot.connection === "connected";
  const orbState = () => (snapshot.paused || snapshot.state === "paused") && connected() ? "paused" : snapshot.state;
  function orbAction() {
    if (snapshot.voiceAdmin?.busy || snapshot.connection === "connecting") return "";
    if (!connected()) return "onStart";
    return ["thinking", "speaking"].includes(orbState()) ? "onInterrupt" : "onPause";
  }

  function announce(value) {
    if (value && value !== lastAnnouncement) {
      el.announcer.textContent = value;
      lastAnnouncement = value;
    }
  }

  const settingsTabs = Array.from(document.querySelectorAll("[data-settings-tab]"));
  function selectSettingsTab(name, focus = false) {
    for (const tab of settingsTabs) {
      const selected = tab.dataset.settingsTab === name;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      const section = byId(tab.getAttribute("aria-controls"));
      section.hidden = !selected;
      if (selected && focus) tab.focus();
    }
    const body = el["settings-dialog"].querySelector(".panel-body");
    body.scrollTop = 0;
  }
  for (const tab of settingsTabs) {
    listen(tab, "click", () => selectSettingsTab(tab.dataset.settingsTab));
    listen(tab, "keydown", event => {
      const index = settingsTabs.indexOf(tab);
      const keys = { ArrowRight: (index + 1) % settingsTabs.length, ArrowLeft: (index + settingsTabs.length - 1) % settingsTabs.length, Home: 0, End: settingsTabs.length - 1 };
      if (!Object.hasOwn(keys, event.key)) return;
      event.preventDefault();
      selectSettingsTab(settingsTabs[keys[event.key]].dataset.settingsTab, true);
    });
  }

  // Follow the OS until the user explicitly chooses an appearance.
  const systemTheme = matchMedia("(prefers-color-scheme: dark)");
  let themeChoice = "";
  try { themeChoice = localStorage.getItem("her-voice-theme") || ""; } catch { /* Optional preference. */ }
  if (!["dark", "light"].includes(themeChoice)) themeChoice = "";
  function applyTheme() {
    if (themeChoice) document.documentElement.dataset.theme = themeChoice;
    else delete document.documentElement.dataset.theme;
    const dark = themeChoice ? themeChoice === "dark" : systemTheme.matches;
    const label = dark ? "切換為淺色外觀" : "切換為深色外觀";
    el["theme-button"].setAttribute("aria-label", label);
    el["theme-button"].title = label;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? "#101412" : "#f5f6f2");
  }
  applyTheme();
  listen(systemTheme, "change", applyTheme);
  listen(el["theme-button"], "click", () => {
    const dark = themeChoice ? themeChoice === "dark" : systemTheme.matches;
    themeChoice = dark ? "light" : "dark";
    try { localStorage.setItem("her-voice-theme", themeChoice); } catch { /* Remains in memory. */ }
    applyTheme();
  });
  listen(el["focus-button"], "click", () => {
    const focus = el["voice-app"].dataset.focus !== "true";
    el["voice-app"].dataset.focus = String(focus);
    el["focus-button"].setAttribute("aria-pressed", String(focus));
    el["focus-button"].setAttribute("aria-label", focus ? "離開專注模式" : "開啟專注模式");
    el["focus-button"].title = focus ? "離開專注模式" : "專注模式";
  });
  document.querySelectorAll("[data-toggle-mode]").forEach(button => listen(button, "click", () => invoke("onModeChange", snapshot.mode === "demo" ? "live" : "demo")));
  document.querySelectorAll("[data-open-history]").forEach(button => listen(button, "click", () => openPanel(el["history-dialog"])));
  document.querySelectorAll("[data-open-settings]").forEach(button => listen(button, "click", () => openPanel(el["settings-dialog"])));
  document.querySelectorAll("[data-prompt]").forEach(button => listen(button, "click", () => {
    el["message-input"].value = button.dataset.prompt;
    setComposer(true);
  }));

  function closePanels() {
    for (const panel of [el["settings-dialog"], el["history-dialog"]]) {
      if (panel.open) panel.close();
    }
  }

  function openPanel(panel, focusTarget) {
    if (panel.open) return;
    closePanels();
    restoreFocus = document.activeElement;
    if (panel === el["settings-dialog"] && focusTarget) {
      const section = focusTarget.closest("[role=tabpanel]");
      if (section) selectSettingsTab(section.id.replace("settings-", ""));
    }
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
  }

  function setComposer(open) {
    composerOpen = open;
    el["text-composer"].hidden = !open;
    el["voice-app"].dataset.composer = String(open);
    el["conversation-starters"].hidden = open || connected() || snapshot.connection === "connecting";
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
      el["keyboard-button"].focus({ preventScroll: true });
    }
  }

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
  listen(el["orb-button"], "click", () => {
    const action = orbAction();
    if (!action) return;
    sound.pulse();
    invoke(action);
  });
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
    const transcript = el["transcript-scroll"];
    const transcriptAtBottom = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 80;
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
    // Mirror only safe, text-populated nodes. Do not reparse model/user content.
    el["transcript-list"].replaceChildren(...Array.from(el["history-list"].children, node => node.cloneNode(true)));
    el["transcript-empty"].hidden = messages.length > 0;
    el["transcript-count"].textContent = String(messages.length);
    el["transcript-count"].setAttribute("aria-label", `${messages.length} 則訊息`);
    if (transcriptAtBottom) transcript.scrollTop = transcript.scrollHeight;
    if (wasNearBottom) scroll.scrollTop = scroll.scrollHeight;
  }

  function renderVoiceDetails() {
    const admin = snapshot.voiceAdmin || {};
    const voice = (admin.voices || []).find(item => item.id === el["voice-select"].value);
    const duration = Number.isFinite(voice?.duration_seconds) ? `${voice.duration_seconds.toFixed(1)} 秒` : "";
    const sampleRate = Number.isFinite(voice?.sample_rate) ? `${(voice.sample_rate / 1000).toFixed(1)} kHz` : "";
    el["voice-details"].textContent = voice
      ? [duration, sampleRate, text(voice.transcript)].filter(Boolean).join(" · ")
      : "請匯入參考錄音，或選擇已儲存的音色。";
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
      const options = [new Option(voices.length ? "請選擇參考音色" : "請先匯入一段參考錄音", "")];
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
      ? `${label} · ${selected ? text(selected.name) : "尚未選擇參考音色"}`
      : admin.loading ? "正在讀取本機聲音設定…" : "声音设置尚未读取";
    const busy = Boolean(admin.loading || admin.busy);
    el["voice-current"].setAttribute("aria-busy", String(busy));
    for (const id of ["voice-backend", "voice-select", "voice-name", "voice-transcript", "voice-file", "voice-import-button", "voice-refresh-button"]) el[id].disabled = busy;
    el["voice-import-button"].textContent = admin.busy ? "正在處理…" : "儲存參考音色";
    el["voice-apply-button"].textContent = admin.busy ? "正在處理…" : "套用聲音設定";
    el["voice-admin-message"].textContent = text(admin.message);
    el["voice-admin-message"].hidden = !text(admin.message);
    el["voice-admin-error"].textContent = text(admin.error);
    el["voice-admin-error"].hidden = !text(admin.error);
    renderVoiceDetails();
  }

  const sound = createSoundField(el["sound-canvas"], el.waveform, el["orb-button"]);

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
    document.querySelectorAll("[data-mode-label]").forEach(label => { label.textContent = el["mode-button-label"].textContent; });
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
    const engineStatus = engineLabels[snapshot.health?.engine?.state] || "尚未檢查";
    el["service-engine"].textContent = engineStatus;
    el["engine-summary"].textContent = demo ? "示範模式 · 未使用引擎" : engineStatus;
    el["privacy-copy"].textContent = demo ? "示範不採集麥克風、不呼叫 API，僅使用測試音。" : "語音在本機處理；對話文字傳送至所選模型。";
    const stateLabels = { idle: "隨時開始", listening: "正在聆聽", thinking: "正在思考", speaking: "正在回覆", paused: "麥克風已暫停", interrupted: "已打斷回覆" };
    el["stage-state"].textContent = connection === "connecting" ? "正在連線" : connection === "error" ? "需要檢查連線" : isConnected ? stateLabels[voiceState] : "隨時開始";
    el["conversation-starters"].hidden = composerOpen || isConnected || connection === "connecting";
    el["control-hint"].textContent = connection === "connecting" ? "準備中，可隨時取消連線" : !isConnected ? demo ? "點擊開始示範，或輕觸光球" : "點擊開始對話，或輕觸光球" : paused ? "輕觸光球或麥克風，繼續對話" : demo ? "示範使用測試音；可透過文字輸入演練" : ["speaking", "thinking"].includes(voiceState) ? "想接話時，輕觸光球即可打斷" : "自然開口就好，HER 正在聆聽";
    el["service-profile"].textContent = text(snapshot.health?.profile) || "尚未檢查";
    const providerReady = snapshot.health?.provider_ready ?? snapshot.health?.api_key_configured;
    el["service-key"].textContent = providerReady === true ? "設定已齊備，待實際連線" : providerReady === false ? "尚未設定" : "尚未檢查";
    renderDevices();
    renderHistory();
    renderVoiceSettings();
    updateComposer();
    const orbCopy = !isConnected
      ? connection === "connecting" ? ["正在連線", "正在連接語音服務"] : [demo ? "輕觸開始示範" : "輕觸開始", demo ? "開始示範" : "開始對話"]
      : voiceState === "paused" ? ["輕觸繼續", "恢復麥克風"]
        : ["thinking", "speaking"].includes(voiceState) ? ["輕觸打斷", "打斷回覆"] : ["輕觸暫停", "暫停麥克風"];
    el["orb-hint"].textContent = orbCopy[0];
    el["orb-button"].setAttribute("aria-label", orbCopy[1]);
    el["orb-button"].disabled = Boolean(snapshot.voiceAdmin?.busy) || connection === "connecting";
    sound.setState(isConnected ? voiceState : connection === "connecting" ? "thinking" : "idle", isConnected);
    announce(`${demo ? "示範模式，" : ""}${connectionLabel}。${heading}`);
  }

  render();
  return {
    render,
    setLevels(input = 0, output = 0) { sound.setLevels(finiteLevel(input), finiteLevel(output)); },
    destroy() {
      controller.abort();
      sound.destroy();
      closePanels();
    },
  };
}

/**
 * Voice orb: a WebGL glass sphere with a fluid luminous interior.
 * It reacts only to real input/output levels supplied by the audio layer.
 * Falls back to a 2D-canvas orb when WebGL is unavailable.
 */
const ORB_STATES = {
  idle:        { deep: "#0a222b", mid: "#3c8a92", high: "#d4f1ec", rim: "#8fd0cc", speed: .20, think: 0, listen: 0, speak: 0, dim: 0, glow: .30 },
  listening:   { deep: "#071f33", mid: "#2b9fc4", high: "#e2f9ff", rim: "#7fd8ee", speed: .30, think: 0, listen: 1, speak: 0, dim: 0, glow: .40 },
  thinking:    { deep: "#110f33", mid: "#6367d1", high: "#dedaff", rim: "#a4a0ff", speed: .78, think: 1, listen: 0, speak: 0, dim: 0, glow: .42 },
  speaking:    { deep: "#0a2a28", mid: "#57c0ad", high: "#fff0d4", rim: "#a6e8da", speed: .40, think: 0, listen: 0, speak: 1, dim: 0, glow: .46 },
  paused:      { deep: "#1d1912", mid: "#7f6c4b", high: "#e6d5b2", rim: "#c2a874", speed: .05, think: 0, listen: 0, speak: 0, dim: 1, glow: .16 },
  interrupted: { deep: "#071f33", mid: "#2b9fc4", high: "#e2f9ff", rim: "#7fd8ee", speed: .30, think: 0, listen: 1, speak: 0, dim: 0, glow: .40 },
};
const ORB_COLORS = ["deep", "mid", "high", "rim"];
const ORB_SCALARS = ["speed", "think", "listen", "speak", "dim", "glow"];

function hexColor(value) {
  const n = parseInt(value.slice(1), 16);
  return [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255];
}

const ORB_VERTEX = `attribute vec2 aPosition;
void main() { gl_Position = vec4(aPosition, 0.0, 1.0); }`;

const ORB_FRAGMENT = `precision highp float;
uniform vec2 uRes;
uniform float uTime, uPhase, uLevel, uRadius, uThink, uListen, uSpeak, uDim, uGlow, uHover, uPress, uRipple, uRippleAge;
uniform vec2 uPointer;
uniform vec3 uDeep, uMid, uHigh, uRim;

vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 permute(vec4 x) { return mod289(((x * 34.0) + 1.0) * x); }
vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }
float snoise(vec3 v) {
  const vec2 C = vec2(1.0 / 6.0, 1.0 / 3.0);
  const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
  vec3 i = floor(v + dot(v, C.yyy));
  vec3 x0 = v - i + dot(i, C.xxx);
  vec3 g = step(x0.yzx, x0.xyz);
  vec3 l = 1.0 - g;
  vec3 i1 = min(g.xyz, l.zxy);
  vec3 i2 = max(g.xyz, l.zxy);
  vec3 x1 = x0 - i1 + C.xxx;
  vec3 x2 = x0 - i2 + C.yyy;
  vec3 x3 = x0 - D.yyy;
  i = mod289(i);
  vec4 p = permute(permute(permute(i.z + vec4(0.0, i1.z, i2.z, 1.0)) + i.y + vec4(0.0, i1.y, i2.y, 1.0)) + i.x + vec4(0.0, i1.x, i2.x, 1.0));
  float n_ = 0.142857142857;
  vec3 ns = n_ * D.wyz - D.xzx;
  vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
  vec4 x_ = floor(j * ns.z);
  vec4 y_ = floor(j - 7.0 * x_);
  vec4 x = x_ * ns.x + ns.yyyy;
  vec4 y = y_ * ns.x + ns.yyyy;
  vec4 h = 1.0 - abs(x) - abs(y);
  vec4 b0 = vec4(x.xy, y.xy);
  vec4 b1 = vec4(x.zw, y.zw);
  vec4 s0 = floor(b0) * 2.0 + 1.0;
  vec4 s1 = floor(b1) * 2.0 + 1.0;
  vec4 sh = -step(h, vec4(0.0));
  vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
  vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;
  vec3 p0 = vec3(a0.xy, h.x);
  vec3 p1 = vec3(a0.zw, h.y);
  vec3 p2 = vec3(a1.xy, h.z);
  vec3 p3 = vec3(a1.zw, h.w);
  vec4 norm = taylorInvSqrt(vec4(dot(p0, p0), dot(p1, p1), dot(p2, p2), dot(p3, p3)));
  p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
  vec4 m = max(0.6 - vec4(dot(x0, x0), dot(x1, x1), dot(x2, x2), dot(x3, x3)), 0.0);
  m = m * m;
  return 42.0 * dot(m * m, vec4(dot(p0, x0), dot(p1, x1), dot(p2, x2), dot(p3, x3)));
}
float fbm(vec3 p) {
  float sum = 0.0, amp = 0.5;
  for (int i = 0; i < 3; i++) { sum += amp * snoise(p); p = p * 1.97 + vec3(1.7, 9.2, 3.1); amp *= 0.46; }
  return sum;
}
mat3 rotateY(float a) { float c = cos(a), s = sin(a); return mat3(c, 0.0, -s, 0.0, 1.0, 0.0, s, 0.0, c); }
mat3 rotateX(float a) { float c = cos(a), s = sin(a); return mat3(1.0, 0.0, 0.0, 0.0, c, s, 0.0, -s, c); }
float hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }

void main() {
  float shortSide = min(uRes.x, uRes.y);
  vec2 p = (gl_FragCoord.xy - 0.5 * uRes) / (0.5 * shortSide);
  float len = length(p);
  float angle = atan(p.y, p.x);
  float pixel = 1.0 / (0.5 * shortSide);

  // Silhouette: slow breathing at rest, organic edge motion driven by real level.
  float breath = sin(uTime * 1.05) * 0.010 * (1.0 - uDim * 0.7);
  float edgeNoise = snoise(vec3(cos(angle) * 1.3, sin(angle) * 1.3, uPhase * 0.9));
  float edgeNoise2 = snoise(vec3(cos(angle) * 3.1, sin(angle) * 3.1, uPhase * 1.6 + 4.0));
  float wobble = edgeNoise * (0.007 + uLevel * 0.040) + edgeNoise2 * uLevel * 0.012;
  float radius = uRadius * (1.0 + breath + uLevel * 0.085 + uHover * 0.022 - uPress * 0.035 + wobble);
  float r = len / radius;
  float pr = pixel / radius;

  // Ambient halo and state rings live outside the sphere.
  float outside = max(r - 1.0, 0.0);
  vec3 haloColor = mix(uMid, uRim, 0.55);
  float haloStrength = uGlow + uLevel * 0.55 + uHover * 0.10;
  float canvasFade = 1.0 - smoothstep(0.72, 0.98, len);
  vec3 color = haloColor * (exp(-outside * 7.0) * haloStrength + exp(-outside * 2.6) * haloStrength * 0.20) * canvasFade;

  float ringRadius = 1.14 + uLevel * 0.04;
  float ring = exp(-pow((r - ringRadius) / (2.2 * pr + 0.006), 2.0));
  float sweep = pow(0.5 + 0.5 * cos(angle + uTime * 2.6), 10.0);
  float sweep2 = pow(0.5 + 0.5 * cos(angle + uTime * 2.6 + 3.14159), 18.0);
  color += uHigh * ring * (sweep + sweep2 * 0.5) * uThink * 0.95;
  color += uRim * ring * 0.07 * uThink;

  float listenRadius = 1.06 + uLevel * 0.22;
  float listenRing = exp(-pow((r - listenRadius) / (3.0 * pr + 0.012), 2.0));
  color += uRim * listenRing * uListen * (0.05 + uLevel * 0.85);

  float rippleRadius = 1.0 + uRippleAge * 0.55;
  float ripple = exp(-pow((r - rippleRadius) / (3.0 * pr + 0.01), 2.0)) * uRipple * (1.0 - uRippleAge);
  color += uHigh * ripple * 0.9;

  // Sphere interior.
  float inside = 1.0 - smoothstep(1.0 - 1.8 * pr, 1.0, r);
  if (inside > 0.0) {
    vec2 surface = p / radius;
    vec3 n = vec3(surface, sqrt(max(0.0, 1.0 - dot(surface, surface))));
    vec3 q = rotateY(uPhase * 0.32 + uPointer.x * 0.45) * rotateX(-uPhase * 0.11 - uPointer.y * 0.45) * n;
    // Parallax: deeper layers move less, giving the glass visible thickness.
    vec3 deepQ = q * (1.0 - n.z * 0.22);
    float swirl = 0.7 + uThink * 1.4;
    float w1 = fbm(deepQ * 0.62 + vec3(0.0, 0.0, uPhase * 0.50));
    float w2 = fbm(deepQ * 0.85 + vec3(w1 * swirl, -w1 * 0.8, uPhase * 0.34));
    float field = fbm(deepQ * 0.70 + vec3(w2 * 1.5, w1 * 1.2, uPhase * 0.20));
    float energy = uLevel * (0.55 + uSpeak * 0.6);
    float bands = smoothstep(-0.55, 0.70, field + energy * 0.22);
    // Silk: thin luminous filaments folded along the flow field.
    float silk = 0.5 + 0.5 * sin(field * 6.5 + w2 * 3.0 + uPhase * 1.1);
    silk = pow(silk, 22.0) * (0.16 + uLevel * 0.65 + uThink * 0.25);
    float veins = 1.0 - abs(snoise(q * 1.5 + vec3(w2 * 1.6, w1, uPhase * 0.6)));
    veins = pow(veins, 16.0) * (0.18 + uLevel * 1.1 + uThink * 0.35);

    vec3 body = mix(uDeep, uMid, bands);
    // Volume: light pools toward the upper left, depth settles toward the lower right.
    body = mix(body, uDeep * 0.7, smoothstep(-0.2, 0.9, -n.x * 0.35 - n.y * 0.65) * 0.45);
    body = mix(body, uHigh, smoothstep(0.38, 1.0, field + energy * 0.42) * 0.72);
    body += uHigh * (veins * 0.45 + silk * 0.55);

    float core = exp(-r * r * 3.4) * (0.10 + uSpeak * uLevel * 1.05 + uListen * uLevel * 0.45 + uThink * 0.08);
    body += mix(uMid, uHigh, 0.7) * core;

    float facing = n.z;
    body *= 0.48 + 0.52 * smoothstep(0.0, 1.0, facing);
    float fresnel = pow(1.0 - facing, 3.2);
    vec3 iris = mix(uRim, uHigh, 0.5 + 0.5 * sin(angle * 2.0 + uPhase * 0.8));
    body += iris * fresnel * (0.85 + uLevel * 0.9 + uHover * 0.25);

    vec3 light = normalize(vec3(-0.42 + uPointer.x * 0.55, 0.58 + uPointer.y * 0.55, 0.78));
    vec3 halfVector = normalize(light + vec3(0.0, 0.0, 1.0));
    float specular = pow(max(dot(n, halfVector), 0.0), 140.0) * 0.55 + pow(max(dot(n, halfVector), 0.0), 18.0) * 0.06;
    float sheen = pow(max(dot(n, light), 0.0), 5.0) * 0.10;
    vec3 bounce = normalize(vec3(0.55, -0.7, 0.45));
    float bounceLight = pow(max(dot(n, bounce), 0.0), 7.0) * 0.16;
    body += vec3(1.0) * specular + uHigh * sheen + uRim * bounceLight;

    float grey = dot(body, vec3(0.299, 0.587, 0.114));
    body = mix(body, vec3(grey) * vec3(1.05, 0.98, 0.86), uDim * 0.45) * (1.0 - uDim * 0.22);

    color = mix(color, body, inside);
  }

  color = 1.0 - exp(-color * 1.35);
  color += (hash(gl_FragCoord.xy + fract(uTime)) - 0.5) / 255.0;
  color = max(color, 0.0);
  // Straight alpha: the halo glows over the page background, the sphere is opaque.
  float alpha = mix(clamp(max(max(color.r, color.g), color.b), 0.0, 1.0), 1.0, inside);
  gl_FragColor = vec4(min(color / max(alpha, 0.0001), 1.0), alpha);
}`;

function createOrbRenderer(canvas) {
  const gl = canvas.getContext("webgl", { alpha: true, premultipliedAlpha: false, antialias: false, powerPreference: "high-performance" });
  if (!gl) return null;
  const compile = (type, source) => {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader) || "Orb shader failed");
    return shader;
  };
  let program;
  try {
    program = gl.createProgram();
    gl.attachShader(program, compile(gl.VERTEX_SHADER, ORB_VERTEX));
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, ORB_FRAGMENT));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program) || "Orb link failed");
  } catch (error) {
    console.warn("HER Voice orb: WebGL unavailable, using 2D fallback.", error);
    return null;
  }
  gl.useProgram(program);
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const position = gl.getAttribLocation(program, "aPosition");
  gl.enableVertexAttribArray(position);
  gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
  const names = ["uRes", "uTime", "uPhase", "uLevel", "uRadius", "uThink", "uListen", "uSpeak", "uDim", "uGlow", "uHover", "uPress", "uRipple", "uRippleAge", "uPointer", "uDeep", "uMid", "uHigh", "uRim"];
  const u = Object.fromEntries(names.map(name => [name, gl.getUniformLocation(program, name)]));
  gl.clearColor(0, 0, 0, 0);
  return {
    draw(frame) {
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.uniform2f(u.uRes, canvas.width, canvas.height);
      for (const key of ["uTime", "uPhase", "uLevel", "uRadius", "uThink", "uListen", "uSpeak", "uDim", "uGlow", "uHover", "uPress", "uRipple", "uRippleAge"]) gl.uniform1f(u[key], frame[key]);
      gl.uniform2f(u.uPointer, frame.pointer[0], frame.pointer[1]);
      gl.uniform3fv(u.uDeep, frame.deep);
      gl.uniform3fv(u.uMid, frame.mid);
      gl.uniform3fv(u.uHigh, frame.high);
      gl.uniform3fv(u.uRim, frame.rim);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    },
    lost: () => gl.isContextLost(),
  };
}

function createFallbackRenderer(canvas) {
  const context = canvas.getContext("2d");
  if (!context) return null;
  const rgba = (color, alpha) => `rgba(${color.map(value => Math.round(value * 255)).join(",")},${alpha})`;
  return {
    draw(frame) {
      const { width, height } = canvas;
      context.setTransform(1, 0, 0, 1, 0, 0);
      context.clearRect(0, 0, width, height);
      const cx = width / 2;
      const cy = height / 2;
      const radius = Math.min(width, height) / 2 * frame.uRadius * (1 + frame.uLevel * .085 + frame.uHover * .022 - frame.uPress * .035 + Math.sin(frame.uTime * 1.05) * .01);
      const halo = context.createRadialGradient(cx, cy, radius * .9, cx, cy, radius * 1.9);
      halo.addColorStop(0, rgba(frame.rim, .22 + frame.uLevel * .3));
      halo.addColorStop(1, rgba(frame.rim, 0));
      context.fillStyle = halo;
      context.fillRect(0, 0, width, height);
      const ox = Math.cos(frame.uPhase) * radius * .18;
      const oy = Math.sin(frame.uPhase * .8) * radius * .14;
      const body = context.createRadialGradient(cx + ox - radius * .3, cy + oy - radius * .35, radius * .05, cx, cy, radius);
      body.addColorStop(0, rgba(frame.high, 1));
      body.addColorStop(.45, rgba(frame.mid, 1));
      body.addColorStop(.92, rgba(frame.deep, 1));
      body.addColorStop(1, rgba(frame.rim, 1));
      context.beginPath();
      context.arc(cx, cy, radius, 0, Math.PI * 2);
      context.fillStyle = body;
      context.fill();
    },
    lost: () => false,
  };
}

/** Live, procedural sound visualization. No inferred/synthetic mic readings. */
function createSoundField(canvas, waveform, orbButton) {
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const field = canvas.parentElement;
  const bars = Array.from({ length: 33 }, () => {
    const bar = document.createElement("span");
    waveform.append(bar);
    return bar;
  });
  let renderer = createOrbRenderer(canvas) || createFallbackRenderer(canvas);
  const current = {};
  for (const key of ORB_COLORS) current[key] = hexColor(ORB_STATES.idle[key]);
  for (const key of ORB_SCALARS) current[key] = ORB_STATES.idle[key];
  let target = ORB_STATES.idle;
  let frame = 0;
  let lastTime = 0;
  let phase = 0;
  let input = 0;
  let output = 0;
  let level = 0;
  let hover = 0;
  let hoverTarget = 0;
  let press = 0;
  let pressTarget = 0;
  let ripple = 0;
  let rippleStart = -10;
  const pointer = [0, 0];
  const pointerTarget = [0, 0];
  let state = "idle";
  let active = false;
  let destroyed = false;
  let clock = 0;

  function drawBars(time) {
    const barLevel = state === "paused" ? 0 : active ? Math.max(input, output) : 0;
    for (let i = 0; i < bars.length; i += 1) {
      const distance = Math.abs(i - 16) / 16;
      const falloff = Math.pow(Math.max(0, 1 - distance), 1.4);
      // The disconnected, static motif is visual identity, never a mic meter.
      const idleShape = !active ? (Math.sin(i * 1.61) ** 2) * falloff * 12 : 0;
      const liveShape = barLevel * falloff * (13 + 31 * Math.abs(Math.sin(i * .9 + time * 4)));
      const barHeight = Math.min(44, 2 + idleShape + liveShape);
      bars[i].style.height = `${barHeight.toFixed(1)}px`;
      bars[i].style.opacity = `${(.22 + .6 * falloff).toFixed(2)}`;
    }
  }

  function step(dt, time) {
    const ease = rate => 1 - Math.exp(-dt * rate);
    for (const key of ORB_COLORS) {
      const goal = hexColor(target[key]);
      for (let c = 0; c < 3; c += 1) current[key][c] += (goal[c] - current[key][c]) * ease(3.2);
    }
    for (const key of ORB_SCALARS) current[key] += (target[key] - current[key]) * ease(3.2);
    const levelTarget = state === "paused" || !active ? 0 : Math.max(input, output);
    level += (levelTarget - level) * ease(levelTarget > level ? 22 : 5.5);
    hover += (hoverTarget - hover) * ease(9);
    pressTarget *= Math.exp(-dt * 7);
    press += (pressTarget - press) * ease(26);
    for (let c = 0; c < 2; c += 1) pointer[c] += (pointerTarget[c] - pointer[c]) * ease(4.5);
    phase += dt * (current.speed + level * .55);
    const rippleAge = Math.min(1, (time - rippleStart) / 1.1);
    if (rippleAge >= 1) ripple = 0;
    return rippleAge;
  }

  function draw(milliseconds = performance.now()) {
    if (destroyed || !renderer) return;
    const still = reducedMotion.matches;
    const time = milliseconds / 1000;
    const dt = still ? 1 : Math.min(.1, Math.max(0, time - (lastTime || time)));
    lastTime = time;
    clock = still ? 0 : clock + dt;
    const rippleAge = step(dt, time);
    renderer.draw({
      uTime: clock, uPhase: still ? 0 : phase, uLevel: level, uRadius: .60,
      uThink: current.think, uListen: current.listen, uSpeak: current.speak, uDim: current.dim, uGlow: current.glow,
      uHover: hover, uPress: press, uRipple: still ? 0 : ripple, uRippleAge: rippleAge, pointer,
      deep: current.deep, mid: current.mid, high: current.high, rim: current.rim,
    });
    drawBars(still ? 0 : clock);
  }

  function tick(milliseconds) {
    frame = 0;
    if (destroyed || document.hidden || reducedMotion.matches) return;
    draw(milliseconds);
    frame = requestAnimationFrame(tick);
  }

  function schedule() {
    if (destroyed) return;
    if (reducedMotion.matches || document.hidden) {
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
      lastTime = 0;
      draw();
    } else if (!frame) {
      lastTime = 0;
      frame = requestAnimationFrame(tick);
    }
  }

  function resize() {
    const bounds = canvas.getBoundingClientRect();
    const ratio = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.max(1, Math.round(bounds.width * ratio));
    canvas.height = Math.max(1, Math.round(bounds.height * ratio));
    field.style.setProperty("--orb-size", `${Math.round(Math.min(bounds.width, bounds.height) * .62)}px`);
    draw();
    schedule();
  }

  const events = new AbortController();
  const on = (targetElement, name, handler, options = {}) => targetElement.addEventListener(name, handler, { ...options, signal: events.signal });
  on(field, "pointermove", event => {
    const bounds = field.getBoundingClientRect();
    const half = Math.min(bounds.width, bounds.height) / 2 || 1;
    pointerTarget[0] = Math.max(-1, Math.min(1, (event.clientX - bounds.left - bounds.width / 2) / half));
    pointerTarget[1] = Math.max(-1, Math.min(1, -(event.clientY - bounds.top - bounds.height / 2) / half));
  });
  on(field, "pointerleave", () => { pointerTarget[0] = 0; pointerTarget[1] = 0; });
  on(orbButton, "pointerenter", () => { hoverTarget = 1; });
  on(orbButton, "pointerleave", () => { hoverTarget = 0; });
  on(orbButton, "focus", () => { hoverTarget = orbButton.matches(":focus-visible") ? 1 : hoverTarget; });
  on(orbButton, "blur", () => { hoverTarget = 0; });
  on(orbButton, "pointerdown", () => { pressTarget = 1; });
  on(canvas, "webglcontextlost", event => { event.preventDefault(); renderer = null; });
  on(canvas, "webglcontextrestored", () => { renderer = createOrbRenderer(canvas); schedule(); });
  on(reducedMotion, "change", schedule);
  on(document, "visibilitychange", schedule);
  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  resize();

  return {
    setState(next, isActive) {
      if (next === "interrupted" && state !== "interrupted") this.pulse();
      state = next;
      active = isActive;
      target = ORB_STATES[next] || ORB_STATES.idle;
      if (!active) { input = 0; output = 0; }
      if (reducedMotion.matches) draw();
    },
    setLevels(nextInput, nextOutput) {
      input = nextInput;
      output = nextOutput;
      if (reducedMotion.matches) draw();
    },
    pulse() {
      ripple = 1;
      rippleStart = performance.now() / 1000;
      pressTarget = Math.max(pressTarget, .6);
    },
    destroy() {
      destroyed = true;
      if (frame) cancelAnimationFrame(frame);
      observer.disconnect();
      events.abort();
    },
  };
}
