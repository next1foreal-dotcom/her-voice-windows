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
  idle: ["准备好听你说。", "从一个念头，开始一段对话。"],
  listening: ["我在听，请说。", "可以自然说话，也可以随时插话。"],
  thinking: ["让我想一想。", "正在生成回复。"],
  speaking: ["正在回复你。", "你可以直接开口打断我。"],
  paused: ["对话已暂停。", "已完成的对话会保留；未说完的内容，恢复后请重说。"],
  interrupted: ["我在听，你继续。", "已停止播放，请继续说。"],
};

function text(value) {
  return typeof value === "string" ? value : "";
}

export function startupCopy(engine = {}) {
  const loadedDrama = engine.tts_backend === 'drama3' && engine.tts_process?.pool?.model_loaded === true;
  const heading = loadedDrama ? '正在预热语音引擎。' : '正在准备语音服务。';
  let supporting = text(engine.detail) || (loadedDrama
    ? 'Drama 3 已从本机加载，正在准备首段语音与会话。'
    : '正在初始化本机语音引擎。');
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
    if (!element) throw new Error(`Open OS1 UI element is missing: ${id}`);
    return element;
  };
  const el = Object.fromEntries([
    "voice-app", "connection-label", "history-button", "settings-button",
    "mode-notice", "error-notice", "error-message", "error-settings",
    "sound-canvas", "waveform", "user-caption", "user-caption-text",
    "main-caption", "supporting-caption", "interrupt-button",
    "text-composer", "message-input", "send-button",
    "mic-button", "mic-label", "captions-button", "call-button", "call-label",
    "sound-button", "keyboard-button", "mode-button",
    "mode-button-label", "settings-dialog", "history-dialog", "mic-device",
    "output-device", "captions-setting", "barge-in-setting",
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
    el["keyboard-button"].setAttribute("aria-label", open ? "收起文字输入" : "显示文字输入");
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
      announce("请先开始通话，再发送消息。");
      return;
    }
    if (snapshot.paused) {
      announce("当前已暂停，请先恢复对话再发送消息。");
      return;
    }
    invoke("onSend", value);
    el["message-input"].value = "";
    updateComposer();
  }

  listen(el["call-button"], "click", () => {
    invoke(snapshot.connection === "connected" || snapshot.connection === "connecting" ? "onStop" : "onStart");
  });
  function activateRibbon() {
    if (snapshot.voiceAdmin?.busy) return;
    if (snapshot.connection === "connecting") return invoke("onStop");
    if (!connected()) return invoke("onStart");
    if (["thinking", "speaking"].includes(snapshot.state) && !snapshot.paused) return invoke("onInterrupt");
    invoke("onPause");
  }
  listen(el["sound-canvas"], "click", activateRibbon);
  listen(el["sound-canvas"], "keydown", event => {
    if (event.repeat || event.isComposing || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "Enter" || event.code === "Space") {
      event.preventDefault(); event.stopPropagation(); activateRibbon();
    }
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
        ["audioinput", "mic-device", "系统默认麦克风"],
        ["audiooutput", "output-device", "系统默认扬声器"],
      ]) {
        const options = [new Option(defaultLabel, "")];
        let number = 0;
        for (const device of devices.filter((item) => item.kind === kind && item.deviceId && item.deviceId !== "default")) {
          number += 1;
          options.push(new Option(text(device.label) || `${kind === "audioinput" ? "麦克风" : "播放设备"} ${number}`, device.deviceId));
        }
        el[id].replaceChildren(...options);
      }
    }
    for (const [key, id] of [["micDeviceId", "mic-device"], ["outputDeviceId", "output-device"]]) {
      const value = text(snapshot.settings[key]);
      if (value && !Array.from(el[id].options).some((option) => option.value === value)) {
        el[id].add(new Option("所选设备（当前未列出）", value));
      }
      el[id].value = value;
    }
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
      row.querySelector(".message-role").textContent = message.role === "user" ? "你" : "Open OS1";
      const statusLabels = { interrupted: "已打断", cancelled: "已停止", failed: "未完成", streaming: message.role === "user" ? "辨识中" : "回复中", partial: "辨识中" };
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
      ? [duration, sampleRate].filter(Boolean).join(" · ")
      : "";
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
      ? { disconnected: "演示待命", connecting: "准备演示", connected: "演示演练", error: "演示中断" }
      : { disconnected: "未连接", connecting: "连接中", connected: "已连接", error: "连接中断" };
    const connectionLabel = paused && isConnected ? demo ? "演示已暂停" : "已暂停" : connectionLabels[connection];
    const stateLabels = {idle: "待命", listening: "正在聆听", thinking: "正在思考", speaking: "正在回复", interrupted: "已打断", paused: "已暂停"};
    el["connection-label"].textContent = isConnected ? stateLabels[voiceState] : connection === "disconnected" ? "准备通话" : connectionLabel;
    el["sound-canvas"].setAttribute("aria-label", snapshot.voiceAdmin?.busy ? "正在应用声音设置" : connection === "connecting" ? "取消连接" : !isConnected ? "开始通话" : paused ? "恢复对话" : ["thinking", "speaking"].includes(voiceState) ? "打断回复" : "暂停对话");
    el["mode-notice"].hidden = !demo;
    el["mode-notice"].textContent = "演示";
    el["mode-button-label"].textContent = demo ? "切回真实连接" : "试用演示";
    el["mode-button"].setAttribute("aria-label", demo ? "切换至真实语音连接" : "切换至演示模式，不使用麦克风或 API");
    const applicationName = text(snapshot.health?.app_name) || "Open OS1";

    document.title = applicationName;
    document.querySelector(".brand").textContent = applicationName;
    const wordmark = document.querySelector(".wordmark");
    if (wordmark) {
      wordmark.setAttribute("aria-label", `${applicationName} 首页`);
      wordmark.querySelector(".wordmark-her").textContent = applicationName;
      wordmark.querySelector(".wordmark-voice").hidden = true;
      wordmark.querySelector(".wordmark-divider").hidden = true;
    }
    document.querySelectorAll(".panel-context").forEach(element => { element.textContent = applicationName; });
    el["error-notice"].hidden = !text(snapshot.error);
    el["error-message"].textContent = text(snapshot.error);

    let [heading, supporting] = STATE_COPY[voiceState];
    if (connection === "connecting") {
      heading = demo ? "正在准备演示。" : "正在连接语音服务。";
      supporting = demo ? "这是协议演练，不会辨识你的声音。" : "若浏览器询问，请允许麦克风。";
      if (!demo && snapshot.startupStage === 'microphone') {
        heading = '语音服务已就绪。';
        supporting = '正在启用选定的麦克风，请确认输入设备与页面权限。';
      } else if (!demo && snapshot.health?.engine?.state === "starting") {
        [heading, supporting] = startupCopy(snapshot.health.engine);
      }
    } else if (connection === "error") {
      heading = "暂时无法连接。";
      supporting = "检查本机服务后，重新开始通话。";
    } else if (!isConnected) {
      heading = demo ? "先感受对话的节奏。" : STATE_COPY.idle[0];
      supporting = demo ? "演示会显示字幕并播放测试音。" : STATE_COPY.idle[1];
    } else if (!snapshot.settings.bargeIn && voiceState === "speaking") {
      supporting = "正在播放回复。";
    } else if (!snapshot.settings.bargeIn && voiceState === "listening") {
      supporting = "说出你的想法，我会在你说完后回应。";
    }
    if (demo && isConnected) {
      const demoCopy = {
        idle: ["演示已准备好。", "输入文字，体验字幕与测试音频。"],
        listening: ["输入一句话，开始演练。", "这里只演练字幕、播放与打断流程。"],
        thinking: ["正在准备演示回复。", "演示内容与测试音不来自语音模型。"],
        speaking: ["正在播放演示。", "点击「打断回复」，试试中途接话。"],
        interrupted: ["演示已打断。", "可输入下一句，或继续操作控制列。"],
        paused: ["演示已暂停。", "已完成的文字会保留；恢复后可以继续输入。"],
      };
      [heading, supporting] = demoCopy[voiceState] || demoCopy.idle;
    }
    const showTranscript = Boolean(snapshot.settings.captions && isConnected && text(snapshot.caption.assistant) && !paused && voiceState !== "interrupted");
    el["main-caption"].textContent = showTranscript ? text(snapshot.caption.assistant) : heading;
    el["main-caption"].classList.toggle("has-transcript", showTranscript);
    el["supporting-caption"].textContent = supporting;
    const showConnectionDetail = ["connecting", "error"].includes(connection);
    el["main-caption"].hidden = !showTranscript && !showConnectionDetail;
    el["supporting-caption"].hidden = !showConnectionDetail;
    const showUser = Boolean(snapshot.settings.captions && isConnected && text(snapshot.caption.user));
    el["user-caption"].hidden = !showUser;
    el["user-caption-text"].textContent = text(snapshot.caption.user);
    el["user-caption-text"].title = text(snapshot.caption.user);
    el["interrupt-button"].hidden = !isConnected || !["thinking", "speaking"].includes(voiceState);
    el["mic-button"].disabled = !isConnected;
    el["mic-button"].setAttribute("aria-pressed", String(paused));
    el["mic-button"].setAttribute("aria-label", paused ? "继续通话" : "暂停通话");
    el["mic-label"].textContent = paused ? "继续" : "暂停";
    setIcon(el["mic-button"], paused ? "mic-off" : "mic");
    el["captions-button"].setAttribute("aria-pressed", String(Boolean(snapshot.settings.captions)));
    el["captions-button"].setAttribute("aria-label", snapshot.settings.captions ? "隐藏字幕" : "显示字幕");
    el["call-button"].classList.toggle("is-active", isConnected);
    el["call-button"].setAttribute("aria-busy", String(connection === "connecting"));
    el["call-button"].disabled = Boolean(snapshot.voiceAdmin?.busy);
    el["call-label"].textContent = connection === "connecting" ? "取消呼叫" : isConnected ? "挂断" : demo ? "开始演示" : "开始通话";
    setIcon(el["call-button"], isConnected || connection === "connecting" ? "end" : "phone");
    el["captions-setting"].checked = Boolean(snapshot.settings.captions);
    el["barge-in-setting"].checked = Boolean(snapshot.settings.bargeIn);
    el["service-mode"].textContent = demo ? "演示协议演练" : "真实连接";
    const engineLabels = { ready: "已就绪", starting: "加载中", failed: "启动失败", stopped: "尚未启动", stopping: "停止中" };
    el["service-engine"].textContent = engineLabels[snapshot.health?.engine?.state] || "尚未检查";
    el["service-profile"].textContent = text(snapshot.health?.profile) || "尚未检查";
    const providerReady = snapshot.health?.provider_ready ?? snapshot.health?.api_key_configured;
    el["service-key"].textContent = providerReady === true ? "设置已齐备，待实际连接" : providerReady === false ? "尚未设置" : "尚未检查";
    renderDevices();
    renderHistory();
    renderVoiceSettings();
    updateComposer();
    sound.setState(isConnected ? voiceState : connection === "connecting" ? "thinking" : "idle", isConnected);
    announce(`${demo ? "演示模式，" : ""}${connectionLabel}${showConnectionDetail ? "。" + heading : ""}`);
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

