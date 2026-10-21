// This page owns DOM, rendering cadence, preferences and page lifecycle.
// Other products can import client.js and provide their own UI adapter.
import {createUI} from './ui.js';
import {VoiceClient} from './client.js';

let saved = {};
try {
  const value = JSON.parse(localStorage.getItem('her-voice-settings') || '{}');
  for (const key of ['captions', 'bargeIn']) if (typeof value?.[key] === 'boolean') saved[key] = value[key];
  for (const key of ['micDeviceId', 'outputDeviceId']) if (typeof value?.[key] === 'string') saved[key] = value[key];
} catch { /* Local storage may be disabled. */ }

const client = new VoiceClient({baseUrl: new URL('./', import.meta.url), settings: saved});
// Errors are already present in the state snapshot. Avoid an unhandled Promise
// rejection from a DOM event dispatcher without hiding the visible error.
const run = action => Promise.resolve().then(action).catch(() => {});
let providerPanel;
const ui = createUI({
  onStart: () => run(() => client.connect()),
  onStop: () => run(() => client.disconnect()),
  onPause: () => run(() => client.state.paused ? client.resume() : client.pause()),
  onInterrupt: () => run(() => client.interrupt()),
  onSend: text => run(() => client.sendText(text)),
  onModeChange: mode => run(() => client.setMode(mode)),
  onSettingsChange: settings => run(async () => {
    await client.updateSettings(settings);
    try { localStorage.setItem('her-voice-settings', JSON.stringify(client.state.settings)); }
    catch { /* Preferences remain in memory when storage is unavailable. */ }
  }),
  onClearHistory: () => run(() => client.clearHistory()),
  onLoadVoiceSettings: () => { void providerPanel?.load(); return run(() => client.loadVoiceSettings()); },
  onImportVoice: voice => run(() => client.importVoice(voice)),
  onApplyVoiceSettings: selection => run(() => client.applyVoiceSettings(selection)),
});
// An already running backend may still have the previous asset allow-list.
// Keep the rest of the UI usable until that backend is restarted.
import('./provider-settings.js').then(({attachProviderSettings}) => {
  if (disposed) return;
  providerPanel = attachProviderSettings({client, baseUrl: new URL('./', import.meta.url)});
  if (document.getElementById('settings-dialog').open) void providerPanel.load();
}).catch(() => {
  if (disposed) return;
  const notice = document.getElementById('provider-message');
  notice.textContent = '請重新啟動 HER Voice 本機服務以載入回答接口設定。';
  notice.className = 'voice-admin-error';
  notice.hidden = false;
});

let latest = client.state;
let frame = null;
let disposed = false;
function render() {
  if (disposed || frame !== null) return;
  frame = requestAnimationFrame(() => { frame = null; if (!disposed) ui.render(latest); });
}
const unsubscribeState = client.subscribe(snapshot => { latest = snapshot; render(); });
const unsubscribeLevels = client.onLevels(({input, output}) => ui.setLevels(input, output));
const events = new AbortController();
window.addEventListener('pagehide', event => {
  // BFCache can restore this same JS/DOM instance. End capture and the session,
  // but preserve its adapter/listeners so Back returns to a usable idle page.
  if (event.persisted) { client.disconnect().catch(() => {}); return; }
  disposed = true;
  if (frame !== null) cancelAnimationFrame(frame);
  events.abort(); unsubscribeState(); unsubscribeLevels(); ui.destroy(); providerPanel?.destroy();
  client.destroy().catch(() => {});
}, {signal: events.signal});
document.addEventListener('visibilitychange', () => { if (!document.hidden) render(); }, {signal: events.signal});
client.initialize().catch(() => {});
