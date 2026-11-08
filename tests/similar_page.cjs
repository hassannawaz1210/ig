// The suggested accounts page in headless Chromium at phone width.
const {spawn} = require('node:child_process');
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const [binary, url] = process.argv.slice(2);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'ig-similar-'));
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
  socket.onmessage = event => { const m = JSON.parse(event.data);
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
    await send('Page.navigate', {url: url + '/u/star/similar'});
    await waitFor("document.getElementById('similar')?.getAttribute('aria-busy') === 'false'", 'suggestions loaded');
    assert.equal(await evaluate("document.querySelectorAll('.similar-card.placeholder').length"), 0, 'placeholders replaced');
    assert.deepEqual(await evaluate("[...document.querySelectorAll('.similar-name')].map(e => e.firstChild.textContent)"),
      ['friend', 'Kept', '<b>x</b>'], 'every suggestion, text not markup');
    assert.deepEqual(await evaluate("[...document.querySelectorAll('.similar-add')].map(b => b.dataset.state)"), ['out', 'in', 'out'],
      'accounts already in a feed say so');
    assert.equal(await evaluate("document.querySelector('.similar-link').getAttribute('href')"), '/u/friend');
    assert.ok(await evaluate("document.querySelector('.similar-picture').getBoundingClientRect().width >= 80"), 'larger cards');
    assert.ok(await evaluate('document.documentElement.scrollWidth <= innerWidth'), 'no sideways scrolling at 390px');
    // Adding opens the usual feed sheet; another page adding the account updates the card.
    await evaluate("window.chooseFeed = async name => { window.chosen = name; return true; }; document.querySelector('.similar-add').click()");
    await waitFor("document.querySelector('.similar-add').dataset.state === 'in'", 'added');
    assert.equal(await evaluate('window.chosen'), 'friend');
    await evaluate("accountChanged('KEPT', false)");
    assert.equal(await evaluate("document.querySelectorAll('.similar-add')[1].dataset.state"), 'out', 'changes from other pages apply');
    // Failure: a clear message with Try again, which recovers.
    await send('Page.navigate', {url: url + '/u/broken/similar'});
    await waitFor("!!document.querySelector('.similar-empty button')", 'retry offered');
    assert.match(await evaluate("document.querySelector('.similar-empty').textContent"), /limiting/);
    await send('Page.navigate', {url: url + '/u/nobody/similar'});
    await waitFor("!!document.querySelector('.similar-empty')", 'empty state');
    assert.match(await evaluate("document.querySelector('.similar-empty').textContent"), /no suggestions for @nobody/);
    assert.deepEqual(errors, []);
    console.log('Suggested accounts page: cards, states, add, sync, failure and empty passed');
  } finally {
    socket.close(); browser.kill('SIGKILL');
    await fs.promises.rm(profile, {recursive: true, force: true, maxRetries: 10, retryDelay: 50});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