/* Her Voice Win / OS¹ reference-fit reconstruction.
 * Procedural WebGL ribbon; Canvas2D fallback. No video, image, texture, atlas,
 * or per-frame vector playback. Analytic curve + compact Fourier deformation/width/opacity functions each tick.
 * Original used only for offline fitting and side-by-side QA.
 * The annular ribbon de-twists into a single ring using the same mesh.
 * Renderer only; createSoundField below receives VoiceClient state and audio levels. */
(function(global){
'use strict';
const FIT={"R":246.47314884694768,"H":56.23224548129997,"ex":0.19488482263878898,"K":4.066937804453259,"t0":1.9117729371420353,"w":-10.863007899287409,"W":21.099525550778324,"b0":1.3352040524110054,"b1":-1.0329263981839685,"amin":0.03466537834674127,"amax":0.8056924170245459,"contrast":-0.3208459500996237,"ringR":98.5,"ringW":17,"ringDY":45};
const FIELD={"spatial":12,"temporal":4,"cx":[-0.141632,-1.216982,0.029258,1.222655,0.484328,3.487129,-0.371835,0.053386,0.132915,0.826048,0.050531,-0.145336,-0.058419,0.354745,0.032107,0.014517,-0.007036,-0.180696,-0.020551,-0.003359,-0.008739,-0.094189,0.018478,-0.019312,0.006058,0.064042,-0.185681,0.061068,0.372894,0.356778,0.069031,1.265134,-0.121621,0.162958,-0.101676,-0.566661,-0.028936,0.009162,-0.001069,0.109015,0.011171,0.001238,-0.008324,0.010819,-0.004029,0.002856,-0.00382,-0.011415,0.002993,-8e-06,-0.16299,-0.224955,0.276685,0.067597,3.606705,-0.197003,-0.034662,0.194538,0.146337,-0.283976,-0.059301,0.028041,0.195701,-0.038042,-0.070808,0.008037,0.018792,-0.008042,-0.01849,0.017447,-0.004879,-0.004037,0.003324,-0.00711,-0.004635,0.017893,3.053546,-0.215373,-0.117168,-0.347075,-1.478214,0.240574,-0.250134,-0.062399,-0.865301,0.0216,0.021895,-0.008058,-0.005288,0.013419,0.035049,0.043431,-0.012569,0.00712,0.01807,0.000698,0.020101,-0.004813,0.006742,-0.005219,1.188253,-0.272179,0.220016,-0.984184,0.050889,0.367453,0.099848,-1.232392,0.068778,0.153123,-0.023424,0.061513,-0.04207,-0.038895,0.02062,0.161292,-0.007918,0.003776,-0.013101,0.012811,-0.000794,0.000748,0.005759,-0.008373,-0.000265,0.199958,0.047391,0.951945,-0.145109,-0.113065,-0.017591,0.931573,-0.05766,-0.103744,0.020019,0.112397,0.0269,-0.019922,0.01295,-0.035828,0.008435,-0.008021,-0.00161,0.002156,0.007394,-0.000277,0.002027,0.000696,-0.003155,0.000274,-0.039125,-0.037606,-0.20288,0.06566,-0.097803,0.089125,-0.130916,-0.046617,-0.32435,0.148826,0.032377,-0.008416,-0.155435,0.01854,0.045432,-0.007926,-0.005311,0.00447,0.011772,0.000712,0.001951,0.000308,-0.001637,0.000806,-0.003431,-0.012071,-0.153746,0.077414,0.043872,0.12176,-0.126792,-0.019149,0.112381,0.057867,0.245122,-0.005012,0.029599,-0.022668,0.155639,-0.003442,-0.041508,-0.025943,-0.004922,-0.005939,-0.018354,0.003405,-0.010722,0.006664,-0.001135,0.004745,0.206081,-0.023477,-0.031991,-0.004973,-0.018334,0.009307,-0.053924,0.005198,0.004197,-0.062497,-0.00712,0.044703,0.004151,-0.028628,0.005445,-0.007017,0.00015,0.001737,-0.002258,-0.030436,0.005446,-0.000794,0.001062,0.001617,-0.003763],"cy":[-0.309524,1.135516,-0.15516,0.154266,0.049349,-1.56104,-0.162804,0.244701,-0.059599,0.14401,0.11897,0.003167,0.017298,0.083994,-0.014589,-0.029782,-0.00234,0.086281,-0.017739,0.010659,0.003017,-0.067634,0.011932,-0.009784,-0.000567,0.503282,-0.069362,1.769806,-0.734473,0.144803,0.087024,-0.568537,0.066969,-0.016957,0.092263,0.064508,0.094422,-0.003315,-0.035066,0.144434,-0.031401,-0.021459,-0.003563,-0.017919,0.008243,0.015856,0.003194,-0.029855,-0.00206,-0.003159,0.31312,-0.341195,-0.494155,-0.44616,1.826967,0.224702,-0.17629,0.20985,1.908321,0.163344,0.283494,-0.022614,-1.446551,-0.050105,-0.164049,-0.042326,0.320979,-0.014107,0.033987,0.044579,0.025529,0.005387,0.00492,-0.026106,-0.028856,0.390639,0.427858,0.190218,0.041805,0.264822,0.353913,0.074793,-0.393338,-0.115156,-0.318957,-0.067242,-0.046572,0.048299,0.028911,0.009363,0.053598,-0.02099,0.001883,0.009165,-0.01317,-0.0017,-0.047221,-0.005351,0.005178,0.003511,1.221777,-0.175051,-0.040162,-0.037894,-0.177989,0.147046,-0.073862,-1.127815,0.059665,0.008628,0.043436,0.288572,-0.001637,-0.013049,-0.051773,0.062702,-0.014724,0.01885,0.026932,-0.108303,0.011873,-0.004906,-0.004234,0.060935,-0.004917,0.122165,-0.08785,0.620184,0.11848,-0.311574,0.154782,0.36633,-0.118989,0.054537,-0.128909,-0.499782,-0.03731,0.074644,0.031089,0.024409,0.023377,-0.042019,0.007212,-1.5e-05,-0.006201,0.013354,-0.00237,0.04505,-0.001989,-0.001137,-0.054291,0.090575,-0.212576,0.053469,-0.561302,-0.082004,-0.103248,-0.047809,0.073528,-0.101783,0.172959,-0.005595,0.073284,0.037695,-0.018967,0.029275,-0.055572,0.013131,0.004324,-0.010115,0.05567,-0.002637,-0.008009,0.001303,-0.039204,-0.154002,0.214781,-0.030752,-0.013326,0.041697,-0.415746,-0.000322,0.143636,-0.029977,-0.123385,0.014988,0.011479,-0.052865,0.141988,-0.004713,-0.015966,0.031673,0.023341,-0.00573,0.001326,0.002824,-0.023071,0.00257,-0.001744,-0.004567,0.007186,-0.018303,0.006069,-0.034296,-0.030447,0.05,-0.004742,-0.03798,0.030502,0.012056,0.027087,0.062713,-0.002976,-0.035444,0.015206,0.039432,-0.008164,-0.002136,-0.012852,-0.02225,0.006458,0.006297,0.001065,0.001223,-0.002651],"cw":[2.477207,-0.070361,0.080545,-0.031339,-0.015547,-0.029435,0.126606,-0.004031,0.001489,-0.008455,0.06,-0.005059,0.01104,-0.002184,0.00151,-0.003992,0.002126,-0.00261,-0.004624,0.000784,-0.001779,0.003753,0.010018,0.000364,0.00222,0.030178,0.265007,-0.016908,0.057016,0.006479,0.080714,0.006653,0.012541,-0.016222,-0.050609,0.00339,-0.005029,-0.029123,0.014156,-0.003955,0.002081,0.005694,-0.003337,0.002531,0.000772,-0.002576,0.000345,-0.000403,0.000184,0.000996,-0.080184,-0.013324,0.069427,-0.058824,-0.021679,0.007121,0.073657,-0.056032,-0.01218,-0.00806,2.9e-05,-0.056838,0.00796,-0.010193,0.023044,0.00107,-0.00074,0.001458,-0.000444,-0.000558,0.00149,0.002405,0.003247,0.000278,0.000163,0.031423,-0.154249,-0.015806,0.024348,-0.035134,0.113282,-0.31444,-0.035479,0.030375,-0.031219,0.132163,-0.005404,-0.00499,0.009473,-0.059593,0.01782,-0.003788,0.002148,0.01803,-0.005194,0.004482,0.000828,-0.004051,-0.001104,-0.001895,0.01777,-0.118529,-0.019857,0.072029,-0.354427,-0.052303,0.010097,-0.064876,0.213788,0.017362,-0.014152,0.035529,-0.081304,-0.010069,0.01146,-0.018088,0.014334,0.001313,-0.008052,0.00737,-0.003959,-0.003037,0.002382,-0.001219,0.000799,0.001344,0.012446,-0.000593,0.007036,0.045149,-0.037074,-0.005869,-0.014162,0.006966,0.010765,0.002732,0.001339,0.017521,0.037377,0.004276,0.006253,-0.004609,-0.012028,-0.002975,-0.002031,0.002135,0.005429,0.001747,4e-06,0.0011,-0.002614,-0.018161,0.007739,0.008169,-0.007923,0.024726,0.024379,0.054188,0.020665,-0.004025,-0.008634,0.035117,0.002448,0.001369,-0.016246,-0.015133,-0.008089,-0.001294,0.001666,0.011893,0.001915,-0.000526,-0.002879,-0.006414,0.000173,0.04229,0.014805,0.008085,0.009528,-0.026442,-0.014638,0.030937,-0.031797,-0.004622,-0.009067,0.028206,0.042509,0.02243,0.006167,-0.013712,-0.034407,-0.015808,0.001473,-0.002161,0.010671,0.007546,-0.00413,0.005283,-0.004722,-0.004324,-0.01914,-0.017142,-0.020418,-0.008558,0.011505,0.005037,-0.0077,0.019348,0.012957,0.028512,0.028149,-0.008631,-0.020777,-0.029298,-0.026135,0.005424,0.003242,0.019388,0.011752,-0.004287,0.005001,-0.005782,-0.004685,0.001333,-0.003554],"ca":[-1.186474,0.013877,0.00324,-0.116586,0.011761,-0.017141,0.000252,0.300225,0.009731,0.004602,0.007164,-0.076058,0.005019,-0.007753,-0.003781,0.014425,0.000895,-0.000891,-0.009454,0.011822,-0.007114,0.012887,0.013504,-0.008568,0.003286,0.016115,0.062567,0.049663,0.024615,0.364397,-0.01809,0.030171,-0.003617,0.177569,-0.005669,-0.052255,0.003837,-0.280289,-0.004731,-0.004323,0.005421,0.215109,-0.006579,0.013475,-0.007386,-0.114482,0.000166,-0.002346,0.004463,0.04136,0.025687,0.014554,0.946443,0.048548,-0.141746,0.012017,0.668901,0.002529,-0.041398,-0.013227,-0.416884,0.003263,0.030685,-0.005833,0.243355,0.006924,0.009211,0.006108,-0.138605,-0.004327,-0.00586,-0.004537,0.055497,0.010834,-6.1e-05,0.156186,-0.104619,0.018483,-0.014087,0.005326,0.099562,-0.050172,-0.144237,0.013104,0.055325,0.020941,0.01574,1.1e-05,-0.019981,-0.029922,-0.018371,-0.016319,-0.005898,0.007578,0.000487,0.011545,-0.002744,-0.005075,0.016754,-0.000575,-0.012313,-0.139995,0.016535,0.02235,-0.15255,0.118477,0.000843,0.02774,0.04806,0.086719,-0.017394,0.00322,-0.018242,-0.07253,0.019381,-0.029845,0.00492,0.040382,-0.013731,0.012718,0.003646,-0.006092,0.004915,0.002878,0.003359,-0.001233,-0.083055,0.049958,-0.021721,-0.069849,0.037063,0.060593,-0.002111,-0.050336,0.032457,0.006766,0.020634,0.017218,0.007966,-0.003456,-0.000816,-0.009097,-0.002205,-0.004346,-0.005981,0.007244,0.002312,-0.003671,0.003001,-0.003167,0.024714,-0.020623,-0.056302,0.019258,0.070879,0.022492,-0.07294,-0.011595,0.000118,0.000422,0.000753,-0.003618,-0.043893,0.006921,0.004277,-0.01254,0.005211,-0.002625,0.001132,-0.00279,0.006289,0.00037,-0.001186,-0.000511,-0.005219,0.011039,0.056602,-0.006506,-0.026958,-0.049444,-0.035736,0.029873,-0.012146,-0.004196,-0.061719,0.008924,0.038083,0.019906,0.024009,0.005203,-0.029998,-0.017907,0.016536,0.005673,0.013092,0.00802,-0.010698,3e-05,-0.010294,-0.003361,-0.026191,-0.000676,-0.026831,-0.021287,0.060649,0.009999,0.002833,0.036799,0.027065,0.010662,0.039231,0.003231,-0.020184,-0.033391,-0.031307,0.012536,-0.001561,0.019172,0.013809,-0.003026,-0.001548,-0.004968,-0.001639,-0.00342,-0.001332]};
const BG=[0xDC/255,0x5C/255,0x35/255]; // #DC5C35 固定
const INK=[1.0,0.871,0.769];            // 暖象牙 #FFDEC4（原片圆环实测）
const STATES=['idle','listening','thinking','speaking','interrupted'];
const TARGET={ // 各状态目标；所有量都经弹簧平滑，可中途改状态
  idle:       {m:0,spd:1.00,hm:1.00,rip:0,   pinch:0, lvl:0},
  listening:  {m:0,spd:0.55,hm:0.82,rip:0.35,pinch:0, lvl:1},
  thinking:   {m:1,spd:1.00,hm:1.00,rip:0,   pinch:0, lvl:0},
  speaking:   {m:0,spd:1.25,hm:1.05,rip:1.0, pinch:0, lvl:1},
  interrupted:{m:0,spd:0.30,hm:0.40,rip:0,   pinch:1, lvl:0},
};
const N=640, M=8, RN=240;
function sstep(a,b,x){x=Math.min(1,Math.max(0,(x-a)/(b-a)));return x*x*(3-2*x)}
class Spring{constructor(v,k){this.v=v;this.d=0;this.k=k}
  step(t,dt){const k=this.k,a=k*k*(t-this.v)-2*k*this.d;this.d+=a*dt;this.v+=this.d*dt;return this.v}}

class OS1Ribbon{
  constructor(canvas,opts={}){
    this.cv=canvas;this.events=new AbortController();this.opts=opts;this.state='idle';this.level=0;this.lvS=0;
    this.sp={m:new Spring(0,4.6),spd:new Spring(1,4),hm:new Spring(1,6),rip:new Spring(0,6),pinch:new Spring(0,9),lvl:new Spring(0,8)};
    this.theta=FIT.t0;this.time=0;this.listeners={};this.reduced=!!opts.reducedMotion;
    this.buf=new Float32Array(((M+1)*N+RN)*6*7);this.order=new Uint16Array(N);this.zs=new Float32Array(N);
    this.G=new Float32Array(N*(M+1)*7);this.C=new Float32Array(N);this.F=new Float32Array(N);this.P=new Float32Array(N*2);this.O=new Float32Array(N*2);this.A=new Float32Array(N);this.Z=new Float32Array(N);
    this.mode='none';this._init(opts.renderer);
    this.resize();
  }
  on(e,f){(this.listeners[e]||=[]).push(f);return this}
  _emit(e,v){(this.listeners[e]||[]).forEach(f=>{try{f(v)}catch(err){console.error(err)}})}
  _init(force){
    this.gl=null;this.ctx=null;
    if(force!=='2d'){
      try{this.gl=this.cv.getContext('webgl',{antialias:true,alpha:false,premultipliedAlpha:true,preserveDrawingBuffer:!!this.opts.preserve})}catch(e){this.gl=null}
    }
    if(this.gl&&this._initGL()){this.mode='webgl'}
    else{this.gl=null;try{this.ctx=this.cv.getContext('2d')}catch(e){this.ctx=null}this.mode=this.ctx?'canvas2d':'none'}
    if(!this._lossBound){this._lossBound=true;
      this.cv.addEventListener('webglcontextlost',e=>{e.preventDefault();this.mode='lost';this._emit('error',{type:'context-lost'})},{signal:this.events.signal});
      this.cv.addEventListener('webglcontextrestored',()=>{this._init();this.resize();this._emit('recovered',{mode:this.mode})},{signal:this.events.signal});}
    this._emit('renderer',this.mode);
  }
  _initGL(){
    const gl=this.gl;
    const vs=`attribute vec2 p;attribute vec4 q;attribute float light;uniform vec2 res;varying vec4 v;varying float lit;
      void main(){v=q;lit=light;gl_Position=vec4(p/res*2.-1.,0.,1.);gl_Position.y=-gl_Position.y;}`;
    const fs=`precision mediump float;varying vec4 v;varying float lit;uniform vec3 ink;
      void main(){
        float edge=1.-smoothstep(1.-v.z,1.,abs(v.x));
        // Matte broad shading, not parallel bright rails or a glossy tube.
        float grain=1.-.008*fract(52.9829189*fract(dot(floor(gl_FragCoord.xy),vec2(.06711056,.00583715))));
        // Broad warm transmission, anchored to the moving surface, not screen x.
        float face=smoothstep(0.,1.,lit);
        float crossBand=smoothstep(-1.,1.,v.x);
        vec3 warm=vec3(1.,.735,.565);
        vec3 pigment=mix(warm,ink,clamp(.36+.58*face+.06*crossBand,0.,1.));
        float a=v.y*edge*grain*(.88+.12*crossBand);
        gl_FragColor=vec4(pigment*a,a);
      }`;
    const sh=(t,s)=>{const o=gl.createShader(t);gl.shaderSource(o,s);gl.compileShader(o);
      if(!gl.getShaderParameter(o,gl.COMPILE_STATUS))throw new Error(gl.getShaderInfoLog(o));return o};
    try{
      const pr=gl.createProgram();gl.attachShader(pr,sh(gl.VERTEX_SHADER,vs));gl.attachShader(pr,sh(gl.FRAGMENT_SHADER,fs));
      gl.linkProgram(pr);if(!gl.getProgramParameter(pr,gl.LINK_STATUS))throw new Error(gl.getProgramInfoLog(pr));
      this.pr=pr;this.vb=gl.createBuffer();this.aP=gl.getAttribLocation(pr,'p');this.aQ=gl.getAttribLocation(pr,'q');
      this.aLight=gl.getAttribLocation(pr,'light');this.uRes=gl.getUniformLocation(pr,'res');this.uInk=gl.getUniformLocation(pr,'ink');return true;
    }catch(err){this._emit('error',{type:'webgl-init',message:String(err)});return false}
  }
  resize(){
    const dpr=Math.min(global.devicePixelRatio||1,2),r=this.cv.getBoundingClientRect();
    const w=Math.max(1,Math.round((r.width||this.cv.width)*dpr)),h=Math.max(1,Math.round((r.height||this.cv.height)*dpr));
    if(this.cv.width!==w||this.cv.height!==h){this.cv.width=w;this.cv.height=h}
    const cw=w/dpr,ch=h/dpr;
    // 桌面 1920×1080 → 1:1 原片比例；窄屏幕保证丝带约占 72% 宽
    this.scale=Math.min(cw*0.72/560,ch*0.42/330,Math.max(cw/1920,ch/1080))*dpr;
    if(this.opts.scale)this.scale=this.opts.scale*dpr;
    this.dpr=dpr;
    // Changing canvas size clears its buffer, including when animation is paused.
    this._geom();this._draw();
  }
  setState(s){if(!STATES.includes(s))throw new Error('unknown state '+s);if(s===this.state)return;
    const prev=this.state;this.state=s;this._emit('state',{state:s,prev})}
  setLevel(v){this.level=Math.max(0,Math.min(1,+v||0))}
  get progress(){return {m:this.sp.m.v,spd:this.sp.spd.v,hm:this.sp.hm.v,pinch:this.sp.pinch.v}}
  /* 推进时间 dt 秒并绘制 */
  tick(dt){
    dt=Math.min(Math.max(dt,0),1/15);const T=TARGET[this.state];
    this.lvS+=(this.level*T.lvl-this.lvS)*(1-Math.exp(-dt*16));
    const L=this.lvS;
    const sub=4;for(let i=0;i<sub;i++){const d=dt/sub;
      this.sp.m.step(T.m,d);this.sp.spd.step(T.spd*(1+0.6*L*(this.state==='speaking')),d);
      this.sp.hm.step(T.hm+(this.state==='listening'?0.45*L:this.state==='speaking'?0.5*L:0),d);
      this.sp.rip.step(T.rip*L,d);this.sp.pinch.step(T.pinch,d);}
    const rm=this.reduced?0.3:1;
    this.theta+=FIT.w*this.sp.spd.v*rm*dt;this.time+=dt*rm;
    this._geom();this._draw();
  }
  _geom(){
    const F=FIT,m=Math.min(1,Math.max(0,this.sp.m.v)),hm=this.sp.hm.v,rip=this.sp.rip.v,pin=this.sp.pinch.v;
    const S=this.scale,cx=this.cv.width/2,cy=this.cv.height/2-F.ringDY*S*sstep(0,1,m);
    const fold=sstep(0,.85,m),settle=sstep(.55,.87,m),compact=fold,ex=F.ex;
    const R=F.R*(1-.12*pin);
    const H=F.H*hm*(1-.52*pin*(1-m));
    const K=F.K,phase=this.theta;
    // Evaluate compact Fourier functions, never a frame/path lookup table.
    const sbasis=this._basis||(this._basis=Array.from({length:N},(_,i)=>{const u=i/N*Math.PI*2,b=[1];for(let k=1;k<=FIELD.spatial;k++)b.push(Math.cos(k*u),Math.sin(k*u));return b;}));
    const tb=[1];for(let j=1;j<=FIELD.temporal;j++)tb.push(Math.cos(j*phase),Math.sin(j*phase));
    const coefficients={};for(const name of ['cx','cy','cw','ca']){
      const c=FIELD[name],r=new Float64Array(25);for(let k=0;k<25;k++)for(let j=0;j<tb.length;j++)r[k]+=c[j*25+k]*tb[j];coefficients[name]=r;
    }
    const widths=this._widths||(this._widths=new Float32Array(N)),opacity=this._opacity||(this._opacity=new Float32Array(N));
    const deform=1;
    const X=this._xyz||(this._xyz=new Float32Array(N*3)),B=this._frame||(this._frame=new Float32Array(N*3));
    // A continuously twisted annular curve. Superellipse parameterization keeps
    // the end turns round without long straight facets or under-sampled tips.
    for(let i=0;i<N;i++){
      const u=i/N*Math.PI*2,e=Math.sin(u),co=Math.cos(u);
      const q=Math.sign(co)*Math.sqrt(Math.max(0,1-Math.pow(Math.abs(e),2/ex)));
      const a=K*q+phase,ca=Math.cos(a),sa=Math.sin(a);
      const yy=e*ca,zz=e*sa;
      let fx=0,fy=0,fw=0,fa=0;const bs=sbasis[i];for(let k=0;k<25;k++){fx+=bs[k]*coefficients.cx[k];fy+=bs[k]*coefficients.cy[k];fw+=bs[k]*coefficients.cw[k];fa+=bs[k]*coefficients.ca[k];}
      const originalX=R*q+fx+5*q*Math.pow(Math.abs(q),8),originalY=H*yy-fy*hm+H*.055*rip*Math.sin(5*u-this.time*9)*(1-m);
      // The band folds into a tilted double loop, then unwinds to one circle. All
      // positions belong to the same continuous mesh, including interruptions.
      const psi=phase*.16+.65,rr=F.ringR;
      const fx8=rr*.85*Math.sin(2*u),fy8=rr*Math.sin(u),ang=psi*(1-settle);
      const coilX=(1-settle)*(Math.cos(ang)*fx8-Math.sin(ang)*fy8)+settle*rr*Math.cos(u);
      const coilY=(1-settle)*(Math.sin(ang)*fx8+Math.cos(ang)*fy8)+settle*rr*Math.sin(u);
      X[i*3]=(1-fold)*originalX+fold*coilX+35*Math.sin(Math.PI*m);
      X[i*3+1]=(1-fold)*originalY+fold*coilY;
      X[i*3+2]=(1-fold)*H*zz+fold*rr*.55*(1-settle)*Math.sin(u+psi);
      widths[i]=Math.min(30,Math.max(.45,Math.exp(fw)));opacity[i]=Math.min(.98,Math.max(.025,Math.exp(fa)));
      B[i*3]=q;B[i*3+1]=yy;B[i*3+2]=zz;
    }
    const P=this.P,O=this.O,A=this.A,Z=this.Z,G=this.G;
    const width=(F.W+(F.ringW-F.W)*compact)*(1-.22*pin);
    this.ringMix=0; // The SAME mesh becomes the ring; no ring cross-fade.
    this.ring={x:cx,y:cy,r:F.ringR*S,w:F.ringW*S};
    for(let i=0;i<N;i++){
      const j=(i+1)%N,k=(i+N-1)%N,u=i/N*Math.PI*2;
      let tx=X[j*3]-X[k*3],ty=X[j*3+1]-X[k*3+1],tz=X[j*3+2]-X[k*3+2];
      const tl=Math.hypot(tx,ty,tz)||1;tx/=tl;ty/=tl;tz/=tl;
      let nx=B[i*3],ny=B[i*3+1],nz=B[i*3+2],dot=nx*tx+ny*ty+nz*tz;
      nx-=dot*tx;ny-=dot*ty;nz-=dot*tz;let nl=Math.hypot(nx,ny,nz);
      if(nl<1e-6){const ax=Math.abs(tx)<.8?1:0,ay=ax?0:1,d=ax*tx+ay*ty;nx=ax-d*tx;ny=ay-d*ty;nz=-d*tz;nl=Math.hypot(nx,ny,nz)||1;}
      nx/=nl;ny/=nl;nz/=nl;
      const vx=ty*nz-tz*ny,vy=tz*nx-tx*nz,vz=tx*ny-ty*nx;
      const beta=(F.b0+F.b1*B[i*3])*(1-settle),cb=Math.cos(beta),sb=Math.sin(beta);
      const bx=cb*nx+sb*vx,by=cb*ny+sb*vy;
      const x=cx+X[i*3]*S,y=cy-X[i*3+1]*S,z=X[i*3+2];
      const screenLen=Math.hypot(tx,ty)||1;
      const coilWidth=F.ringW*((.35+.65*Math.abs(Math.cos(u+phase*.16+.65)))*(1-settle)+settle);
      const fittedWidth=(widths[i]+(coilWidth-widths[i])*compact)*(1+.35*Math.sin(Math.PI*m))*(1-.22*pin);
      const ox=ty/screenLen*fittedWidth*.5*S,oy=tx/screenLen*fittedWidth*.5*S;
      let alpha=Math.min(.98,opacity[i]*1.08);
      const coilAlpha=(.25+.70*sstep(-1,1,-Math.sin(u+phase*.16+.65)))*(1-settle)+.94*settle;
      alpha+=(coilAlpha-alpha)*fold;
      P[i*2]=x;P[i*2+1]=y;O[i*2]=ox;O[i*2+1]=oy;A[i]=alpha;Z[i]=z;
      const feather=Math.min(.95,.85*this.dpr/Math.max(.01,fittedWidth*.5*S));
      for(let lane=0;lane<=M;lane++){
        const v=lane/M*2-1;let at=(i*(M+1)+lane)*7;
        G[at++]=x+O[i*2]*v;G[at++]=y+O[i*2+1]*v;G[at++]=v;G[at++]=alpha;G[at++]=feather;G[at++]=u;G[at++]=.5+.5*Math.tanh(z/Math.max(1,H*.75));
      }
      this.C[i]=0;this.F[i]=feather;this.order[i]=i;
    }
    for(let i=0;i<N;i++)this.zs[i]=(Z[i]+Z[(i+1)%N])*.5;
    this.order.sort((a,b)=>this.zs[b]-this.zs[a]);
  }

  _draw(){
    if(this.mode==='webgl')return this._drawGL();
    if(this.mode==='canvas2d')return this._draw2D();
  }
  _drawGL(){
    const gl=this.gl;if(gl.isContextLost())return;
    const G=this.G,P=this.P,buf=this.buf;let o=0;
    const raw=(x,y,v,a,f,u,light=1)=>{buf[o++]=x;buf[o++]=y;buf[o++]=v;buf[o++]=a;buf[o++]=f;buf[o++]=u;buf[o++]=light};
    const put=(i,lane,wrap=false)=>{const a=(i*(M+1)+lane)*7;for(let k=0;k<7;k++)buf[o++]=G[a+k];if(wrap){buf[o-5]*=-1;buf[o-2]=Math.PI*2;}};
    for(let n=0;n<N;n++){
      const i=this.order[n],j=(i+1)%N,ui=i/N*Math.PI*2,uj=ui+Math.PI*2/N;
      const coreAlpha=(this.C[i]+this.C[j])*.5;
      if(coreAlpha>.001){const dx=P[j*2]-P[i*2],dy=P[j*2+1]-P[i*2+1],len=Math.hypot(dx,dy)||1,hw=.6*this.dpr,ox=-dy/len*hw,oy=dx/len*hw;
        const core=(k,sg,u)=>raw(P[k*2]+ox*sg,P[k*2+1]+oy*sg,sg,coreAlpha,.95,u);
        core(i,-1,ui);core(i,1,ui);core(j,1,uj);core(i,-1,ui);core(j,1,uj);core(j,-1,uj);
      }
      const next=l=>l;
      for(let lane=0;lane<M;lane++){
        put(i,lane);put(i,lane+1);put(j,next(lane+1));put(i,lane);put(j,next(lane+1));put(j,next(lane));
      }
    }
    const ring=this.ring;
    const ringPut=(u,sg)=>{const rr=ring.r+sg*ring.w/2;raw(ring.x+Math.cos(u)*rr,ring.y+Math.sin(u)*rr,sg,.90*this.ringMix,Math.min(.95,2*this.dpr/ring.w),u,.97);};
    if(this.ringMix>0)for(let i=0;i<RN;i++){const u=i/RN*Math.PI*2,v=(i+1)/RN*Math.PI*2;ringPut(u,-1);ringPut(u,1);ringPut(v,1);ringPut(u,-1);ringPut(v,1);ringPut(v,-1);}
    gl.viewport(0,0,this.cv.width,this.cv.height);gl.clearColor(BG[0],BG[1],BG[2],1);gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(this.pr);gl.bindBuffer(gl.ARRAY_BUFFER,this.vb);gl.bufferData(gl.ARRAY_BUFFER,buf.subarray(0,o),gl.DYNAMIC_DRAW);
    gl.enableVertexAttribArray(this.aP);gl.vertexAttribPointer(this.aP,2,gl.FLOAT,false,28,0);
    gl.enableVertexAttribArray(this.aQ);gl.vertexAttribPointer(this.aQ,4,gl.FLOAT,false,28,8);
    gl.enableVertexAttribArray(this.aLight);gl.vertexAttribPointer(this.aLight,1,gl.FLOAT,false,28,24);
    gl.uniform2f(this.uRes,this.cv.width,this.cv.height);gl.uniform3f(this.uInk,INK[0],INK[1],INK[2]);
    gl.enable(gl.BLEND);gl.blendFunc(gl.ONE,gl.ONE_MINUS_SRC_ALPHA);gl.drawArrays(gl.TRIANGLES,0,o/7);
  }
  _draw2D(){
    const c=this.ctx,G=this.G,P=this.P;c.setTransform(1,0,0,1,0,0);c.fillStyle='#DC5C35';c.fillRect(0,0,this.cv.width,this.cv.height);
    for(let n=0;n<N;n++){
      const i=this.order[n],j=(i+1)%N,coreAlpha=(this.C[i]+this.C[j])*.5;
      if(coreAlpha>.001){c.strokeStyle=`rgba(255,222,196,${coreAlpha})`;c.lineWidth=.8*this.dpr;c.lineCap='round';c.beginPath();c.moveTo(P[i*2],P[i*2+1]);c.lineTo(P[j*2],P[j*2+1]);c.stroke();}
      const at=(k,l)=>(k*(M+1)+l)*7,next=l=>l;
      // Fill the entire curved cross-section as one outline to avoid Canvas2D
      // antialias cracks between its eight adjacent faces.
      const base=at(i,M/2),a0=at(i,0),a1=at(i,M);
      const grad=c.createLinearGradient(G[a0],G[a0+1],G[a1],G[a1+1]);
      const face=G[base+6]*G[base+6]*(3-2*G[base+6]);
      for(const t of [0,.5,1]){const k=Math.min(1,.36+.58*face+.06*t);
        grad.addColorStop(t,`rgba(255,${(187.425+(222.105-187.425)*k).toFixed(2)},${(144.075+(196.095-144.075)*k).toFixed(2)},${G[base+3]*(.88+.12*t)})`);}
      c.fillStyle=grad;c.beginPath();
      for(let l=0;l<=M;l++){const a=at(i,l);l?c.lineTo(G[a],G[a+1]):c.moveTo(G[a],G[a+1]);}
      for(let l=M;l>=0;l--){const a=at(j,next(l));c.lineTo(G[a],G[a+1]);}c.closePath();c.fill();
    }
    if(this.ringMix>0){const r=this.ring;c.globalAlpha=this.ringMix*.86;c.strokeStyle='#FFDEC4';c.lineWidth=r.w;c.beginPath();c.arc(r.x,r.y,r.r,0,Math.PI*2);c.stroke();c.globalAlpha=1;}
  }

  destroy(){
    this.events.abort();this.listeners={};
    if(this.gl){this.gl.deleteBuffer(this.vb);this.gl.deleteProgram(this.pr);}
    this.mode='none';
  }
  /* 确定性取样（截图/对照用）：从 t=0 起以固定步长模拟 timeline=[[秒,状态],...] */
  simulate(seconds,timeline=[[0,'idle']],fps=60){
    this.theta=FIT.t0;this.time=0;this.lvS=0;this.level=0;for(const k in this.sp){this.sp[k].v=TARGET[timeline[0][1]][k];this.sp[k].d=0}
    this.state=timeline[0][1];const n=Math.round(seconds*fps);let ti=0;
    for(let f=0;f<n;f++){const t=f/fps;while(ti+1<timeline.length&&t>=timeline[ti+1][0]){ti++;this.state=timeline[ti][1]}
      const T=TARGET[this.state];for(let s=0;s<4;s++){const d=1/fps/4;
        this.sp.m.step(T.m,d);this.sp.spd.step(T.spd,d);this.sp.hm.step(T.hm,d);this.sp.rip.step(0,d);this.sp.pinch.step(T.pinch,d)}
      this.theta+=FIT.w*this.sp.spd.v/fps;this.time+=1/fps;}
    this._geom();this._draw();
  }
}
OS1Ribbon.FIT=FIT;OS1Ribbon.STATES=STATES;OS1Ribbon.TARGET=TARGET;
global.OS1Ribbon=OS1Ribbon;
})(typeof window!=='undefined'?window:globalThis);

