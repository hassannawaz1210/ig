// The real profile page with fixture account details, on a phone and a desktop.
const {spawn} = require('node:child_process');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const [binary, url, screenshot] = process.argv.slice(2);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function launch() {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'ig-reels-'));
  const browser = spawn(binary, ['--headless', '--no-sandbox', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=0', '--user-data-dir=' + profile, '--window-size=1000,800', 'about:blank'],
    {stdio: ['ignore', 'ignore', 'pipe'], detached: process.platform !== 'win32'});
  const exited = new Promise(resolve => browser.once('close', resolve));
  const close = async () => {
    // CI's Chrome binary may be a launcher script. Stop its whole process
    // group, otherwise orphaned Chrome children keep writing the profile.
    if (browser.pid) {
      try {
        if (process.platform === 'win32') browser.kill('SIGKILL');
        else process.kill(-browser.pid, 'SIGKILL');
      } catch (error) { if (error.code !== 'ESRCH') throw error; }
      await Promise.race([exited, new Promise(resolve => {
        const timer = setTimeout(resolve, 5000); timer.unref();
      })]);
    }
    await fs.promises.rm(profile, {recursive: true, force: true, maxRetries: 20, retryDelay: 50});
  };
  let socketUrl;
  try {
    socketUrl = await new Promise((resolve, reject) => {
      let output = '';
      const timer = setTimeout(() => reject(new Error('no DevTools address')), 15000);
      browser.stderr.on('data', data => {
        output += data;
        const match = output.match(/DevTools listening on (ws:\S+)/);
        if (match) { clearTimeout(timer); resolve(match[1]); }
      });
      browser.on('error', reject);
      browser.on('exit', code => reject(new Error('browser exited with ' + code + ': ' + output.slice(-500))));
    });
  } catch (error) {
    await close();
    console.error('Could not start the browser:', error.message);
    process.exit(77);
  }
  const port = new URL(socketUrl).port;
  const page = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t => t.type === 'page');
  const socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let lastId = 0;
  const pending = new Map();
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    const waiter = pending.get(message.id);
    if (!waiter) return;
    pending.delete(message.id);
    message.error ? waiter.reject(new Error(JSON.stringify(message.error))) : waiter.resolve(message.result);
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    pending.set(++lastId, {resolve, reject});
    socket.send(JSON.stringify({id: lastId, method, params}));
  });
  const evaluate = async expression => {
    const result = await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
    if (result.exceptionDetails) throw new Error('page error: ' + JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  return {send, evaluate, close: async () => { socket.close(); await close(); }};
}

(async () => {
  const browser = await launch();
  try {
    const {send, evaluate} = browser;
    const waitFor = async expression => {
      for (let i = 0; !(await evaluate(expression)); i++) { assert.ok(i < 100, 'Timed out: ' + expression); await sleep(50); }
    };
    const box = selector => evaluate(`(() => { const r = document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();
      return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height}; })()`);
    await send('Page.enable');
    for (const phone of [true, false]) {
      const width = phone ? 390 : 1000;
      await send('Emulation.setDeviceMetricsOverride', {width, height: 844, deviceScaleFactor: 1, mobile: phone});
      await send('Page.navigate', {url});
      await waitFor("document.readyState === 'complete' && !!document.querySelector('.profile-summary')");
      await evaluate(`document.body.classList.toggle('android-app', ${phone})`);
      // The expired picture falls back to the initial.
      await waitFor("document.querySelector('.profile-avatar img').hidden");
      assert.equal(await evaluate("document.querySelector('.profile-avatar span').textContent"), 'C');
      assert.deepEqual(await evaluate("[...document.querySelectorAll('.profile-counts div')].map(d => d.querySelector('dd').textContent + ' ' + d.querySelector('dt').textContent)"),
        ['4,154 posts', '681M followers', '640 following']);
      assert.equal(await evaluate("document.querySelector('.profile-bio strong').textContent"), 'Cristiano Ronaldo');
      assert.ok(await evaluate("!!document.querySelector('.profile-bio .verified svg')"), 'verified badge');
      const bioLines = await evaluate("(() => { const p = document.querySelector('.profile-bio p'); return p.offsetHeight / parseFloat(getComputedStyle(p).lineHeight); })()");
      assert.ok(bioLines >= 2, 'bio keeps its line break');
      assert.equal(await evaluate("document.querySelector('.profile-links > a').textContent"), 'hrbl.me/CR7Pro2col');
      assert.equal(await evaluate("document.querySelector('.profile-links summary').textContent"), 'and 3 more');
      assert.ok(await evaluate("document.querySelector('.profile-links details a').checkVisibility() === false"), 'other links start folded');
      await evaluate("document.querySelector('.profile-links summary').click()");
      assert.deepEqual(await evaluate("[...document.querySelectorAll('.profile-links a')].map(a => [a.target, a.rel, a.protocol])"),
        [0, 1, 2, 3].map(i => ['_blank', 'noopener noreferrer', i === 0 || i === 3 ? 'https:' : 'http:']));
      assert.ok(await evaluate("document.querySelector('.profile-links details a').checkVisibility()"), 'other links open');
      assert.ok(await evaluate("!!document.querySelector('.page-header a.back[href=\"/\"]')"), 'back arrow stays');
      assert.equal(await evaluate("document.getElementById('addacc').textContent.trim()"), 'add to feed');
      const [avatar, counts, bio, button] = await Promise.all(
        ['.profile-avatar', '.profile-counts', '.profile-bio', '#addacc'].map(box));
      assert.ok(counts.left >= avatar.right, 'counts sit beside the picture');
      assert.ok(bio.top >= (phone ? avatar.bottom : counts.bottom), 'name and bio sit below');
      for (const [name, part] of Object.entries({avatar, counts, bio, button}))
        assert.ok(part.left >= 0 && part.right <= width, name + ' fits inside the screen');
      assert.ok(await evaluate('document.documentElement.scrollWidth <= innerWidth'), 'no sideways scrolling at ' + width + 'px');
      if (phone) {
        assert.equal(avatar.width, 86);
        await evaluate("document.querySelector('.profile-links details').open = false; window.scrollTo(0, 0)");
        if (screenshot) fs.writeFileSync(screenshot, Buffer.from((await send('Page.captureScreenshot', {format: 'png'})).data, 'base64'));
      }
    }
    console.log('Profile header: details, links, picture fallback and phone/desktop layout passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
