// Drives the profile viewer with touch emulation at phone size.
const {spawn} = require('node:child_process');
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const [binary, url] = process.argv.slice(2);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'ig-carousel-'));
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
  let id = 0; const pending = new Map();
  socket.onmessage = event => { const m = JSON.parse(event.data); pending.get(m.id)?.(m); pending.delete(m.id); };
  const send = (method, params = {}) => new Promise(resolve => { pending.set(++id, resolve); socket.send(JSON.stringify({id, method, params})); });
  const evaluate = async expression => {
    const r = await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
    if (r.result.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails));
    return r.result.result.value;
  };
  const waitFor = async (expression, label) => {
    for (let i = 0; !(await evaluate(expression)); i++) { assert.ok(i < 60, 'Timed out: ' + label); await sleep(50); }
  };
  try {
    await send('Emulation.setDeviceMetricsOverride', {width: 390, height: 844, deviceScaleFactor: 1, mobile: true});
    await send('Emulation.setTouchEmulationEnabled', {enabled: true, maxTouchPoints: 5});
    await send('Page.navigate', {url});
    await waitFor("typeof store !== 'undefined' && store.length === 3", 'profile page');
    await evaluate("document.body.classList.add('android-app')");
    // A multi-image post without a preview gets its first item's image.
    await waitFor("store[0].image && !!document.querySelector('#grid .card img')", 'filled preview');
    assert.ok(await evaluate("store[0].image.startsWith('data:image/svg')"), 'first item becomes the preview');
    // Open it: swipe sideways with a finger, the counter and dots follow.
    await evaluate('lb(0)');
    await waitFor("!!document.querySelector('#lbcontent .slides')", 'carousel opened');
    assert.equal(await evaluate("document.querySelector('#lbcontent .slide-count').textContent"), '1/3');
    await send('Input.dispatchTouchEvent', {type: 'touchStart', touchPoints: [{x: 320, y: 400}]});
    for (let k = 1; k <= 10; k++) {
      await send('Input.dispatchTouchEvent', {type: 'touchMove', touchPoints: [{x: 320 - 26 * k, y: 402}]});
      await sleep(16);
    }
    await send('Input.dispatchTouchEvent', {type: 'touchEnd', touchPoints: []});
    await waitFor("document.querySelector('#lbcontent .slide-count').textContent === '2/3'", 'swipe to the second item');
    assert.equal(await evaluate("[...document.querySelectorAll('#lbcontent .slide-dots i')].findIndex(d => d.classList.contains('on'))"), 1);
    assert.equal(await evaluate('cur'), 0, 'a sideways swipe stays on the same post');
    // A photo opens with its preview straight away; if Instagram fails, the preview stays.
    await evaluate('lb(1)');
    assert.ok(await evaluate("!!document.querySelector('#lbcontent img')"), 'photo shows at once');
    await waitFor("!document.querySelector('#lbcontent .preview-wait')", 'loading finished');
    assert.ok(await evaluate("!!document.querySelector('#lbcontent > img')"), 'preview kept when the full photo fails');
    // Views fill in after the grid shows; photos get none.
    await evaluate('closeLb()');
    await waitFor("!document.querySelector('[data-views=\"2\"]').hidden", 'reel views filled in');
    assert.match(await evaluate("document.querySelector('[data-views=\"2\"]').textContent.trim()"), /^2\.8M$/);
    assert.equal(await evaluate("document.querySelector('[data-views=\"1\"]').hidden"), true, 'no views on a photo');
    await evaluate('lb(2)');
    assert.ok(await evaluate("document.getElementById('lbstats').textContent.includes('2.8M')"), 'the viewer shows views');
    // A sound page mixes creators: each post names who posted it.
    await send('Page.navigate', {url: url.replace('/test-profile', '/test-sound')});
    await waitFor("document.querySelectorAll('.tile-owner').length === 2", 'creators on the sound page');
    assert.deepEqual(await evaluate("[...document.querySelectorAll('.tile-owner')].map(e => e.textContent)"), ['@someone', '@other.creator']);
    await evaluate('lb(1)');
    assert.deepEqual(await evaluate("(a => [a.textContent, a.getAttribute('href')])(document.querySelector('#lbstats .lb-owner'))"),
      ['@other.creator', '/u/other.creator'], 'the post viewer links the creator');
    console.log('Profile carousel swipe, counter, dots and previews passed; sound pages name creators');
  } finally {
    socket.close(); browser.kill('SIGKILL');
    await fs.promises.rm(profile, {recursive: true, force: true, maxRetries: 10, retryDelay: 50});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
