// Credentials stay in this form and the request body, never in VoiceClient
// snapshots or browser storage. The backend only returns configured/not set.
export function attachProviderSettings({client, baseUrl, fetch: fetcher = globalThis.fetch.bind(globalThis)}) {
  const ids = ['form', 'fields', 'kind', 'url', 'model', 'model-list', 'model-manual', 'models-refresh', 'models-message', 'key', 'key-show', 'test', 'save', 'reload', 'current', 'message'];
  const el = Object.fromEntries(ids.map(id => [id, document.getElementById(`provider-${id}`)]));
  const events = new AbortController();
  let token = '', loaded = false, busy = false, disposed = false;
  let catalog = [], manual = false;

  function catalogMessage(text, error = false) {
    el['models-message'].textContent = text;
    el['models-message'].className = error ? 'voice-admin-error' : 'field-help';
    el['models-message'].setAttribute('role', error ? 'alert' : 'status');
  }
  function setManual(value) {
    manual = value;
    el.model.hidden = !value;
    el.model.required = value;
    el['model-list'].required = !value;
    el['model-list'].disabled = value;
    el['model-manual'].textContent = value ? '使用模型列表' : '手動輸入模型';
    el['model-manual'].setAttribute('aria-expanded', String(value));
  }
  function renderModels(models, current = el.model.value.trim()) {
    catalog = models;
    const options = [];
    const add = (id, name, disabled = false) => {
      const option = document.createElement('option');
      option.value = id; option.textContent = name; option.disabled = disabled;
      options.push(option);
    };
    if (!current) add('', models.length ? '請選擇模型' : '請先拉取模型', true);
    if (current && !models.some(model => model.id === current)) add(current, `${current} · 目前填寫`);
    for (const model of models) add(model.id, model.name === model.id ? model.id : `${model.name} · ${model.id}`);
    el['model-list'].replaceChildren(...options);
    el['model-list'].value = current;
  }
  function invalidateCatalog() {
    renderModels([], el.model.value.trim());
    catalogMessage('設定已變更，請重新拉取模型。');
  }
  async function fetchModels() {
    if (disposed || busy || !loaded || !el.url.reportValidity()) return;
    const {model, ...data} = payload();
    setBusy(true);
    el['models-refresh'].textContent = '拉取中…';
    catalogMessage('正在拉取模型…');
    try {
      const result = await request('api/provider/models', 'POST', data);
      if (disposed) return;
      if (!result.ok) { renderModels([]); catalogMessage(result.message, true); return; }
      renderModels(result.models);
      catalogMessage(result.message);
    } catch (error) {
      if (!disposed) { renderModels([]); catalogMessage(error.message, true); }
    } finally {
      if (!disposed) { el['models-refresh'].textContent = '拉取模型'; setBusy(false); }
    }
  }
  function message(text, error = false) {
    el.message.textContent = text;
    el.message.hidden = !text;
    el.message.className = error ? 'voice-admin-error' : 'voice-admin-message';
    el.message.setAttribute('role', error ? 'alert' : 'status');
  }
  function setBusy(value) {
    busy = value;
    el.fields.disabled = value || !loaded;
    el.reload.disabled = value;
    el.form.setAttribute('aria-busy', String(value));
  }
  async function request(path, method = 'GET', data) {
    if (!token) {
      const response = await fetcher(new URL('api/bootstrap', baseUrl), {cache: 'no-store', signal: events.signal});
      if (!response.ok) throw new Error('本機服務無法連線，請重新整理頁面。');
      token = (await response.json()).token;
    }
    const response = await fetcher(new URL(path, baseUrl), {
      method, cache: 'no-store', credentials: 'same-origin', redirect: 'error', signal: events.signal,
      headers: {'X-Voice-Token': token, ...(data ? {'Content-Type': 'application/json'} : {})},
      ...(data ? {body: JSON.stringify(data)} : {}),
    });
    if (response.status === 404) throw new Error('請重新啟動 HER Voice 本機服務，以載入回答接口設定功能。');
    if (response.status === 403) { token = ''; throw new Error('設定連線已失效，請重新讀取。'); }
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `設定失敗（HTTP ${response.status}）。`);
    return result;
  }
  function showConfiguration(config) {
    el.kind.value = config.provider;
    el.url.value = config.url;
    el.url.readOnly = config.provider === 'deepseek';
    el.model.value = config.model;
    renderModels(catalog, config.model);
    setManual(false);
    el.key.value = '';
    el.key.type = 'password';
    el['key-show'].textContent = '顯示';
    el['key-show'].setAttribute('aria-pressed', 'false');
    el.key.placeholder = config.api_key_configured ? '•••••••• 已儲存；輸入新 Key 可更換' : '貼上此接口的 API Key';
    el.current.textContent = `${config.provider === 'deepseek' ? 'DeepSeek' : '相容接口'} · ${config.model} · ${config.api_key_configured ? 'Key 已設定' : '未設定 Key'}`;
  }
  async function load(force = false) {
    if (disposed || busy || (loaded && !force)) return;
    let autoFetch = false;
    setBusy(true); message('正在讀取設定…');
    try {
      token = '';
      const config = await request('api/provider');
      catalog = [];
      showConfiguration(config);
      catalogMessage('從目前接口讀取可用模型。');
      autoFetch = config.api_key_configured;
      loaded = true; message('');
    } catch (error) { if (!disposed) message(error.message, true); }
    finally { if (!disposed) setBusy(false); }
    if (autoFetch) await fetchModels();
  }
  function payload() {
    return {provider: el.kind.value, url: el.url.value.trim(), model: el.model.value.trim(), api_key: el.key.value.trim()};
  }
  async function submit(testOnly) {
    if (busy || !el.form.reportValidity()) return;
    const data = payload();
    setBusy(true); message(testOnly ? '正在測試連線…' : '正在儲存並套用…');
    try {
      if (testOnly) {
        const result = await request('api/provider/test', 'POST', data);
        if (!disposed) message(result.message, !result.ok);
      } else {
        const current = await request('api/provider');
        const potentiallyUnchanged = !data.api_key && ['provider', 'url', 'model'].every(name => data[name] === current[name]);
        if (!potentiallyUnchanged) await client.disconnect();
        const result = await request('api/provider', 'PUT', data);
        if (potentiallyUnchanged && !result.unchanged) await client.disconnect();
        if (!disposed) {
          showConfiguration(result);
          message(result.unchanged ? '設定未變，已儲存；無需重新載入模型。' : '已儲存並套用。下次開始對話會使用此設定。');
          try { await client.refreshHealth(); }
          catch { message('已儲存。請重新整理頁面以更新服務狀態。'); }
        }
      }
    } catch (error) { if (!disposed) message(error.message, true); }
    finally { if (!disposed) setBusy(false); }
  }
  const listen = (element, type, handler) => element.addEventListener(type, handler, {signal: events.signal});
  listen(el.form, 'submit', event => { event.preventDefault(); void submit(false); });
  listen(el.test, 'click', () => void submit(true));
  listen(el.reload, 'click', () => void load(true));
  listen(el.form, 'input', () => message(''));
  listen(el['models-refresh'], 'click', () => void fetchModels());
  listen(el['model-list'], 'change', () => { el.model.value = el['model-list'].value; message(''); });
  listen(el['model-manual'], 'click', () => {
    if (manual) renderModels(catalog);
    setManual(!manual);
    if (manual) el.model.focus();
  });
  listen(el.url, 'input', () => {
    el.model.value = ''; setManual(false); invalidateCatalog();
  });
  listen(el.key, 'input', invalidateCatalog);
  listen(el.kind, 'change', () => {
    const deepseek = el.kind.value === 'deepseek';
    el.url.value = deepseek ? 'https://api.deepseek.com/chat/completions' : '';
    el.url.readOnly = deepseek;
    el.model.value = deepseek ? 'deepseek-flash' : '';
    setManual(false); invalidateCatalog();
    el.key.value = ''; el.key.placeholder = '請填寫所選接口的 API Key';
  });
  listen(el['key-show'], 'click', () => {
    const show = el.key.type === 'password';
    el.key.type = show ? 'text' : 'password';
    el['key-show'].textContent = show ? '隱藏' : '顯示';
    el['key-show'].setAttribute('aria-pressed', String(show));
  });
  listen(document.getElementById('settings-dialog'), 'close', () => {
    el.key.value = ''; el.key.type = 'password';
    el['key-show'].textContent = '顯示'; el['key-show'].setAttribute('aria-pressed', 'false');
  });
  return {load, destroy() { disposed = true; el.key.value = ''; token = ''; events.abort(); }};
}
