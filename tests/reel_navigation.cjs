// Drives the reels page in headless Chromium through the DevTools protocol and
// checks that each key press, wheel notch or extension scroll moves one reel.
// Usage: node reel_navigation.cjs <chromium binary> <reels page URL>
// Exit code 77 means the browser could not be started (the test is skipped).
const {spawn} = require('node:child_process');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const [binary, url] = process.argv.slice(2);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function launch() {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'ig-reels-'));
  const browser = spawn(binary, ['--headless', '--no-sandbox', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=0', '--user-data-dir=' + profile, '--window-size=1000,800', 'about:blank'],
    {stdio: ['ignore', 'ignore', 'pipe']});
  const close = () => { browser.kill('SIGKILL'); fs.rmSync(profile, {recursive: true, force: true}); };
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
    close();
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
  return {send, evaluate, close: () => { socket.close(); close(); }};
}

const KEYS = {
  j: {key: 'j', code: 'KeyJ', windowsVirtualKeyCode: 74, text: 'j'},
  k: {key: 'k', code: 'KeyK', windowsVirtualKeyCode: 75, text: 'k'},
  ArrowDown: {key: 'ArrowDown', code: 'ArrowDown', windowsVirtualKeyCode: 40},
};

(async () => {
  const browser = await launch();
  try {
    const {send, evaluate} = browser;
    await send('Page.enable');
    await send('Page.navigate', {url});
    for (let tries = 0; !(await evaluate(`typeof current === 'number' && current === 0`)); tries++) {
      assert.ok(tries < 50, 'reels page did not load');
      await sleep(100);
    }
    // The reel whose top edge is at the top of the feed, as the user sees it.
    const shown = () => evaluate(`(() => {
      const top = feed.getBoundingClientRect().top;
      const slide = [...feed.querySelectorAll('.slide')].find(s =>
        s.style.display !== 'none' && Math.abs(s.getBoundingClientRect().top - top) < 2);
      return slide ? +slide.id.slice(1) : null;
    })()`);
    const expectReel = async (expected, message) => {
      await sleep(400);
      assert.equal(await shown(), expected, message + ' (reel in view)');
      assert.equal(await evaluate('current'), expected, message + ' (current reel)');
    };
    const key = async (name, {holdMs = 60, repeats = 0} = {}) => {
      const k = KEYS[name];
      await send('Input.dispatchKeyEvent', {type: k.text ? 'keyDown' : 'rawKeyDown', ...k});
      for (let n = 0; n < repeats; n++) {
        await sleep(30);
        await send('Input.dispatchKeyEvent', {type: k.text ? 'keyDown' : 'rawKeyDown', ...k, autoRepeat: true});
      }
      await sleep(holdMs);
      await send('Input.dispatchKeyEvent', {type: 'keyUp', key: k.key, code: k.code, windowsVirtualKeyCode: k.windowsVirtualKeyCode});
    };
    const wheel = deltaY => send('Input.dispatchMouseEvent', {type: 'mouseWheel', x: 400, y: 400, deltaX: 0, deltaY});

    await key('j'); await expectReel(1, 'J moves one reel');
    await key('ArrowDown'); await expectReel(2, 'ArrowDown moves one reel');
    await key('k'); await expectReel(1, 'K moves back one reel');
    await key('j', {repeats: 10}); await expectReel(2, 'a held key moves one reel');
    await key('j', {holdMs: 20}); await sleep(40); await key('j', {holdMs: 20});
    await expectReel(4, 'a quick double tap moves two reels');

    // Pressing a key after clicking a side-bar button.
    const button = await evaluate(`(() => { const b = document.querySelector('#s' + current + ' .reel-like').getBoundingClientRect();
      return {x: b.x + b.width / 2, y: b.y + b.height / 2}; })()`);
    for (const type of ['mousePressed', 'mouseReleased'])
      await send('Input.dispatchMouseEvent', {type, ...button, button: 'left', clickCount: 1});
    assert.ok(await evaluate(`document.activeElement.classList.contains('reel-like')`), 'the clicked button has focus');
    await key('ArrowDown'); await expectReel(5, 'a focused button still moves one reel');

    // Vimium handles j/k itself (the page never sees them) and scrolls the feed
    // in small scrollBy() steps, one per animation frame. With mandatory snapping
    // each step used to land on the next reel: one press skipped about six.
    const extensionScroll = sign => evaluate(`new Promise(done => {
      let moved = 0;
      const step = () => {
        const before = feed.scrollTop;
        feed.scrollBy({top: ${sign} * 10, behavior: 'instant'});
        if (feed.scrollTop !== before && (moved += 10) < 60) requestAnimationFrame(step); else done();
      };
      requestAnimationFrame(step);
    })`);
    await extensionScroll(1); await expectReel(6, 'an extension scroll down moves one reel');
    await extensionScroll(-1); await expectReel(5, 'an extension scroll up moves one reel');

    await wheel(100); await expectReel(6, 'one wheel notch moves one reel');
    await wheel(-100); await expectReel(5, 'one wheel notch up moves one reel');
    for (let n = 0; n < 12; n++) { await wheel(8); await sleep(16); }
    await expectReel(6, 'one trackpad swipe moves one reel');

    // The feed selector stays open while choosing multiple feeds. Resuming
    // J/K navigation must dismiss it instead of silently swallowing the key.
    await evaluate(`document.querySelector('#feed-menu summary').click();
      document.querySelector('#feed-menu summary').focus()`);
    assert.ok(await evaluate(`document.getElementById('feed-menu').open`));
    await key('j'); await expectReel(7, 'J resumes navigation with the feed menu open');
    assert.equal(await evaluate(`document.getElementById('feed-menu').open`), false);
    await evaluate(`document.querySelector('#feed-menu summary').click()`);
    await key('k'); await expectReel(6, 'K resumes navigation with the feed menu open');
    assert.equal(await evaluate(`document.getElementById('feed-menu').open`), false);
    console.log('Reel navigation moved one reel per key press, wheel notch and scroll');
  } finally { browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
