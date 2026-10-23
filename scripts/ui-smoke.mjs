// Optional browser regression: install Playwright locally, then node scripts/ui-smoke.mjs.
// All backend responses and UI driver callbacks are test fixtures, not real inference.
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {readFile, mkdir} from 'node:fs/promises';
const {chromium} = createRequire(import.meta.url)('playwright');
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
const webRoot=new URL('../web/',import.meta.url);
const fixtureSource=await readFile(new URL('../tests/fixtures/ui-driver.js',import.meta.url));
const server=createServer(async (req,res)=>{
  const name=new URL(req.url,'http://localhost').pathname.slice(1)||'index.html';
  if(!/^[a-zA-Z0-9.-]+$/.test(name)){res.writeHead(404).end();return;}
  try {
    let data;
    if(name==='ui-driver.js')data=fixtureSource;
    else {data=await readFile(new URL(name==='fixture.html'?'index.html':name,webRoot));
      if(name==='fixture.html')data=Buffer.from(data.toString().replace('src="./app.js"','src="./ui-driver.js"'));}
    res.setHeader('Content-Type', name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'text/html');res.end(data);
  } catch {res.writeHead(404).end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const baseUrl=`http://127.0.0.1:${server.address().port}`;
const out=process.env.UI_SCREENSHOT_DIR;
if(out)await mkdir(out,{recursive:true});
let browser;
try {
browser=await chromium.launch({...(process.env.UI_CHROMIUM?{executablePath:process.env.UI_CHROMIUM}:{}),args:['--use-angle=swiftshader','--enable-unsafe-swiftshader','--ignore-gpu-blocklist']});
const errors=[],checks=[];
const page=await browser.newPage({viewport:{width:1440,height:900},colorScheme:'dark',reducedMotion:'reduce'});
page.on('pageerror',e=>errors.push(e.message));
const shot=async name=>{if(!out)return;await page.waitForTimeout(100);await page.screenshot({path:`${out}/${name}.png`,fullPage:true});};
const go=async query=>{await page.goto(`${baseUrl}/fixture.html${query||''}`);await page.waitForFunction(()=>window.fixture);};
const noOverflow=async name=>{const result=await page.evaluate(()=>({viewport:innerWidth,content:document.documentElement.scrollWidth,buttons:[...document.querySelectorAll('button')].filter(e=>e.getClientRects().length&&!e.disabled).map(e=>({id:e.id||e.textContent.trim(),w:e.getBoundingClientRect().width,h:e.getBoundingClientRect().height})).filter(r=>r.w<43||r.h<43)}));assert.ok(result.content<=result.viewport,`${name} horizontal overflow`);assert.deepEqual(result.buttons,[],`${name} undersize target`);checks.push(name+' layout/targets');};
try {
 for(const size of (process.env.APP_ONLY ? [] : [{width:1440,height:900},{width:390,height:844}])){
  await page.setViewportSize(size);const prefix=size.width===1440?'desktop':'mobile';
  for(const state of ['idle','listening','thinking','speaking','paused']){
   await go(`?state=${state}${state==='speaking'?'&messages=1':''}`);await noOverflow(prefix+'-'+state);await shot(prefix+'-'+state);
   if(state==='idle'){
    const c=await page.locator('#call-button').boundingBox();assert.ok(c.y+c.height<=size.height,prefix+' primary action below first viewport');
    if(prefix==='desktop')assert.ok(await page.evaluate(()=>document.documentElement.scrollHeight)<=size.height,'desktop idle scrolls');
   }
  }
  await go('?state=listening');
  await page.locator('#sound-button').click();assert.equal(await page.locator('#tab-preferences').getAttribute('aria-selected'),'true');assert.equal(await page.evaluate(()=>document.activeElement.id),'output-device');await page.locator('#settings-done').click();
  await page.locator('#settings-button').click();
  for(const tab of ['provider','voice','preferences','service']){await page.locator('#tab-'+tab).click();await noOverflow(prefix+'-settings-'+tab);await shot(prefix+'-settings-'+tab);assert.ok(await page.locator('#settings-'+tab).isVisible());}
  await page.locator('#tab-provider').focus();await page.keyboard.press('ArrowRight');assert.equal(await page.evaluate(()=>document.activeElement.id),'tab-voice');await page.keyboard.press('End');assert.equal(await page.evaluate(()=>document.activeElement.id),'tab-service');await page.keyboard.press('Escape');assert.ok(!await page.locator('#settings-dialog').isVisible());assert.equal(await page.evaluate(()=>document.activeElement.id),'settings-button');
  await page.locator('#history-button').click();await shot(prefix+'-history-empty');await page.keyboard.press('Escape');
  await page.evaluate(()=>window.fixture.render({messages:[{id:1,role:'user',text:'<img src=x onerror=alert(1)>',status:'complete'},{id:2,role:'assistant',text:'記錄以純文字安全顯示。',status:'complete'}]}));
  assert.equal(await page.locator('#transcript-count').textContent(),'2');assert.equal(await page.locator('#transcript-list img').count(),0);
  await page.locator('#history-button').click();await shot(prefix+'-history-filled');await page.locator('#clear-history').click();assert.equal(await page.locator('#transcript-count').textContent(),'0');await page.keyboard.press('Escape');
  await page.locator('[data-prompt]').first().click();assert.ok(await page.locator('#message-input').inputValue());assert.ok(await page.locator('#send-button').isDisabled());await page.locator('#call-button').click();assert.ok(await page.locator('#send-button').isEnabled());await page.locator('#message-input').press('Enter');assert.equal(await page.locator('#transcript-count').textContent(),'1');assert.equal(await page.locator('#voice-app').getAttribute('data-state'),'thinking');await page.locator('#interrupt-button').click();assert.equal(await page.locator('#voice-app').getAttribute('data-state'),'interrupted');await page.locator('#mic-button').click();assert.equal(await page.locator('#voice-app').getAttribute('data-state'),'paused');await page.locator('#mic-button').click();assert.equal(await page.locator('#voice-app').getAttribute('data-state'),'listening');
  await page.locator('#keyboard-button').click();await page.locator('#captions-button').click();assert.equal(await page.locator('#captions-button').getAttribute('aria-pressed'),'false');
  await go('?error=1');await shot(prefix+'-error');await page.locator('#error-settings').click();assert.ok(await page.locator('#settings-dialog').isVisible());await page.keyboard.press('Escape');
  await go();if(prefix==='desktop')await page.locator('#mode-button').click();else await page.locator('[data-toggle-mode]').click();assert.ok(await page.locator('#mode-notice').isVisible());await shot(prefix+'-demo');
  await go();await page.locator('#theme-button').click();assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'light');await shot(prefix+'-light');await page.locator('#theme-button').click();
  if(prefix==='desktop'){await page.locator('#focus-button').click();assert.ok(!await page.locator('.app-sidebar').isVisible());assert.ok(await page.evaluate(()=>document.documentElement.scrollHeight)<=900,'focus mode should fit desktop viewport');await shot('desktop-focus');await page.locator('#focus-button').click();assert.ok(await page.locator('.app-sidebar').isVisible());}
  checks.push(prefix+' callbacks/panels/tabs/prompt/send/pause/interrupt/clear/theme');
 }
 // Guard against a long streamed transcript stretching the whole workspace.
 await page.setViewportSize({width:1440,height:900});await go('?state=speaking');
 const initialHeight=await page.evaluate(()=>document.documentElement.scrollHeight);
 await page.evaluate(()=>window.fixture.render({messages:[{id:1,role:'assistant',text:'長回覆應留在可捲動的逐字稿區。'.repeat(200),status:'streaming'}]}));
 assert.equal(await page.evaluate(()=>document.documentElement.scrollHeight),initialHeight);
 assert.ok(await page.locator('#transcript-scroll').evaluate(e=>e.scrollHeight>e.clientHeight));
 checks.push('long transcript scrolls independently without stretching workspace');
 for(const width of [320,701,768,1080,1081,1280,1920]){
  await page.setViewportSize({width,height:900});await go();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${width}px overflow`);
 }
 checks.push('320–1920px responsive breakpoint sweep');
 // Actual app boot and provider controller, using local API fixtures only.
 await page.setViewportSize({width:1440,height:900});
 const health={app_name:'HER Voice',provider:'deepseek',provider_ready:true,engine:{state:'ready'}};
 await page.route('**/api/**',async route=>{const url=new URL(route.request().url());let data={};
 if(url.pathname==='/api/bootstrap')data={token:'local-test-only',health};
 else if(url.pathname==='/api/health')data=health;
 else if(url.pathname==='/api/provider')data={provider:'deepseek',url:'https://api.deepseek.com/chat/completions',model:'deepseek-flash',api_key_configured:true};
 else if(url.pathname==='/api/provider/models')data={ok:true,models:[{id:'deepseek-flash',name:'DeepSeek Flash'}],message:'模型列表已更新'};
 else if(url.pathname==='/api/tts')data={backend:'drama3',voice_id:'v1'};
 else if(url.pathname==='/api/voices')data={voices:[{id:'v1',name:'參考音色',transcript:'你好',duration_seconds:8,sample_rate:24000}]};
 await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.goto(`${baseUrl}/index.html`);await page.locator('#settings-button').click();await page.waitForFunction(()=>!document.getElementById('provider-fields').disabled);assert.equal(await page.locator('#provider-model-list').inputValue(),'deepseek-flash');await shot('desktop-settings-live-controller');checks.push('actual app boot/provider controller with local API fixtures');
 assert.deepEqual(errors,[]);console.log(JSON.stringify({checks,pageErrors:errors},null,2));if(out)await writeFile(`${out}/results.json`,JSON.stringify({checks,pageErrors:errors},null,2));
} finally {await browser.close();}
} finally {if(browser?.isConnected())await browser.close();await new Promise(resolve=>server.close(resolve));}
