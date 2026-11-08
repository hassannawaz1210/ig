// The stats page in headless Chromium at phone width.
const {spawn} = require('node:child_process');
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const [binary, url] = process.argv.slice(2);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'ig-stats-'));
  const browser = spawn(binary, ['--headless', '--no-sandbox', '--no-first-run', '--remote-debugging-port=0',
    '--user-data-dir=' + profile, 'about:blank'], {stdio: ['ignore', 'ignore', 'pipe']});
  let socketUrl;
  try {
    socketUrl = await new Promise((resolve, reject) => {
      let output = ''; const timer = setTimeout(() => reject(new Error('no DevTools address')), 15000);
      browser.stderr.on('data', data => { output += data; const m = output.match(/DevTools listening on (ws:\S+)/); if (m) { clearTimeout(timer); resolve(m[1]); } });
      browser.on('exit', code => reject(new Error('browser exited ' + code)));
    });
  } catch (error) { browser.kill('SIGKILL'); console.error(error.message); process.exit(77); }
  const port = new URL(socketUrl).port;
  const target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t => t.type === 'page');
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(resolve => { socket.onopen = resolve; });
  let id = 0; const pending = new Map(); const errors = [];
  const held = [];   // requests paused by the test (Fetch.requestPaused)
  socket.onmessage = event => { const m = JSON.parse(event.data);
    if (m.method === 'Fetch.requestPaused') held.push(m.params.requestId);
    if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description);
    pending.get(m.id)?.(m); pending.delete(m.id); };
  const send = (method, params = {}) => new Promise(resolve => { pending.set(++id, resolve); socket.send(JSON.stringify({id, method, params})); });
  const evaluate = async expression => (await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true})).result.result?.value;
  const waitFor = async (expression, label) => {
    for (let i = 0; !(await evaluate(expression)); i++) { assert.ok(i < 80, 'Timed out: ' + label); await sleep(50); }
  };
  try {
    await send('Runtime.enable');
    await send('Emulation.setDeviceMetricsOverride', {width: 390, height: 844, deviceScaleFactor: 1, mobile: true});
    // Hold the hashtags answer back: the rest of the page must not wait for it.
    await send('Fetch.enable', {patterns: [{urlPattern: '*part=tags*'}]});
    await send('Page.navigate', {url});
    await waitFor("document.getElementById('tiles')?.getAttribute('aria-busy') === 'false'", 'stats loaded');
    await waitFor("!!document.querySelector('#weeks svg')", 'weekly chart shown while hashtags still load');
    assert.ok(await evaluate("!!document.querySelector('#tags .skeleton-row')"), 'hashtags still loading');
    assert.ok(held.length >= 1, 'the hashtags request was held');
    for (const requestId of held.splice(0)) await send('Fetch.continueRequest', {requestId});
    await send('Fetch.disable');
    await waitFor("document.getElementById('tags').getAttribute('aria-busy') === 'false'", 'hashtags shown when they arrive');
    assert.equal(await evaluate("document.querySelector('#tiles .tile b').textContent"), '3', '30 days: three reels');
    assert.equal(await evaluate("document.querySelectorAll('#creators .list-row').length"), 3);
    assert.equal(await evaluate("document.querySelector('#creators .list-row a').getAttribute('href')"), '/u/fresh');
    assert.ok(await evaluate("[...document.querySelectorAll('#sources .name')].map(e => e.textContent).includes('Instagram suggestions')"));
    assert.ok(await evaluate("!!document.querySelector('#weeks svg') && document.querySelectorAll('#weeks-legend span').length >= 2"),
      'weekly chart has a legend for its series');
    assert.ok(await evaluate('document.documentElement.scrollWidth <= innerWidth'), 'no sideways scrolling at 390px');
    // Hover shows a tooltip; the table view shows the same numbers.
    const box = await evaluate("(() => { const b = [...document.querySelectorAll('#weeks .hit')].at(-1).getBoundingClientRect(); return {x: b.x + b.width / 2, y: b.y + b.height / 2}; })()");
    await evaluate(`scrollTo(0, ${box.y} - 200)`); await sleep(100);
    const moved = await evaluate("(() => { const b = [...document.querySelectorAll('#weeks .hit')].at(-1).getBoundingClientRect(); return {x: b.x + b.width / 2, y: b.y + b.height / 2}; })()");
    await send('Input.dispatchMouseEvent', {type: 'mouseMoved', x: moved.x, y: moved.y}); await sleep(100);
    assert.match(await evaluate("document.getElementById('tooltip').hidden ? '' : document.getElementById('tooltip').innerText"), /Week of/);
    await evaluate("document.getElementById('weeks-table-toggle').click()");
    assert.ok(await evaluate("!!document.querySelector('#weeks table')"), 'table view');
    // Switching period reloads; all time includes the older view.
    await evaluate("document.querySelector('#period input[value=\"0\"]').click()");
    await waitFor("document.querySelector('#tiles .tile b').textContent === '4'", 'all time');
    await waitFor("!document.querySelector('.viz-root').classList.contains('refreshing')", 'refresh finished');
    assert.equal(await evaluate("document.querySelectorAll('.skeleton-tile, .skeleton-row, .skeleton-chart').length"), 0, 'placeholders replaced');
    // A failed load keeps the last numbers, says so, and offers Retry.
    await evaluate(`window.realFetch = window.fetch; window.fetch = () => Promise.reject(new Error('offline'))`);
    await evaluate("document.querySelector('#period input[value=\"7\"]').click()");
    await waitFor("[...document.querySelectorAll('#settings-status button')].some(b => b.textContent === 'Retry')", 'retry offered');
    assert.ok(await evaluate("!!document.querySelector('#weeks svg, #weeks table')"), 'other sections stay when one fails');
    assert.equal(await evaluate("document.querySelector('#tiles .tile b').textContent"), '4', 'last numbers kept');
    assert.equal(await evaluate("document.querySelector('.viz-root').classList.contains('refreshing')"), false);
    await evaluate("window.fetch = window.realFetch; document.querySelector('#settings-status button').click()");
    await waitFor("document.querySelector('#tiles .tile b').textContent !== '4' && !document.querySelector('#settings-status button')", 'retry loads 7 days');
    console.error = () => {};
    assert.deepEqual(errors, []);
    console.log('Stats page: tiles, creators, sources, weekly chart, tooltip, table, periods passed');
  } finally {
    socket.close(); browser.kill('SIGKILL');
    await fs.promises.rm(profile, {recursive: true, force: true, maxRetries: 10, retryDelay: 50});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