// VoiceClient remains the only source of state and real audio levels.
function createSoundField(canvas, waveform, orbButton) {
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const events = new AbortController();
  const on = (target, name, fn) => target.addEventListener(name, fn, {signal:events.signal});
  const bars = Array.from({length:33}, () => {const bar=document.createElement('span');waveform.append(bar);return bar;});
  const ribbon = new globalThis.OS1Ribbon(canvas, {reducedMotion:reducedMotion.matches});
  let state='idle', active=false, input=0, output=0, frame=0, last=0, destroyed=false;
  const bound = v => Number.isFinite(v) ? Math.max(0,Math.min(1,v)) : 0;
  function meter() {
    const level=active && state!=='paused' ? (state==='speaking'?output:state==='listening'?input:0) : 0;
    ribbon.setLevel(level);
    canvas.dataset.visualState=state;
    canvas.dataset.audioSource=state==='speaking'?'output':state==='listening'?'input':'none';
    canvas.dataset.renderer=ribbon.mode;
    canvas.dataset.level=String(level);
    bars.forEach((bar,i)=>{const f=Math.pow(1-Math.abs(i-16)/17,1.4);bar.style.height=`${2+22*f*level}px`;bar.style.opacity=String(.25+.65*level);});
  }
  function draw(dt=0) {meter();ribbon.tick(dt);}
  function cancel(){if(frame)cancelAnimationFrame(frame);frame=0;last=0;}
  function tick(now){
    frame=0;if(destroyed || document.hidden || reducedMotion.matches || state==='paused' || ribbon.mode==='lost')return;
    draw(last?Math.min(.067,(now-last)/1000):0);last=now;frame=requestAnimationFrame(tick);
  }
  function schedule(){
    cancel();if(destroyed || document.hidden)return;
    draw();if(!reducedMotion.matches && state!=='paused' && ribbon.mode!=='lost')frame=requestAnimationFrame(tick);
  }
  function settleStill(){
    // No continuous phase travel under reduced motion; finish only the requested shape.
    const phase=ribbon.theta, render=ribbon._draw;ribbon._draw=()=>{};
    try{for(let i=0;i<60;i++){ribbon.tick(1/15);ribbon.theta=phase;}}
    finally{ribbon._draw=render;}
    draw();
  }
  function resize(){
    if(destroyed)return;

    ribbon.resize();schedule();
  }
  on(document,'visibilitychange',schedule);
  on(reducedMotion,'change',()=>{ribbon.reduced=reducedMotion.matches;if(reducedMotion.matches)settleStill();schedule();});
  ribbon.on('error',event=>{if(event.type==='context-lost'){canvas.dataset.renderer='lost';cancel();}});
  ribbon.on('recovered',()=>{resize();});
  const observer=new ResizeObserver(resize);observer.observe(canvas);
  resize();
  return {
    setState(next,isActive){
      const previous=state;state=next;active=!!isActive;
      if(!active || state==='paused'){input=0;output=0;}
      const visual=state==='paused'?'idle':globalThis.OS1Ribbon.STATES.includes(state)?state:'idle';
      ribbon.setState(visual);meter();
      if(reducedMotion.matches && previous!==state)settleStill();
      if(previous!==state)schedule();
    },
    setLevels(nextInput,nextOutput){input=bound(nextInput);output=bound(nextOutput);meter();if(reducedMotion.matches)draw();},
    pulse(){ /* Button actions change real client state; do not manufacture audio activity. */ },
    destroy(){destroyed=true;cancel();observer.disconnect();events.abort();ribbon.destroy();}
  };
}
