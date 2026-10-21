// Real UI + isolated HTTP fixture. No models, microphone or provider calls.
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const require = createRequire(import.meta.url);
const {chromium} = require(require.resolve('playwright', {paths: [process.cwd(), process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES || process.cwd()]}));
const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const output = path.resolve(process.env.VOICE_QA_OUTPUT || '/tmp/her-voice-settings-qa');
await mkdir(output, {recursive: true});
const config = {backend: 'drama3', voice_id: null}, voices = [], mutations = [];
let rejectImport = false, rejectApply = false;
const health = {demo_only: true, provider_ready: false, app_name: 'HER Voice', profile: 'Drama 3 · Qwen3-TTS Base', engine: {state: 'stopped', ready: false}};
const server = createServer(async (request, response) => {
  const url = new URL(request.url, 'http://127.0.0.1');
  const send = (body, status = 200) => { response.writeHead(status, {'Content-Type': 'application/json', 'Cache-Control': 'no-store'}); response.end(JSON.stringify(body)); };
  try {
    if (url.pathname === '/api/bootstrap') return send({token: 'fixture-token', health});
    if (url.pathname === '/api/status') return send(health);
    if (url.pathname.startsWith('/api/') && request.headers['x-voice-token'] !== 'fixture-token') return send({error: 'Missing token'}, 403);
    if (url.pathname === '/api/tts' && request.method === 'GET') return send(config);
    if (url.pathname === '/api/voices' && request.method === 'GET') return send({voices, selected_voice_id: config.voice_id});
    if (url.pathname === '/api/tts' && request.method === 'PUT') {
      const parts = []; for await (const part of request) parts.push(part); mutations.push('apply');
      if (rejectApply) { rejectApply = false; return send({error: '测试：原引擎未完全释放，请重试。'}, 409); }
      Object.assign(config, JSON.parse(Buffer.concat(parts).toString('utf8'))); return send(config);
    }
    if (url.pathname === '/api/voices' && request.method === 'POST') {
      const parts = []; for await (const part of request) parts.push(part); mutations.push('import');
      assert.match(request.headers['content-type'], /multipart\/form-data; boundary=/);
      assert.ok(Buffer.concat(parts).includes(Buffer.from('RIFF')));
      if (rejectImport) { rejectImport = false; return send({error: '测试：参考录音不符合 PCM WAV 要求。'}, 400); }
      const voice = {id: 'joyboi-voice', name: 'Joyboi <测试音色>', transcript: '你好，I am Joyboi.', duration_seconds: 6, sample_rate: 24000};
      voices.push(voice); return send({voice}, 201);
    }
    const name = url.pathname === '/' ? 'index.html' : url.pathname.slice(1);
    if (!['index.html', 'styles.css', 'app.js', 'ui.js', 'client.js', 'audio.js', 'audio-dsp.js', 'protocol.js', 'capture-worklet.js'].includes(name)) { response.writeHead(404); return response.end(); }
    response.writeHead(200, {'Content-Type': (name.endsWith('.html') ? 'text/html' : name.endsWith('.css') ? 'text/css' : 'text/javascript') + '; charset=utf-8'});
    response.end(await readFile(path.join(root, 'web', name)));
  } catch (error) { send({error: error.message}, 500); }
});
await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
const baseUrl = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({headless: true, ...(process.env.VOICE_CHROMIUM ? {executablePath: process.env.VOICE_CHROMIUM} : {})});
const context = await browser.newContext({viewport: {width: 1536, height: 1024}, reducedMotion: 'reduce'});
const page = await context.newPage();
const errors = [], expectedConsoleErrors = [], checks = [];
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => {
  if (message.type() !== 'error') return;
  if (/Failed to load resource.*(?:400|409)/.test(message.text())) expectedConsoleErrors.push(message.text()); else errors.push(message.text());
});
try {
  await page.goto(baseUrl, {waitUntil: 'networkidle'});
  assert.equal(await page.title(), 'HER Voice'); assert.equal(new URL(page.url()).origin, baseUrl);
  assert.ok((await page.locator('#main-caption').innerText()).length > 0);
  await page.locator('#settings-button').click();
  await page.waitForFunction(() => document.querySelector('#voice-current').textContent.includes('Drama 3'));
  assert.equal(await page.locator('#voice-backend').inputValue(), 'drama3');
  assert.equal(await page.locator('#voice-apply-button').isDisabled(), true);
  checks.push('Page identity, meaningful screen, Drama default, no selected voice');
  await page.screenshot({path: path.join(output, 'desktop-voice-settings.png')});
  await page.locator('#voice-name').fill('Joyboi <测试音色>');
  await page.locator('#voice-transcript').fill('你好，I am Joyboi.');
  await page.locator('#voice-file').setInputFiles({name: 'reference.wav', mimeType: 'audio/wav', buffer: Buffer.from('RIFF fixture')});
  rejectImport = true;
  await page.locator('#voice-import-button').click();
  await page.waitForFunction(() => document.querySelector('#voice-admin-error').textContent.includes('PCM WAV'));
  assert.equal(await page.locator('#voice-name').inputValue(), 'Joyboi <测试音色>');
  assert.equal(await page.locator('#voice-apply-button').isDisabled(), true);
  checks.push('Rejected import remains visible, preserves form, adds no fake voice');
  await page.locator('#voice-import-button').click();
  await page.waitForFunction(() => document.querySelector('#voice-select').value === 'joyboi-voice');
  assert.equal(await page.locator('#voice-import-details').getAttribute('open'), null);
  assert.equal(await page.locator('#voice-name').inputValue(), '');
  assert.equal(config.voice_id, null);
  assert.match(await page.locator('#voice-details').innerText(), /你好，I am Joyboi/);
  assert.equal(await page.locator('#voice-select option').last().textContent(), 'Joyboi <测试音色>');
  checks.push('Multipart upload, Unicode name/transcript, safe text rendering, explicit selection');
  await page.locator('#voice-backend').selectOption('qwen3'); rejectApply = true;
  await page.locator('#voice-apply-button').click();
  await page.waitForFunction(() => document.querySelector('#voice-admin-error').textContent.includes('原引擎'));
  assert.equal(config.backend, 'drama3'); assert.match(await page.locator('#voice-current').innerText(), /^Drama 3/);
  await page.locator('#voice-apply-button').click();
  await page.waitForFunction(() => document.querySelector('#voice-admin-message').textContent.includes('声音设置已应用'));
  assert.equal(config.backend, 'qwen3'); assert.equal(config.voice_id, 'joyboi-voice');
  assert.match(await page.locator('#voice-current').innerText(), /^Qwen3-TTS Base/);
  assert.equal(await page.locator('#voice-app').getAttribute('data-connection'), 'disconnected');
  assert.equal(await page.locator('#voice-admin-error').isVisible(), false);
  checks.push('Failed apply preserves confirmed backend; retry applies without auto-start');
  await page.screenshot({path: path.join(output, 'desktop-voice-applied.png')});
  await page.locator('#voice-refresh-button').click();
  await page.waitForFunction(() => !document.querySelector('#voice-refresh-button').disabled);
  assert.equal(await page.locator('#voice-select').inputValue(), 'joyboi-voice');
  for (const viewport of [{width: 390, height: 844}, {width: 320, height: 700}]) {
    await page.setViewportSize(viewport); await page.locator('#voice-import-details summary').click();
    await page.locator('#voice-import-button').scrollIntoViewIfNeeded();
    const size = await page.evaluate(() => { const panel = document.querySelector('#settings-dialog'), body = panel.querySelector('.panel-body'); return {screen: innerWidth, document: document.documentElement.scrollWidth, panelWidth: panel.clientWidth, panelScroll: panel.scrollWidth, bodyWidth: body.clientWidth, bodyScroll: body.scrollWidth}; });
    assert.ok(size.document <= size.screen); assert.ok(size.panelScroll <= size.panelWidth); assert.ok(size.bodyScroll <= size.bodyWidth);
    await page.screenshot({path: path.join(output, `mobile-voice-${viewport.width}.png`)});
    await page.locator('#voice-import-details summary').click();
  }
  checks.push('Refresh persists selection; 390px/320px forms scroll without horizontal overflow');
  assert.deepEqual(errors, []); assert.deepEqual(mutations, ['import', 'import', 'apply', 'apply']);
  const result = {result: 'PASS', fixture: true, browser: await browser.version(), browserPlugin: 'Invocation failed: originating browser context unavailable; repository Playwright permitted', baseUrl, checks, errors, expectedConsoleErrors, output};
  await writeFile(path.join(output, 'results.json'), JSON.stringify(result, null, 2)); console.log(JSON.stringify(result, null, 2));
} catch (error) {
  await page.screenshot({path: path.join(output, 'failure.png'), fullPage: true});
  console.error(JSON.stringify({result: 'FAIL', fixture: true, checks, errors, message: error.message, output}, null, 2)); process.exitCode = 1;
} finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
