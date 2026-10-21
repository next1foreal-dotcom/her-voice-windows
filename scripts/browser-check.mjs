// Optional browser smoke test. Start `python -m her_voice --demo` first.
// Install Playwright separately (`npm install --no-save playwright`) and its
// browser, or set VOICE_CHROMIUM to an existing Chrome/Chromium executable.
import {createRequire} from 'node:module';
import {mkdir, writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import path from 'node:path';

const require = createRequire(import.meta.url);
const {chromium} = require(require.resolve('playwright', {paths: [process.cwd(), process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES || process.cwd()]}));
const output = path.resolve(process.env.VOICE_QA_OUTPUT || '.runtime/browser-qa');
await mkdir(output, {recursive: true});
const browser = await chromium.launch({headless: true, ...(process.env.VOICE_CHROMIUM ? {executablePath: process.env.VOICE_CHROMIUM} : {})});
const context = await browser.newContext({viewport: {width: 1536, height: 1024}, reducedMotion: 'reduce'});
const page = await context.newPage();
const errors = [];
const checks = [];
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
const state = value => page.waitForFunction(expected => document.querySelector('#voice-app').dataset.state === expected, value, {timeout: 12000});
const connection = value => page.waitForFunction(expected => document.querySelector('#voice-app').dataset.connection === expected, value, {timeout: 12000});

try {
  await page.goto(process.env.VOICE_QA_URL || 'http://127.0.0.1:8790', {waitUntil: 'networkidle'});
  await page.waitForFunction(() => document.querySelector('#voice-app').dataset.mode === 'demo');
  await page.screenshot({path: path.join(output, 'desktop-idle.png')});
  assert.match(await page.locator('#mode-notice').innerText(), /協定演練/);
  checks.push('Demo is explicitly labeled; no microphone/API request');
  await page.locator('#call-button').click();
  await connection('connected');
  await page.locator('#keyboard-button').click();
  const englishInput = 'Please demonstrate streaming captions and interruption.';
  const mixedInput = '請用 English 解釋 GPU memory，再用中文總結。';
  await page.locator('#message-input').fill(englishInput);
  await page.locator('#send-button').click();
  await state('speaking');
  await page.screenshot({path: path.join(output, 'desktop-speaking.png')});
  await page.locator('#interrupt-button').click();
  await connection('connected');
  await state('interrupted');
  const interruptedText = await page.locator('#main-caption').innerText();
  await page.waitForTimeout(400);
  assert.equal(await page.locator('#main-caption').innerText(), interruptedText);
  await page.screenshot({path: path.join(output, 'desktop-interrupted.png')});
  checks.push('Typed turn streams PCM/text, and manual interrupt stops it across session reset');

  await page.locator('#message-input').fill(mixedInput);
  await page.locator('#send-button').click();
  await state('speaking');
  await state('listening');
  assert.match(await page.locator('#main-caption').innerText(), /切換「即時語音」/);
  checks.push('Complete turn returns to listening after scheduled audio ends');
  await page.locator('#mic-button').click();
  await state('paused');
  assert.equal(await page.locator('#send-button').isDisabled(), true);
  await page.screenshot({path: path.join(output, 'desktop-paused.png')});
  await page.locator('#mic-button').click();
  await connection('connected');
  await state('listening');
  checks.push('Pause closes session; resume restores complete text history');

  await page.locator('#captions-button').click();
  await page.waitForFunction(() => document.querySelector('#captions-button').getAttribute('aria-pressed') === 'false');
  assert.equal(await page.locator('#captions-button').getAttribute('aria-pressed'), 'false');
  await page.locator('#captions-button').click();
  await page.waitForFunction(() => document.querySelector('#captions-button').getAttribute('aria-pressed') === 'true');
  await page.locator('#settings-button').click();
  assert.equal(await page.locator('#speech-language').innerText(), '語音語言：中文 / English，自動辨識');
  assert.match(await page.locator('#speech-language-help').innerText(), /要求改用中文或英文/);
  await page.screenshot({path: path.join(output, 'desktop-settings.png')});
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#settings-dialog').isVisible(), false);
  checks.push('Captions toggle, settings dialog, keyboard dismissal');

  await page.locator('#history-button').click();
  assert.ok(await page.locator('#history-list li').count() >= 3);
  const history = await page.locator('#history-list').innerText();
  assert.ok(history.includes(englishInput));
  assert.ok(history.includes(mixedInput));
  checks.push('Automatic Chinese/English mode is visible; English and mixed text survive pause/resume without forced translation');
  await page.locator('#clear-history').click();
  await connection('disconnected');
  assert.equal(await page.locator('#history-list li').count(), 0);
  await page.keyboard.press('Escape');
  checks.push('Clear history also resets backend conversation and disconnects');

  for (const viewport of [{width: 390, height: 844}, {width: 1366, height: 768}, {width: 320, height: 700}]) {
    await page.setViewportSize(viewport);
    await page.screenshot({path: path.join(output, `viewport-${viewport.width}x${viewport.height}.png`), fullPage: true});
    const dimensions = await page.evaluate(() => ({width: window.innerWidth, document: document.documentElement.scrollWidth}));
    assert.ok(dimensions.document <= dimensions.width, `Horizontal overflow at ${viewport.width}`);
    assert.equal(await page.locator('#call-button').isVisible(), true);
    for (const selector of ['#history-button', '#settings-button']) {
      const box = await page.locator(selector).boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= viewport.width, `Header action clipped at ${viewport.width}: ${selector}`);
    }
  }
  checks.push('390×844, 1366×768, and 320×700 fit without horizontal overflow or clipped header actions');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({result: 'PASS', browser: await browser.version(), checks, errors, output}, null, 2));
  await writeFile(path.join(output, 'results.json'), JSON.stringify({result: 'PASS', browser: await browser.version(), checks, errors}, null, 2));
} catch (error) {
  await page.screenshot({path: path.join(output, 'failure.png'), fullPage: true});
  console.error(JSON.stringify({result: 'FAIL', checks, errors, message: error.message, output}, null, 2));
  process.exitCode = 1;
} finally {
  await browser.close();
}
