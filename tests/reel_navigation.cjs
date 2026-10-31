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
  ArrowUp: {key: 'ArrowUp', code: 'ArrowUp', windowsVirtualKeyCode: 38},
  ',': {key: ',', code: 'Comma', windowsVirtualKeyCode: 188, text: ','},
  Space: {key: ' ', code: 'Space', windowsVirtualKeyCode: 32, text: ' '},
  Enter: {key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13},
  Escape: {key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27},
  End: {key: 'End', code: 'End', windowsVirtualKeyCode: 35},
  '+': {key: '+', code: 'Equal', windowsVirtualKeyCode: 187, text: '+'},
  a: {key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, text: 'a'},
  Tab: {key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9},
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
    // Control comment responses to exercise races while switching reels.
    await evaluate(`globalThis.commentRequests = [];
      commentsPage = (p, after) => new Promise((resolve, reject) =>
        commentRequests.push({code:p.shortcode, after, resolve, reject}));
      void toggleComments(current)`);
    const replyComments = async (index, comments, cursor = null, fail = false) => {
      await evaluate(`commentRequests[${index}].${fail ? 'reject' : 'resolve'}(${JSON.stringify(
        fail ? 'offline' : {comments, next_cursor: cursor})}); undefined`);
      await sleep(80);
    };
    const comment = id => ({id, username:'fan', text:'same text', like_count:0});
    await replyComments(0, [comment('1'), comment('2')], 'page2');
    assert.equal(await evaluate('commentRequests.length'), 2, 'short panel automatically fetches another page');
    await evaluate('void toggleComments(current); void toggleComments(current)');
    await replyComments(1, [comment('2'), comment('3')], 'page2');
    assert.equal(await evaluate(`document.querySelectorAll('.comment-row').length`), 3,
      'reopening during pagination keeps distinct comments and removes repeated IDs');
    assert.equal(await evaluate('posts[current]._commentCursor'), null, 'repeated cursor stops pagination');

    await evaluate(`posts[current]._commentCursor = 'page3'; void loadMoreComments(posts[current]); showSlide(7)`);
    assert.equal(await evaluate(`document.getElementById('comments-panel').hidden`), false,
      'reel navigation keeps the drawer open');
    assert.equal(await evaluate('commentRequests.length'), 4, 'new reel fetches its own comments');
    await replyComments(2, [], null, true);
    assert.equal(await evaluate(`document.getElementById('comments-body').textContent`), 'Loading comments…',
      'a previous reel failure cannot overwrite the current panel');
    await replyComments(3, [], null, true);
    assert.ok(await evaluate(`document.querySelector('button#comments-more') !== null`),
      'failed initial request offers a keyboard-accessible retry');
    await evaluate(`document.querySelector('button#comments-more').click()`);
    await replyComments(4, [comment('4')], 'more');
    await replyComments(5, [comment('4')], 'next');
    await evaluate(`document.getElementById('comments-body').dispatchEvent(new Event('scroll'))`);
    assert.equal(await evaluate('commentRequests.length'), 6, 'duplicate-only page does not loop automatically');
    assert.ok(await evaluate(`document.querySelector('button#comments-more') !== null`),
      'underfilled panel can still load more without scrolling');
    await evaluate(`document.querySelector('button#comments-more').click()`);
    await replyComments(6, [], null, true);
    await evaluate(`document.querySelector('button#comments-more').click()`);
    await replyComments(7, [comment('5')]);
    assert.equal(await evaluate(`document.querySelectorAll('.comment-row').length`), 2,
      'page retry preserves existing comments');
    await evaluate(`posts[current]._comments = []; posts[current]._commentKeys = new Set();
      posts[current].comment_count = 1; renderComments(posts[current])`);
    assert.ok((await evaluate(`document.getElementById('comments-body').textContent`)).includes('Instagram lists 1 comment, but returned none.'),
      'an empty response does not pretend the advertised comment count is zero');
    await evaluate(`document.querySelector('button#comments-more').click()`);
    assert.equal(await evaluate('commentRequests[8].after'), null, 'retry reloads the first page');
    await replyComments(8, [comment('6')]);
    assert.equal(await evaluate(`document.querySelectorAll('.comment-row').length`), 1,
      'a previously empty result can be retried');
    await evaluate('closeComments(); showSlide(6)');
    assert.equal(await evaluate('commentRequests.length'), 9, 'closed drawer does not fetch on navigation');

    await key(',');
    assert.ok(await evaluate(`document.activeElement.dataset.all`), 'picker focuses All initially');
    await key('ArrowDown');
    assert.equal(await evaluate(`document.activeElement.dataset.feed`), 'memes');
    await key('Space');
    assert.deepEqual(await evaluate('chosenFeeds'), ['memes']);
    await key('End');
    await key('Space');
    assert.deepEqual(await evaluate('chosenFeeds'), ['islam', 'memes']);
    assert.equal(await evaluate('current'), 6, 'picker arrows and Space never navigate the reel');
    await key('Escape');
    assert.ok(await evaluate(`document.activeElement.matches('#feed-menu summary')`), 'Escape restores trigger focus');
    await key(',');
    await key('End');
    await key('Enter');
    await sleep(500);
    assert.deepEqual(await evaluate('selectedFeeds'), ['islam'], 'Enter saves the highlighted feed alone');
    assert.equal(await evaluate(`document.getElementById('feed-menu').open`), false);
    await key(',');
    if (process.env.IG_TEST_SCREENSHOT) {
      const capture = await send('Page.captureScreenshot', {format: 'png'});
      fs.writeFileSync(process.env.IG_TEST_SCREENSHOT, Buffer.from(capture.data, 'base64'));
    }
    await send('Emulation.setDeviceMetricsOverride', {width: 390, height: 844, deviceScaleFactor: 1, mobile: true});
    await sleep(200);
    assert.ok(await evaluate(`(() => {
      const r = document.querySelector('.feed-panel').getBoundingClientRect();
      return r.left >= 0 && r.right <= innerWidth && r.bottom <= innerHeight;
    })()`), 'picker fits the mobile viewport');
    if (process.env.IG_TEST_SCREENSHOT) {
      const capture = await send('Page.captureScreenshot', {format: 'png'});
      fs.writeFileSync(process.env.IG_TEST_SCREENSHOT.replace('.png', '-mobile.png'), Buffer.from(capture.data, 'base64'));
    }
    await key('+');
    assert.equal(await evaluate('document.activeElement.name'), 'feedname', 'plus opens creation with name focused');
    await send('Input.insertText', {text: 'My feed'});
    await key('Tab');
    assert.equal(await evaluate('document.activeElement.name'), 'accounts');
    await send('Input.insertText', {text: '@alpha @beta'});
    if (process.env.IG_TEST_SCREENSHOT) {
      const capture = await send('Page.captureScreenshot', {format:'png'});
      fs.writeFileSync(process.env.IG_TEST_SCREENSHOT.replace('.png', '-create.png'), Buffer.from(capture.data, 'base64'));
    }
    const submitFeed = async () => {
      await send('Input.dispatchKeyEvent', {type:'rawKeyDown', ...KEYS.Enter, modifiers:2});
      await send('Input.dispatchKeyEvent', {type:'keyUp', ...KEYS.Enter});
      for (let tries = 0; await evaluate(`!!document.getElementById('feed-picker')`); tries++) {
        assert.ok(tries < 40, 'feed dialog did not save');
        await sleep(50);
      }
      await sleep(150);
    };
    await submitFeed();
    assert.ok(await evaluate(`!!document.querySelector('.feed-choice[data-feed="my_feed"]')`), 'new feed is selectable without reload');
    assert.deepEqual(await evaluate('selectedFeeds'), ['islam'], 'creating a feed preserves the current selection');
    await key('a');
    await sleep(100);
    assert.equal(await evaluate(`document.querySelector('#feed-picker textarea').value`),
      await evaluate(`'@' + posts[current].owner`), 'A pre-fills the current account');
    await submitFeed();
    await evaluate('boot.feeds = []; updateFeedStatus()');
    assert.equal(await evaluate(`document.getElementById('feed-create').hidden`), false, 'new users see Create feed');
    console.log('Reel navigation, feed selection, creation and account entry passed');
  } finally { browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
