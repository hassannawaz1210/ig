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

const KEYS = {
  h: {key:'h',code:'KeyH',windowsVirtualKeyCode:72,text:'h'},
  j: {key: 'j', code: 'KeyJ', windowsVirtualKeyCode: 74, text: 'j'},
  k: {key: 'k', code: 'KeyK', windowsVirtualKeyCode: 75, text: 'k'},
  ArrowDown: {key: 'ArrowDown', code: 'ArrowDown', windowsVirtualKeyCode: 40},
  ArrowUp: {key: 'ArrowUp', code: 'ArrowUp', windowsVirtualKeyCode: 38},
  ',': {key: ',', code: 'Comma', windowsVirtualKeyCode: 188, text: ','},
  Space: {key: ' ', code: 'Space', windowsVirtualKeyCode: 32, text: ' '},
  Enter: {key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: '\r'},
  Escape: {key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27},
  Delete: {key: 'Delete', code: 'Delete', windowsVirtualKeyCode:46},
  End: {key: 'End', code: 'End', windowsVirtualKeyCode: 35},
  '+': {key: '+', code: 'Equal', windowsVirtualKeyCode: 187, text: '+'},
  a: {key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, text: 'a'},
  m: {key: 'm', code: 'KeyM', windowsVirtualKeyCode: 77, text: 'm'},
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
    const shareUrls = await evaluate(`posts.slice(0,2).map(p=>'https://www.instagram.com/reel/'+p.shortcode+'/')`);
    await evaluate(`window.shareCalls=[]; window.copyCalls=[];
      Object.defineProperty(navigator,'share',{configurable:true,writable:true,value:async data=>shareCalls.push(data)});
      Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>copyCalls.push(text)}});
      document.querySelector('#s0 .reel-share').click()`);
    await sleep(50);
    assert.deepEqual(await evaluate('shareCalls'),[{url:shareUrls[0]}]);
    await evaluate(`navigator.share=async()=>{throw new DOMException('Cancelled','AbortError')}; shareReel(0)`);
    assert.deepEqual(await evaluate('copyCalls'),[],'cancel does not copy');
    await evaluate(`navigator.share=undefined; shareReel(1)`);
    assert.deepEqual(await evaluate('copyCalls'),[shareUrls[1]]);
    assert.equal(await evaluate('current'),0,'sharing does not switch reels');
    assert.equal(await evaluate('location.href'),url,'sharing does not navigate away');
    await evaluate(`delete navigator.share; delete navigator.clipboard`);
    // Search fills the phone width and keeps its input above scrolling results.
    await evaluate(`document.body.classList.add('android-app');showSearch()`);
    await sleep(100);
    for (const [width,height] of [[360,800],[390,430],[780,360]]) {
      await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:true});
      await send('Emulation.setTouchEmulationEnabled',{enabled:true});
      await sleep(100);
      const searchLayout = await evaluate(`(() => {
        const dialog=document.getElementById('reel-search'), results=dialog.querySelector('.search-results');
        results.replaceChildren(...Array.from({length:30},(_,n)=>{const b=document.createElement('button');b.textContent='@creator'+n;return b}));
        const rect=dialog.getBoundingClientRect(), input=dialog.querySelector('input').getBoundingClientRect();
        const before=input.top;results.scrollTop=200;
        return {left:rect.left,width:rect.width,top:rect.top,bottom:rect.bottom,
          viewport:innerWidth,height:innerHeight,scrollable:results.scrollHeight>results.clientHeight,
          inputFixed:dialog.querySelector('input').getBoundingClientRect().top===before};
      })()`);
      assert.ok(Math.abs(searchLayout.left)<1 && Math.abs(searchLayout.width-searchLayout.viewport)<1,JSON.stringify(searchLayout));
      assert.ok(searchLayout.top>=0 && searchLayout.bottom<=searchLayout.height+1,JSON.stringify(searchLayout));
      assert.ok(searchLayout.scrollable && searchLayout.inputFixed,'only search results scroll');
      if (width===360 && process.env.IG_SEARCH_SCREENSHOT) {
        const shot=await send('Page.captureScreenshot');fs.writeFileSync(process.env.IG_SEARCH_SCREENSHOT,Buffer.from(shot.data,'base64'));
      }
    }
    await evaluate(`document.getElementById('reel-search').close();document.body.classList.remove('android-app')`);
    await send('Emulation.clearDeviceMetricsOverride');
    await send('Emulation.setTouchEmulationEnabled',{enabled:false});
    // The reel whose top edge is at the top of the feed, as the user sees it.
    await evaluate(`showSearch(); window.searchRoute=null; window.originalVisit=visitLocalPage;
      visitLocalPage=async path=>{searchRoute=path};
      var searchInput=document.querySelector('#reel-search input');searchInput.value='@ocean.of.films';searchInput.dispatchEvent(new Event('input'));
      document.querySelector('#reel-search form').requestSubmit()`);
    assert.equal(await evaluate('searchRoute'),'/u/ocean.of.films');
    await evaluate(`showSearch(); var searchInput=document.querySelector('#reel-search input');
      searchInput.value='https://www.instagram.com/reel/DSmBF6-k8rM/?tracking=example';searchInput.dispatchEvent(new Event('input'));
      document.querySelector('#reel-search form').requestSubmit()`);
    assert.equal(await evaluate('searchRoute'),'/reel/DSmBF6-k8rM');
    await evaluate(`showSearch();var searchInput=document.querySelector('#reel-search input');searchInput.value='https://evil.example/reel/DSmBF6-k8rM/';searchInput.dispatchEvent(new Event('input'))`);
    assert.equal(await evaluate(`document.querySelector('#reel-search .search-results').children.length`),0);
    await evaluate(`document.getElementById('reel-search').close();visitLocalPage=originalVisit`);
    await sleep(50);
    await evaluate(`document.activeElement.blur()`);
    const shown = () => evaluate(`(() => {
      const top = feed.getBoundingClientRect().top;
      const slide = [...feed.querySelectorAll('.slide')].find(s =>
        s.style.display !== 'none' && Math.abs(s.getBoundingClientRect().top - top) < 2);
      return slide ? +slide.id.slice(1) : null;
    })()`);
    const expectReel = async (expected, message) => {
      await sleep(400);
      assert.equal(await evaluate('location.href'), url, message + ' (stays on reels page)');
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

    // Real touch input in a phone viewport, including a pause longer than the
    // desktop scroll-burst timeout and a fast fling with native momentum.
    await send('Emulation.setDeviceMetricsOverride', {width:390,height:844,deviceScaleFactor:1,mobile:true});
    await send('Emulation.setTouchEmulationEnabled', {enabled:true,maxTouchPoints:5});
    const touch = (type, points) => send('Input.dispatchTouchEvent', {type,
      touchPoints:points.map(([x,y,id=1]) => ({x,y,id}))});
    const swipe = async (from, to, pause=0) => {
      await touch('touchStart', [[180,from]]);
      await touch('touchMove', [[180,(from+to)/2]]);
      if (pause) await sleep(pause);
      await touch('touchMove', [[180,to]]);
      await touch('touchEnd', []);
    };
    await sleep(250);
    await evaluate(`window.holdVideo=document.createElement('video');document.getElementById('s'+current).append(holdVideo);
      Object.defineProperty(holdVideo,'paused',{configurable:true,value:false});holdVideo.playbackRate=1.25;
      window.beforeHoldPaused=posts[current]._paused`);
    await touch('touchStart', [[180,440]]); await sleep(450);
    assert.equal(await evaluate('holdVideo.playbackRate'),2,'holding plays at 2x');
    await touch('touchEnd', []); await sleep(100);
    assert.equal(await evaluate('holdVideo.playbackRate'),1.25,'release restores previous speed');
    assert.equal(await evaluate('posts[current]._paused===beforeHoldPaused'),true,'hold release does not tap-pause');
    await touch('touchStart', [[180,440]]); await sleep(450);
    await touch('touchCancel', []);
    assert.equal(await evaluate('holdVideo.playbackRate'),1.25,'cancel restores speed');
    await evaluate(`holdVideo.remove()`);
    await swipe(740,100); await expectReel(7, 'a phone fling moves one reel');
    await sleep(600); await expectReel(7, 'momentum cannot advance another reel');
    await swipe(740,100,350); await expectReel(8, 'a paused phone swipe moves one reel');
    await swipe(100,740); await expectReel(7, 'a downward swipe goes back one reel');
    await swipe(440,420); await expectReel(7, 'a small touch movement does not navigate');
    await touch('touchStart', [[180,740]]);
    await touch('touchMove', [[180,100]]);
    await touch('touchCancel', []);
    await expectReel(7, 'a cancelled swipe does not navigate');
    await swipe(740,100); await swipe(740,100);
    await expectReel(9, 'two distinct quick phone swipes move two reels');
    await touch('touchStart', [[100,440]]);
    await touch('touchMove', [[280,440]]);
    await touch('touchEnd', []);
    await expectReel(9, 'a horizontal gesture does not advance the feed');
    await evaluate(`posts[current]._comments = Array.from({length:30}, (_,i) =>
      ({id:String(i),username:'viewer',text:'Comment '+i,like_count:0}));
      posts[current]._commentCursor=null; toggleComments(current)`);
    await sleep(300);
    const sheet = await evaluate(`(() => {
      const p=document.getElementById('comments-panel').getBoundingClientRect();
      return {top:p.top,bottom:p.bottom,width:p.width,feedWidth:feed.getBoundingClientRect().width};
    })()`);
    const preview = await send('Page.captureScreenshot', {format:'png'});
    fs.writeFileSync('/tmp/ig-phone-comments.png', Buffer.from(preview.data,'base64'));
    assert.equal(sheet.width,390,'phone comments span screen width');
    assert.equal(sheet.feedWidth,390,'phone reel is not squeezed sideways');
    assert.equal(sheet.bottom,844,'comments are anchored to bottom');
    assert.ok(sheet.top>100,'reel remains visible above sheet');
    await swipe(740,400);
    await expectReel(9,'scrolling comments does not swipe reels');
    assert.ok(await evaluate(`document.getElementById('comments-body').scrollTop > 0`), 'comments scroll inside sheet');
    await touch('touchStart', [[180,sheet.top+12]]);
    await touch('touchMove', [[180,sheet.top+140]]);
    await touch('touchEnd', []);
    await sleep(300);
    assert.equal(await evaluate(`document.getElementById('comments-panel').hidden`),true,'drag handle down dismisses sheet');
    await evaluate('toggleComments(current)');
    await touch('touchStart', [[180,80]]); await touch('touchEnd', []);
    await sleep(300);
    assert.equal(await evaluate(`document.getElementById('comments-panel').hidden`),true,'tapping backdrop dismisses sheet');
    await send('Emulation.setTouchEmulationEnabled', {enabled:false});
    await send('Emulation.clearDeviceMetricsOverride');
    await evaluate('showSlide(6)');
    await expectReel(6, 'desktop navigation resumes after touch');

    await evaluate(`(() => {
      const v=document.createElement('video'); v.id='auto-test';
      Object.defineProperty(v,'ended',{value:true}); document.getElementById('s'+current).append(v);
      toggleAutoScroll(); document.getElementById('comments-panel').hidden=false;
      advanceAutoScroll();
    })()`);
    assert.equal(await evaluate('current'),6,'auto-scroll waits while comments are open');
    await evaluate(`closeComments(); posts[current]._paused=true; advanceAutoScroll()`);
    assert.equal(await evaluate('current'),6,'auto-scroll respects explicit pause');
    await evaluate(`posts[current]._paused=false; advanceAutoScroll(); toggleAutoScroll()`);
    await expectReel(7,'auto-scroll advances one ended reel');
    await send('Input.dispatchKeyEvent',{type:'keyDown',key:'t',code:'KeyT',windowsVirtualKeyCode:84});
    await send('Input.dispatchKeyEvent',{type:'keyUp',key:'t',code:'KeyT',windowsVirtualKeyCode:84});
    assert.equal(await evaluate('autoScroll'),true,'T enables auto-scroll');
    await evaluate(`toggleAutoScroll(); document.getElementById('auto-test')?.remove(); showSlide(6)`);

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
    await key(',');
    await key('End');
    assert.equal(await evaluate('document.activeElement.dataset.feed'), 'my_feed');
    await key('Delete');
    await sleep(200);
    assert.ok(await evaluate(`!!document.querySelector('.feed-choice[data-feed="my_feed"]')`), 'Delete key does not delete without confirmation');
    assert.equal(await evaluate('document.activeElement.textContent'), 'Cancel', 'confirmation defaults to Cancel');
    await key('Enter');
    assert.equal(await evaluate(`document.getElementById('feed-delete-confirm')`), null);
    assert.ok(await evaluate(`!!document.querySelector('.feed-choice[data-feed="my_feed"]')`), 'Cancel preserves the feed');
    await evaluate(`document.querySelector('.feed-choice[data-feed="my_feed"]').closest('.feed-row').querySelector('.feed-delete').click()`);
    assert.ok(await evaluate(`!!document.getElementById('feed-delete-confirm')`), 'trash icon also requires confirmation');
    await key('Tab');
    assert.equal(await evaluate('document.activeElement.textContent'), 'Delete feed');
    await key('Enter');
    await sleep(200);
    assert.equal(await evaluate(`document.querySelector('.feed-choice[data-feed="my_feed"]')`), null);
    assert.ok(await evaluate(`!!document.querySelector('#action-status button')`), 'delete offers Undo without a modal');
    await evaluate(`document.querySelector('#action-status button').click()`);
    await sleep(200);
    assert.ok(await evaluate(`!!document.querySelector('.feed-choice[data-feed="my_feed"]')`), 'Undo restores the feed');
    assert.deepEqual(await evaluate('selectedFeeds'), ['islam'], 'deleting another feed preserves selection');
    // Exercise actual media events, not just mocked progress callbacks.
    await evaluate(`(async () => {
      closeFeedMenu(); closeComments();
      window.songRequests = [];
      const oldFetch = window.fetch;
      window.fetch = (url, options) => {
        if (String(url).startsWith('/recognize/')) {
          songRequests.push(String(url));
          return Promise.resolve(new Response(JSON.stringify({status:'matched',title:'Test song',artist:'Test artist'}),
            {status:200,headers:{'Content-Type':'application/json'}}));
        }
        return oldFetch(url, options);
      };
      const canvas = document.createElement('canvas'); canvas.width=90; canvas.height=160;
      const paint = canvas.getContext('2d');
      const stream = canvas.captureStream(10);
      const recorder = new MediaRecorder(stream, {mimeType:'video/webm'});
      const chunks = [];
      recorder.ondataavailable = e => chunks.push(e.data);
      const stopped = new Promise(resolve => recorder.onstop=resolve);
      recorder.start();
      let frame=0;
      const draw = setInterval(() => {paint.fillStyle=frame++ % 2 ? 'red' : 'blue';paint.fillRect(0,0,90,160);}, 80);
      await new Promise(resolve=>setTimeout(resolve,1000));
      recorder.stop(); await stopped; clearInterval(draw); stream.getTracks().forEach(t=>t.stop());
      const p=posts[current];
      p.reservation='a'.repeat(32); p.video=URL.createObjectURL(new Blob(chunks,{type:'video/webm'}));
      p._paused=false; setVideo(p,current,true);
    })()`);
    await sleep(3500);
    const viewing = await evaluate(`({seconds:posts[current]._watchedSeconds, recorded:posts[current]._viewRecorded,
      duration:document.querySelector('#s'+current+' video').duration, looped:posts[current]._looped, time:document.querySelector('#s'+current+' video').currentTime, paused:document.querySelector('#s'+current+' video').paused, ready:document.querySelector('#s'+current+' video').readyState, interrupted:posts[current]._interrupted})`);
    assert.ok(viewing.recorded, 'visible video playback reports its first actual view');
    assert.ok(viewing.seconds > .2 && viewing.seconds < 1.5, 'looping a one-second video does not accumulate seconds indefinitely');
    assert.ok(viewing.looped, 'test video completed a loop: ' + JSON.stringify(viewing));
    assert.equal(await evaluate('songRequests.length'), 1, 'short looping video still starts automatic recognition');
    assert.equal(await evaluate("document.getElementById('song'+current).hidden"), false);
    assert.equal(await evaluate("document.getElementById('song'+current).textContent.trim()"), 'Test song · Test artist');
    const originalSlot = await evaluate('({index:current,code:posts[current].shortcode})');
    await evaluate(`(() => {
      const previousFetch = window.fetch;
      window.creatorRequests = 0;
      window.fetch = (url, options) => {
        if (String(url).startsWith('/creator/')) {
          creatorRequests++;
          return Promise.resolve(new Response(JSON.stringify({posts:[{shortcode:'branch001',
            owner:posts[current].owner,is_video:true,caption:'Same creator reel',reservation:'b'.repeat(32)}],
            has_more:true}),{status:200,headers:{'Content-Type':'application/json'}}));
        }
        return previousFetch(url, options);
      };
    })()`);
    await send('Input.dispatchKeyEvent',{type:'keyDown',key:'ArrowRight',code:'ArrowRight',windowsVirtualKeyCode:39});
    await send('Input.dispatchKeyEvent',{type:'keyUp',key:'ArrowRight',code:'ArrowRight',windowsVirtualKeyCode:39});
    await sleep(200);
    assert.equal(await evaluate('current'),originalSlot.index,'horizontal browse keeps mixed-feed position');
    assert.equal(await evaluate('posts[current].shortcode'),'branch001');
    assert.equal(await evaluate("document.getElementById('creator-browse')"),null,'no creator-browsing banner');
    await send('Input.dispatchKeyEvent',{type:'keyDown',key:'ArrowLeft',code:'ArrowLeft',windowsVirtualKeyCode:37});
    await send('Input.dispatchKeyEvent',{type:'keyUp',key:'ArrowLeft',code:'ArrowLeft',windowsVirtualKeyCode:37});
    assert.equal(await evaluate('posts[current].shortcode'),originalSlot.code);
    await send('Input.dispatchKeyEvent',{type:'keyDown',key:'ArrowRight',code:'ArrowRight',windowsVirtualKeyCode:39});
    await send('Input.dispatchKeyEvent',{type:'keyUp',key:'ArrowRight',code:'ArrowRight',windowsVirtualKeyCode:39});
    assert.equal(await evaluate('creatorRequests'),1,'back/forward reuses loaded creator reel');
    await key('j');
    await sleep(200);
    assert.equal(await evaluate('creatorTrail'),null,'J resumes the mixed feed');
    assert.equal(await evaluate('posts['+originalSlot.index+'].shortcode'),'branch001','vertical navigation retains the horizontal reel');
    await key('k');
    assert.equal(await evaluate('posts[current].shortcode'),'branch001','returning keeps the horizontal position');
    await evaluate('browseCreator(-1)');
    assert.equal(await evaluate('posts[current].shortcode'),originalSlot.code,'previous horizontal reel remains accessible');
    await evaluate('browseCreator(1)');
    assert.equal(await evaluate('posts[current].shortcode'),'branch001');
    assert.equal(await evaluate('creatorRequests'),1,'returning reuses the horizontal history');
    await key('j');
    await evaluate(`(() => {
      const previousFetch=window.fetch;
      window.fetch=(url,options)=>String(url).startsWith('/creator/') ?
        new Promise(resolve=>window.finishCreatorRequest=resolve) : previousFetch(url,options);
      void browseCreator(1);
    })()`);
    await key('j');
    const afterLeaving = await evaluate('posts[current].shortcode');
    await evaluate(`finishCreatorRequest(new Response(JSON.stringify({posts:[{shortcode:'latebranch',
      owner:'creator',reservation:'c'.repeat(32)}],has_more:true}),
      {status:200,headers:{'Content-Type':'application/json'}}))`);
    await sleep(100);
    assert.equal(await evaluate('posts[current].shortcode'),afterLeaving,'late creator response cannot replace the mixed reel');
    assert.equal(await evaluate('creatorTrail'),null);
    await evaluate('boot.feeds = []; updateFeedStatus()');
    assert.equal(await evaluate(`document.getElementById('feed-create').hidden`), false, 'new users see Create feed');
    for (const [width,height] of [[320,568],[390,844],[640,320],[1000,800]]) {
      await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<700});
      await sleep(100);
      await evaluate('showShortcuts()');
      await sleep(100);
      const bounds = await evaluate(`(() => {
        const d=document.getElementById('shortcut-help'), list=d.querySelector('dl');
        const r=d.getBoundingClientRect(), b=d.querySelector('button').getBoundingClientRect();
        list.scrollTop=list.scrollHeight;
        return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,buttonBottom:b.bottom,
          scrollable:list.scrollHeight>list.clientHeight,scrolled:list.scrollTop>0,
          overflows:list.scrollWidth>list.clientWidth};
      })()`);
      assert.ok(bounds.top>=0 && bounds.bottom<=height && bounds.left>=0 && bounds.right<=width,
        'shortcut dialog fits '+width+'x'+height);
      assert.ok(bounds.buttonBottom<=height,'Done stays on screen');
      assert.ok(!bounds.scrollable || bounds.scrolled,'overflowing shortcut list scrolls at '+width+'x'+height);
      assert.equal(bounds.overflows,false,'shortcut list does not overflow sideways');
      await evaluate("document.getElementById('shortcut-help').close()");
      await sleep(100);
    }
    // The Android shell uses the same player with its own touch controls.
    await send('Emulation.setDeviceMetricsOverride', {width:390,height:844,deviceScaleFactor:1,mobile:true});
    await evaluate("document.body.classList.add('android-app'); setupAndroidUI(); boot.feeds=['islam']; feeded.add('creator2'); showSlide(visibleIndices()[0]); document.querySelectorAll('.spin').forEach(el=>el.hidden=true); updateFeedStatus();");
    await sleep(200);
    const mobile = await evaluate(`(() => {
      const nav=document.querySelector('.android-nav').getBoundingClientRect();
      const reel=document.querySelector('.slide:not([hidden])').getBoundingClientRect();
      return {navTop:nav.top,navBottom:nav.bottom,reelHeight:reel.height,
        shortcuts:getComputedStyle(document.getElementById('shortcuts')).display,
        more:getComputedStyle(document.querySelector('.android-reel-more')).display,
        rail:[...document.querySelector('.slide:not([hidden]) .bar').children].filter(el=>getComputedStyle(el).display!=='none').map(el=>el.className),
        navItems:[...document.querySelectorAll('.android-nav > *')].map(el=>el.textContent.trim()),
        autoScrollButton:!!document.getElementById('auto-scroll')};
    })()`);
    assert.equal(mobile.shortcuts,'none','Android has no keyboard-shortcut button');
    // How old each post is, shown beside the creator on the phone.
    const ages = await evaluate(`(() => { const now = Date.UTC(2026, 10, 7, 12); const t = s => now / 1000 - s;
      return [postAge(t(20), now), postAge(t(60), now), postAge(t(7200), now), postAge(t(86400), now),
        postAge(t(3 * 86400), now), postAge(t(15 * 86400), now), postAge(t(70 * 86400), now), postAge(t(800 * 86400), now), postAge(0, now)]; })()`);
    assert.deepEqual(ages, ['just now', '1 minute ago', '2 hours ago', '1 day ago', '3 days ago', '2 weeks ago', '2 months ago', '2 years ago', '']);
    const dated = await evaluate(`(() => { const d = document.querySelector('.slide:not([hidden]) .info .date');
      return d && {text: d.textContent, display: getComputedStyle(d).display, datetime: d.getAttribute('datetime'), top: d.getBoundingClientRect().top,
        userTop: d.parentNode.querySelector('.user').getBoundingClientRect().top}; })()`);
    assert.ok(dated, 'reels show their post date');
    assert.match(dated.text, / ago · /, 'age and date: ' + dated.text);
    assert.notEqual(dated.display, 'none', 'the post date is visible on the phone');
    assert.ok(Math.abs(dated.top - dated.userTop) < 12, 'date sits beside the creator name');
    assert.equal(mobile.more,'flex','Android reel actions have a visible touch control');
    assert.ok(mobile.rail.some(c=>c.includes('account-action')),'add creator sits in the reel rail');
    assert.ok(!mobile.rail.some(c=>c.includes('profile-action')),'profile has no redundant rail button');
    assert.equal(await evaluate(`document.querySelector('#s'+current+' .user').hasAttribute('target')`),false,'username opens profile within the app');
    assert.deepEqual(mobile.navItems,['Watch','Saved','Settings'],'feeds are chosen from the top menu only');
    assert.equal(mobile.autoScrollButton,false,'auto-scroll lives in Settings');
    assert.equal(mobile.navBottom,844,'bottom navigation fits the viewport');
    assert.equal(mobile.reelHeight,mobile.navTop,'video viewport ends above navigation');
    fs.writeFileSync('/tmp/ig-android-watch.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
    // Clean view never resizes the player or changes playback state.
    await evaluate("closeComments(); closeFeedMenu(); window.cleanVideo=document.createElement('video'); document.getElementById('s'+current).append(cleanVideo); Object.defineProperty(cleanVideo,'paused',{configurable:true,value:false}); cleanVideo.currentTime=7; window.cleanBefore={index:current,time:cleanVideo.currentTime,paused:cleanVideo.paused,height:feed.clientHeight}");
    const twoFingerTap = async () => {
      await touch('touchStart',[[140,350,1]]);
      await touch('touchStart',[[140,350,1],[220,350,2]]);
      await touch('touchEnd',[[220,350,2]]);
      await touch('touchEnd',[]);
    };
    for(let n=0;n<4;n++) {
      await twoFingerTap();
      assert.equal(await evaluate('cleanView'),n%2===0,'two-finger taps repeatedly toggle Clean view');
    }
    await twoFingerTap();
    const cleanState=await evaluate(`({height:feed.clientHeight,index:current,same:cleanVideo===document.querySelector('#s'+current+' video'),time:cleanVideo.currentTime,paused:cleanVideo.paused,
      info:getComputedStyle(document.querySelector('#s'+current+' .info')).visibility,
      actions:getComputedStyle(document.querySelector('#s'+current+' .bar')).visibility,
      nav:getComputedStyle(document.querySelector('.android-nav')).visibility,
      seek:getComputedStyle(document.querySelector('#s'+current+' .reel-seek')).visibility,before:cleanBefore})`);
    assert.equal(cleanState.height,cleanState.before.height,'Clean view preserves Android navigation space');
    assert.equal(cleanState.index,cleanState.before.index);assert.equal(cleanState.same,true);
    assert.equal(cleanState.time,cleanState.before.time);assert.equal(cleanState.paused,cleanState.before.paused);
    assert.equal(cleanState.info,'hidden');assert.equal(cleanState.actions,'hidden');assert.equal(cleanState.nav,'hidden');assert.equal(cleanState.seek,'hidden');
    // Pinching and dragging two fingers must not become navigation or a tap.
    await touch('touchStart',[[140,350,1],[220,350,2]]);
    await touch('touchMove',[[90,300,1],[270,400,2]]);await touch('touchEnd',[]);
    assert.equal(await evaluate('cleanView'),true,'pinch does not toggle');
    await touch('touchStart',[[140,350,1],[220,350,2]]);
    await touch('touchMove',[[140,150,1],[220,150,2]]);await touch('touchEnd',[]);
    await sleep(350);
    assert.equal(await evaluate('current'),cleanState.index,'two-finger drag never scrolls');
    assert.equal(await evaluate('cleanVideo.paused'),cleanState.paused,'release clicks do not pause');
    await key('h');assert.equal(await evaluate('cleanView'),false,'H exits Clean view');
    await key('h');assert.equal(await evaluate('cleanView'),true,'H enables Clean view');
    await touch('touchStart',[[180,350]]);await sleep(400);
    assert.equal(await evaluate('cleanVideo.playbackRate'),2,'Clean view keeps hold-to-speed');
    assert.equal(await evaluate("getComputedStyle(document.querySelector('.hold-speed')).visibility"),'visible','speed feedback remains visible');
    await touch('touchStart',[[180,350,1],[240,350,2]]);await touch('touchEnd',[]);
    assert.equal(await evaluate('cleanVideo.playbackRate'),1,'second finger cancels hold');
    assert.equal(await evaluate('cleanView'),true,'a long hold plus second finger is not a two-finger tap');
    const bottom=await evaluate("document.querySelector('#s'+current).getBoundingClientRect().bottom-12");
    await evaluate("window.cleanSeekCalls=[];window.realCleanSeek=seekReel;seekReel=(i,r)=>cleanSeekCalls.push(Number(r.value)); document.querySelector('#s'+current+' .reel-seek input').disabled=false");
    await touch('touchStart',[[180,bottom]]);await touch('touchMove',[[280,bottom]]);await touch('touchEnd',[]);
    assert.ok(await evaluate('cleanSeekCalls.some(value=>value>650)'),'initial bottom gesture can scrub without a second touch');
    await evaluate('seekReel=realCleanSeek');
    assert.equal(await evaluate("getComputedStyle(document.querySelector('#s'+current+' .reel-seek')).visibility"),'visible','bottom touch reveals seeking');
    await sleep(2700);
    assert.equal(await evaluate("getComputedStyle(document.querySelector('#s'+current+' .reel-seek')).visibility"),'hidden','seek control hides again');
    await evaluate("revealCleanSeek(); document.querySelector('#s'+current+' .reel-seek input').disabled=false;document.querySelector('#s'+current+' .reel-seek input').focus()");
    await key('h');assert.equal(await evaluate('cleanView'),false,'H exits with seek input focused');
    await evaluate('document.activeElement.blur();setCleanView(true)');
    assert.equal(await evaluate('window.exitCleanView()'),true);assert.equal(await evaluate('window.exitCleanView()'),false);
    await evaluate('showReelActions(current)');
    await evaluate("[...document.querySelectorAll('#android-reel-actions button')].find(b=>b.textContent==='Clean view').click()");
    assert.equal(await evaluate('cleanView'),true,'More menu enables Clean view');
    await evaluate('setCleanView(false)');await sleep(100);
    await touch('touchStart',[[180,350,1]]);
    await touch('touchStart',[[180,350,1],[20,25,2]]);await touch('touchEnd',[]);
    assert.equal(await evaluate('cleanView'),false,'second finger outside video does not toggle');
    assert.equal(await evaluate('reelHold'),null,'second finger outside video cancels hold');
    await evaluate('cleanVideo.remove()');
    // A sideways swipe browses the current creator (Left/Right on a keyboard).
    const swipes = await evaluate(`(() => {
      const calls=[], real=browseCreator; browseCreator=d=>calls.push(d);
      const slide=document.getElementById('s'+current);
      const touch=(x,y)=>new Touch({identifier:1,target:slide,clientX:x,clientY:y});
      const swipe=(from,to)=>{
        feed.dispatchEvent(new TouchEvent('touchstart',{touches:[touch(from,400)],changedTouches:[touch(from,400)],bubbles:true}));
        feed.dispatchEvent(new TouchEvent('touchmove',{touches:[touch(to,405)],changedTouches:[touch(to,405)],bubbles:true,cancelable:true}));
        feed.dispatchEvent(new TouchEvent('touchend',{touches:[],changedTouches:[touch(to,405)],bubbles:true}));
      };
      swipe(300,100); swipe(100,300); swipe(200,180);
      browseCreator=real; return calls;
    })()`);
    assert.deepEqual(swipes,[1,-1],'swipe left shows more from the creator, right goes back, small moves do nothing');
    // @usernames in captions link to profiles; tapping one doesn't expand the caption.
    const mention = await evaluate(`(async () => {
      const caption = document.createElement('div'); caption.id = 'caption-test'; caption.className = 'cap';
      caption.innerHTML = mentions('funny one by @Some_Creator. ping a@b.com');
      caption.onclick = event => { if (!event.target.closest('a')) caption.classList.toggle('full'); };
      document.querySelector('#s' + current + ' .info').append(caption);
      const visited = [], real = routeLocalPage; routeLocalPage = url => { visited.push(url.pathname); return true; };
      const link = caption.querySelector('a.mention');
      link.click(); await new Promise(r => setTimeout(r, 200));  // the app saves pending changes first
      const result = {href: link.getAttribute('href'), text: link.textContent, links: caption.querySelectorAll('a').length,
        expandedByLink: caption.classList.contains('full')};
      caption.click(); result.expandedByText = caption.classList.contains('full');
      routeLocalPage = real; caption.remove();
      return {...result, visited};
    })()`);
    await sleep(100);
    assert.deepEqual(mention, {href: '/u/some_creator', text: '@Some_Creator', links: 1, expandedByLink: false,
      expandedByText: true, visited: ['/u/some_creator']}, 'caption mentions open profiles');
    assert.equal(await evaluate(`document.querySelector('.cap')?.tagName ?? 'DIV'`), 'DIV', 'captions are not buttons, so links inside work');
    // A caption selection (Copy / Select all menu) clears when you press elsewhere.
    const selecting = await evaluate(`(() => {
      const info = document.querySelector('#s' + current + ' .info');
      const text = document.createElement('span'); text.textContent = 'some caption words to copy'; info.append(text);
      const select = () => { const r = document.createRange(); r.selectNodeContents(text); const s = getSelection(); s.removeAllRanges(); s.addRange(r); };
      const press = (x, y) => document.elementFromPoint(x, y)?.dispatchEvent(new PointerEvent('pointerdown', {clientX: x, clientY: y, bubbles: true}));
      select(); const box = text.getBoundingClientRect();
      press(box.left + 4, box.top + box.height / 2);
      const keptOnText = !getSelection().isCollapsed;
      press(innerWidth / 2, 60);
      const clearedElsewhere = getSelection().isCollapsed;
      text.remove();
      return {keptOnText, clearedElsewhere};
    })()`);
    assert.deepEqual(selecting, {keptOnText: true, clearedElsewhere: true}, 'text selection menu closes on an outside press');
    // Smooth mode: the reel follows the finger and the next one slides in;
    // a long drag moves on, a short slow one springs back. Snappy: no follow.
    const dragging = await evaluate(`(async () => {
      const wait = ms => new Promise(r => setTimeout(r, ms));
      const fire = (type, y, slide) => {
        const t = new Touch({identifier: 7, target: slide, clientX: 200, clientY: y});
        feed.dispatchEvent(new TouchEvent(type, {touches: type === 'touchend' ? [] : [t], changedTouches: [t], bubbles: true, cancelable: true}));
      };
      const nextOf = i => visibleIndices()[visibleIndices().indexOf(i) + 1];
      const result = {}, previousMode = scrollAnimation;
      setScrollAnimation('smooth');
      showSlide(visibleIndices()[1]); await wait(400);
      let start = current, slide = document.getElementById('s' + start);
      fire('touchstart', 600, slide); fire('touchmove', 590, slide); fire('touchmove', 380, slide);
      await wait(50);
      result.follow = {current: current === start, offset: Math.round(slideOffset(start)), next: Math.round(slideOffset(nextOf(start)))};
      fire('touchend', 380, slide); await wait(450);
      result.commit = {moved: current === nextOf(start), aligned: Math.abs(slideOffset(current)) < 2};
      start = current; slide = document.getElementById('s' + start);
      fire('touchstart', 600, slide); fire('touchmove', 590, slide); await wait(150);
      fire('touchmove', 565, slide); await wait(150); fire('touchmove', 565, slide); await wait(150);
      fire('touchend', 565, slide); await wait(400);
      result.springBack = {stayed: current === start, aligned: Math.abs(slideOffset(start)) < 2};
      setScrollAnimation('snappy');
      fire('touchstart', 600, slide); fire('touchmove', 590, slide); fire('touchmove', 380, slide);
      await wait(50);
      result.snappy = {still: Math.abs(slideOffset(start)) < 2, cls: document.body.classList.contains('snappy-scroll')};
      fire('touchend', 380, slide); await wait(100);
      result.snappy.moved = current === nextOf(start);
      setScrollAnimation('smooth');
      result.smoothCls = document.body.classList.contains('snappy-scroll');
      setScrollAnimation(previousMode);
      return result;
    })()`);
    assert.equal(dragging.follow.current, true, 'dragging does not change the reel yet');
    assert.ok(dragging.follow.offset <= -150 && dragging.follow.offset >= -230, 'reel follows the finger: ' + JSON.stringify(dragging.follow));
    assert.ok(dragging.follow.next > 0 && dragging.follow.next < 844, 'the next reel slides into view: ' + JSON.stringify(dragging.follow));
    assert.deepEqual(dragging.commit, {moved: true, aligned: true}, 'a long drag moves to the next reel');
    assert.deepEqual(dragging.springBack, {stayed: true, aligned: true}, 'a short slow drag springs back');
    assert.deepEqual(dragging.snappy, {still: true, cls: true, moved: true}, 'Snappy keeps the reel still while dragging and moves on release');
    assert.equal(dragging.smoothCls, false);
    assert.equal(await evaluate(`(() => { setScrollAnimation('snappy'); const c = document.createElement('div'); c.className = 'cap full';
      document.querySelector('.info').append(c); const v = getComputedStyle(c).overscrollBehaviorY; c.remove(); setScrollAnimation('smooth'); return v; })()`),
      'contain', 'Snappy: an expanded caption does not drag the feed along');
    await evaluate("openFeedMenu()");
    await sleep(100);
    const picker = await evaluate(`(() => {
      const p=document.querySelector('.feed-panel').getBoundingClientRect();
      return {open:document.getElementById('feed-menu').open,left:p.left,right:p.right,bottom:p.bottom,
        hints:getComputedStyle(document.querySelector('.feed-key-hints')).display};
    })()`);
    assert.equal(picker.open,true,'top feed menu opens the selector');
    assert.equal(picker.left,0); assert.equal(picker.right,390); assert.equal(picker.bottom,844);
    assert.equal(picker.hints,'none','Android feed sheet omits keyboard instructions');
    fs.writeFileSync('/tmp/ig-android-feeds.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
    await evaluate("closeFeedMenu(); showReelActions(current)");
    assert.ok(await evaluate("document.querySelector('#android-reel-actions[open]') !== null"));
    const actions = await evaluate("Array.from(document.querySelectorAll('#android-reel-actions .sheet-actions button'),b=>b.textContent)");
    assert.ok(actions.includes('Remove from feeds'));
    assert.ok(!actions.includes('Add creator to a feed') && !actions.includes('View profile'),'rail actions are not repeated in More');
    assert.ok(actions.includes('More from this creator'));
    assert.ok(!actions.includes('More reels on this sound'), 'no sound entry without an audio id');
    const withSound = await evaluate(`(() => { document.getElementById('android-reel-actions').close();
      posts[current].audio_id = '561481164338977'; showReelActions(current);
      return Array.from(document.querySelectorAll('#android-reel-actions .sheet-actions button'), b => b.textContent); })()`);
    assert.ok(withSound.includes('More reels on this sound'), 'phones reach sound pages from the More menu');
    fs.writeFileSync('/tmp/ig-android-actions.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
    // Removing a creator from the rail or the More menu asks first.
    const removal = await evaluate(`(async () => {
      const calls=[], real=rmAcc; rmAcc=(u)=>calls.push(u);
      document.getElementById('android-reel-actions').close();
      const owner=posts[current].owner; feeded.add(owner); syncAccountButtons();
      const button=document.querySelector('#s'+current+' .account-action');
      button.click();
      const asked=!!document.querySelector('#remove-creator-confirm[open]');
      const focused=document.activeElement.textContent;
      document.querySelector('#remove-creator-confirm .confirm-actions button').click();
      await new Promise(r=>setTimeout(r,50));
      const afterCancel=[calls.length, !!document.getElementById('remove-creator-confirm')];
      showReelActions(current);
      [...document.querySelectorAll('#android-reel-actions .sheet-actions button')].find(b=>b.textContent==='Remove from feeds').click();
      await new Promise(r=>setTimeout(r,50));
      document.querySelector('#remove-creator-confirm .confirm-remove').click();
      rmAcc=real;
      return {asked, focused, afterCancel, calls, owner, label:button.textContent.trim()};
    })()`);
    assert.equal(removal.label,'in feed');
    assert.equal(removal.asked,true,'tapping "in feed" asks before removing');
    assert.equal(removal.focused,'Cancel','Cancel is the safe default');
    assert.deepEqual(removal.afterCancel,[0,false],'Cancel removes nothing');
    assert.deepEqual(removal.calls,[removal.owner],'confirming from the More menu removes once');
    await evaluate("openReelLink()");
    assert.ok(await evaluate("document.querySelector('#android-link-sheet[open]') !== null"));
    await evaluate("document.querySelector('#android-link-sheet input').value='https://example.com/reel/abcde'; document.querySelector('#android-link-sheet form').requestSubmit()");
    assert.ok(await evaluate("document.querySelector('#android-link-sheet .link-error').textContent.length>0"),'bad links show inline feedback');
    await evaluate("document.getElementById('android-link-sheet').close()");
    await send('Emulation.setDeviceMetricsOverride', {width:844,height:390,deviceScaleFactor:1,mobile:true});
    await evaluate('posts[current]._comments=[]; posts[current].comment_count=0; posts[current]._commentCursor=null; closeComments(); toggleComments(current)');
    await sleep(300);
    const landscape = await evaluate(`(() => {
      const panel=document.getElementById('comments-panel').getBoundingClientRect();
      return {width:panel.width,bottom:panel.bottom,feedWidth:feed.getBoundingClientRect().width};
    })()`);
    assert.equal(landscape.width,844,'Android comments remain a full-width sheet in landscape');
    assert.equal(landscape.bottom,390);
    assert.equal(landscape.feedWidth,844,'Android comments do not squeeze the reel into a desktop column');
    await evaluate(`(() => {
      const previousFetch = window.fetch;
      window.importWrites = [];
      window.importPreviewError = false;
      window.fetch = async (url, options) => {
        if (url === '/following/preview') {
          if (options.headers['X-File-Name'] !== 'following.json' || !(options.body instanceof File))
            throw new Error('Export must be sent as a raw file with its name');
          return new Response(JSON.stringify(importPreviewError ? {error:'No following accounts found'} :
            {accounts:['alpha','beta','blocked']}), {status:importPreviewError ? 400 : 200});
        }
        if (url === '/following/import') {
          const body = JSON.parse(options.body);
          importWrites.push(body);
          if (body.create) await previousFetch('/feeds', {method:'POST',body:JSON.stringify({...body,accounts:'alpha'})});
          return new Response(JSON.stringify({name:body.name.toLowerCase().replaceAll(' ','_'),
            created:body.create,accounts:['alpha'],added:1,existing:0,blocked:1}));
        }
        return previousFetch(url, options);
      };
      window.pickFollowingFile = () => {
        const input = document.querySelector('#feed-picker input[type=file]');
        const files = new DataTransfer();
        files.items.add(new File(['{}'], 'following.json', {type:'application/json'}));
        input.files = files.files;
        input.dispatchEvent(new Event('change'));
      };
    })()`);
    for (const android of [false,true]) {
      await send('Emulation.setDeviceMetricsOverride',{width:android?390:1000,height:844,deviceScaleFactor:1,mobile:android});
      await evaluate(`document.body.classList.toggle('android-app', ${android}); closeComments(); void chooseFeed()`);
      await sleep(100);
      await evaluate("document.querySelector('#feed-picker .fp-import-link').click()");
      await sleep(100);
      assert.equal(await evaluate("document.querySelector('#feed-picker button[type=submit]').disabled"),true,'import requires a preview');
      await evaluate('pickFollowingFile()');
      await sleep(100);
      assert.equal(await evaluate("document.querySelector('#feed-picker textarea').value"),'@alpha\n@beta\n@blocked');
      assert.equal(await evaluate('importWrites.length'),android?1:0,'preview makes no changes');
      const bounds=await evaluate(`(() => {const r=document.getElementById('feed-picker').getBoundingClientRect();
        return {left:r.left,right:r.right,bottom:r.bottom,top:r.top};})()`);
      assert.ok(bounds.top>=0 && bounds.bottom<=844 && bounds.left>=0 && bounds.right<=(android?390:1000),'import fits viewport');
      if (android) { assert.equal(bounds.bottom,844,'Android import is a bottom sheet');
        fs.writeFileSync('/tmp/ig-following-import-mobile.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64')); }
      else fs.writeFileSync('/tmp/ig-following-import-desktop.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
      await evaluate("document.getElementById('feed-picker').close()");
      await sleep(50);
      await evaluate('void importFollowing()'); await sleep(100);
      await evaluate('importPreviewError=true; pickFollowingFile()'); await sleep(100);
      assert.equal(await evaluate("document.querySelector('#feed-picker .fp-status').textContent"),'No following accounts found');
      assert.equal(await evaluate("document.querySelector('#feed-picker button[type=submit]').disabled"),true);
      await evaluate('importPreviewError=false; pickFollowingFile()'); await sleep(100);
      await evaluate(`(() => {
        const form=document.querySelector('#feed-picker form');
        form.elements.accounts.value='@alpha\\n@blocked'; form.elements.accounts.dispatchEvent(new Event('input'));
        form.elements.destination.value=${android ? "''" : "'edits'"}; form.elements.destination.dispatchEvent(new Event('change'));
        if (${android}) form.elements.feedname.value='Imported';
        form.requestSubmit();
      })()`);
      await sleep(250);
      const request=await evaluate('importWrites.at(-1)');
      assert.deepEqual(request.accounts,['alpha','blocked'],'edited selection is imported');
      assert.equal(request.name,android?'Imported':'edits'); assert.equal(request.create,android);
      assert.match(await evaluate("document.querySelector('#feed-picker .fp-status').textContent"),/1 added.*1 blocked skipped/);
      assert.equal(await evaluate("document.querySelector('#feed-picker button[type=submit]').hidden"),true,'completed import cannot submit twice');
      await evaluate("document.querySelector('#feed-picker .fp-actions button[type=button]').click()");
      await sleep(50);
    }
    await evaluate(`(() => {
      const previousFetch = window.fetch;
      window.backupMerges = 0; window.backupPreviewError = false; window.backupMergeError = false;
      window.fetch = async (url, options) => {
        if (url === '/feeds/import/preview') {
          if (!(options.body instanceof File)) throw new Error('Preview requires raw backup file');
          window.previewedBackup = options.body;
          return new Response(JSON.stringify(backupPreviewError ? {error:'Not an ig feed export'} :
            {feeds:2,names:['edits','imported'],accounts:3,blocked:1,
              ...(window.backupVersion2 ? {saved_reels:3,settings:['sound','auto_scroll']} : {})}),{status:backupPreviewError?400:200});
        }
        if (url === '/feeds/import') {
          if (options.body !== previewedBackup) throw new Error('Merge requires exactly the previewed file');
          if (backupMergeError) return new Response(JSON.stringify({error:'Could not merge. Try again.'}),{status:500});
          backupMerges++;
          return new Response(JSON.stringify({feeds:2,added:1,blocked:1,
            feed_accounts:['creator0','creator1','alpha'],blocked_accounts:['creator0'],
            ...(window.backupVersion2 ? {saved_reels_added:2,settings_applied:['sound','auto_scroll'],
              settings_values:{sound:false,auto_scroll:false}} : {})}));
        }
        return previousFetch(url,options);
      };
      window.pickBackupFile = (type = 'application/json') => {
        const input = document.querySelector('#feed-picker input[type=file]');
        const files = new DataTransfer();
        files.items.add(new File(['{}'],'ig-feeds.json',{type}));
        input.files=files.files; input.dispatchEvent(new Event('change'));
      };
    })()`);
    assert.equal(await evaluate("document.querySelector('#settings-link').getAttribute('href')"),'/settings');
    assert.equal(await evaluate("document.querySelector('.feed-tools').textContent.trim()"),'Open reel link');
    for (const android of [false,true]) {
      await send('Emulation.setDeviceMetricsOverride',{width:android?390:1000,height:844,deviceScaleFactor:1,mobile:android});
      await evaluate(`document.body.classList.toggle('android-app',${android}); window.backupVersion2=${android}; importFeeds()`);
      assert.equal(await evaluate("document.querySelector('#feed-picker input[type=file]').hasAttribute('accept')"),false,
        'export files remain selectable when a document provider reports a different MIME type');
      assert.equal(await evaluate("document.querySelector('#feed-picker button[type=submit]').disabled"),true);
      await evaluate('backupPreviewError=true; pickBackupFile()'); await sleep(100);
      assert.equal(await evaluate("document.querySelector('#feed-picker .fp-status').textContent"),'Not an ig feed export');
      assert.equal(await evaluate("document.querySelector('#feed-picker button[type=submit]').disabled"),true);
      await evaluate("backupPreviewError=false; pickBackupFile('application/x-json')"); await sleep(100);
      assert.match(await evaluate("document.querySelector('#feed-picker .fp-preview').textContent"),/2 feeds.*3 accounts.*1 permanent block/);
      assert.match(await evaluate("document.querySelector('#feed-picker .fp-preview').textContent"),android ? /3 saved reels/ : /0 saved reels/);
      assert.equal(await evaluate("document.querySelector('#feed-picker .fp-settings').hidden"),!android,'only supplied settings are replaced');
      if(android) assert.match(await evaluate("document.querySelector('#feed-picker .fp-settings').textContent"),/Replace current settings: sound, auto-scroll/);
      assert.equal(await evaluate('backupMerges'),android?1:0,'preview does not merge feeds');
      const bounds=await evaluate(`(() => {const r=document.getElementById('feed-picker').getBoundingClientRect();
        return {left:r.left,right:r.right,top:r.top,bottom:r.bottom};})()`);
      assert.ok(bounds.top>=0 && bounds.bottom<=844 && bounds.left>=0 && bounds.right<=(android?390:1000));
      if (android) {assert.equal(bounds.bottom,844); fs.writeFileSync('/tmp/ig-feed-backup-mobile.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));}
      await evaluate("document.querySelector('#feed-picker button[type=button]').click()"); await sleep(50);
      assert.equal(await evaluate('backupMerges'),android?1:0,'cancel does not merge feeds');
      await evaluate('importFeeds(); pickBackupFile()'); await sleep(100);
      await evaluate("backupMergeError=true; document.querySelector('#feed-picker form').requestSubmit()"); await sleep(100);
      assert.equal(await evaluate("document.querySelector('#feed-picker .fp-status').textContent"),'Could not merge. Try again.');
      assert.equal(await evaluate("document.querySelector('#feed-picker button[type=submit]').disabled"),false,'failed merge can be retried');
      await evaluate("backupMergeError=false; document.querySelector('#feed-picker form').requestSubmit()"); await sleep(250);
      assert.equal(await evaluate('backupMerges'),android?2:1);
      assert.equal(await evaluate("blockedAccounts.has('creator0') && feeded.has('creator0') && feeded.has('creator1')"),true,'blocked feed memberships stay blocked after merge');
      assert.equal(await evaluate("posts.filter(p=>p.owner==='creator0').every(isHidden)"),true,'newly blocked creators disappear immediately');
      assert.match(await evaluate("document.querySelector('#feed-picker .fp-status').textContent"),/2 feeds merged.*1 account added.*1 block added/);
      assert.match(await evaluate("document.querySelector('#feed-picker .fp-status').textContent"),android ? /2 saved reels added.*Settings applied/ : /0 saved reels added/);
      assert.equal(await evaluate("document.querySelector('#feed-picker button[type=submit]').hidden"),true);
      await evaluate("document.querySelector('#feed-picker button[type=button]').click()"); await sleep(50);
    }
    const motionIndices = await evaluate(`(() => {
      // Jump to the first reel instantly, then check one smooth move from there.
      const indices=visibleIndices(); setScrollAnimation('snappy'); showSlide(indices[0]); setScrollAnimation('smooth');
      goRel(1); return indices.slice(0,4);
    })()`);
    await sleep(80);
    assert.equal(await evaluate('current'),motionIndices[1],'smooth intermediate frames keep the selected reel');
    const sliding = await evaluate(`({offset:slideOffset(current), height:feed.clientHeight, snap:feed.style.scrollSnapType})`);
    assert.ok(sliding.offset > 2 && sliding.offset < sliding.height - 2,'smooth visibly moves between reels instead of jumping');
    assert.equal(sliding.snap,'none','native snapping cannot collapse the animation');
    await sleep(1000);
    assert.equal(await shown(),motionIndices[1],'smooth navigation lands on exactly one reel');
    assert.equal(await evaluate('feed.style.scrollSnapType'),'','native snap is restored after transition');
    await key('j',{repeats:4});
    await sleep(1200);
    assert.equal(await shown(),motionIndices[2],'held key does not skip multiple reels in smooth mode');
    assert.equal(await evaluate('current'),motionIndices[2]);
    // Vimium can consume J/K before the page receives any key event. Its
    // scrollBy steps must use the same smooth transition as native shortcuts.
    for (const [direction,target] of [[1,motionIndices[3]],[-1,motionIndices[2]]]) {
      const from=await evaluate('feed.scrollTop');
      const extension=extensionScroll(direction);
      await sleep(80);
      const progress=await evaluate(`({top:feed.scrollTop,height:feed.clientHeight,snap:feed.style.scrollSnapType})`);
      const moved=Math.abs(progress.top-from);
      assert.ok(moved>2 && moved<progress.height-2,
        'extension '+(direction>0?'J':'K')+' visibly slides in smooth mode: '+JSON.stringify({from,...progress}));
      assert.equal(progress.snap,'none','extension steps cannot trigger native instant snap');
      await extension;
      await sleep(700);
      assert.equal(await shown(),target,'one extension burst moves exactly one reel in smooth mode');
      assert.equal(await evaluate('current'),target);
      assert.equal(await evaluate('feed.style.scrollSnapType'),'','extension transition restores snapping');
    }
    await evaluate(`window.dispatchEvent(new StorageEvent('storage',{key:'ig-scroll-animation',newValue:'snappy'})); goRel(1)`);
    assert.equal(await evaluate('scrollAnimation'),'snappy');
    await expectReel(motionIndices[3],'snappy preference switches immediately');
    // Sound and auto-scroll survive leaving the page (e.g. for Settings) and coming back.
    assert.equal(await evaluate('muted'),true,'reels start muted by default');
    await key('m');
    await evaluate('if (!autoScroll) toggleAutoScroll(); playbackWrite');
    assert.deepEqual(await evaluate("fetch('/settings/data').then(r=>r.json()).then(d=>[d.sound,d.auto_scroll])"),[true,true]);
    await evaluate('localStorage.clear()');
    await send('Page.reload');
    for (let tries = 0; !(await evaluate(`typeof current === 'number' && current === 0`)); tries++) {
      assert.ok(tries < 50, 'reels page did not reload');
      await sleep(100);
    }
    await sleep(300);
    assert.equal(await evaluate('autoScroll'),true,'auto-scroll survives reload');
    // Headless Chrome may block sound before a gesture; then the first tap restores it.
    if (await evaluate('soundBlocked')) await send('Input.dispatchMouseEvent', {type:'mousePressed', x:5, y:400, button:'left', clickCount:1});
    assert.equal(await evaluate('muted'),false,'sound survives reload');
    assert.equal(await evaluate(`document.getElementById('mute').getAttribute('aria-pressed')`),'false');
    // Browser blocks sound before a gesture: keep playing muted, then restore sound on the first tap.
    assert.equal(await evaluate(`(async () => {
      let attempts = 0;
      const video = {muted:false, play() { attempts++; return attempts === 1 ? Promise.reject(new DOMException('blocked', 'NotAllowedError')) : Promise.resolve(); }};
      playVideo(video); await new Promise(r => setTimeout(r, 20));
      return JSON.stringify([attempts, muted, soundBlocked, document.getElementById('action-status').textContent]);
    })()`), JSON.stringify([2, true, true, 'Tap anywhere to turn sound on']), 'blocked sound falls back to muted playback');
    await send('Input.dispatchMouseEvent', {type:'mousePressed', x:5, y:400, button:'left', clickCount:1});
    await send('Input.dispatchMouseEvent', {type:'mouseReleased', x:5, y:400, button:'left', clickCount:1});
    assert.equal(await evaluate('muted'),false,'first tap restores the saved sound choice');
    assert.equal(await evaluate("fetch('/settings/data').then(r=>r.json()).then(d=>d.sound)"),true,'fallback does not overwrite the saved choice');
    await evaluate(`window.dispatchEvent(new StorageEvent('storage',{key:'ig-playback-change',newValue:JSON.stringify({changes:{sound:false}})}))`);
    assert.equal(await evaluate('muted'),true,'Settings in another tab can mute reels');
    // Immediately leaving after toggles and a debounced feed selection flushes
    // every preference before the next document reads SQLite-backed boot data.
    await evaluate(`toggleMute(); chosenFeeds=['memes']; renderFeedSelection();
      selectionTimer=setTimeout(saveFeedSelection,350); document.querySelector('a[href="/settings"]').click()`);
    for(let i=0;!(await evaluate("location.pathname==='/settings' && !!document.querySelector('#local-page iframe')?.contentDocument?.querySelector('#auto-scroll-on:not(:disabled)')"));i++){
      assert.ok(i<80,'Settings did not load');await sleep(50);
    }
    assert.equal(await evaluate("!!document.querySelector('#local-page iframe').contentDocument.getElementById('sound-on')"),false,'Settings has no separate sound toggle');
    assert.equal(await evaluate("document.querySelector('#local-page iframe').contentDocument.getElementById('auto-scroll-on').checked"),true);
    assert.deepEqual(await evaluate("fetch('/preferences').then(r=>r.json()).then(d=>d.selected_feeds)"),['memes'],'selection saved before Settings navigation');
    await evaluate("document.querySelector('#local-page iframe').contentDocument.querySelector('a[aria-label=\"Back to reels\"]').click()");
    for(let i=0;!(await evaluate("!document.getElementById('local-page') && typeof current==='number' && current===0"));i++){
      assert.ok(i<80,'Reels did not return');await sleep(50);
    }
    assert.equal(await evaluate('desiredSound'),true,'sound choice survives Settings round trip');
    assert.equal(await evaluate('muted'),false,'Settings does not reset effective sound on return');
    assert.equal(await evaluate('autoScroll'),true,'auto-scroll survives Settings round trip');
    assert.deepEqual(await evaluate('selectedFeeds'),['memes'],'feed selection survives Settings round trip');
    // Pages with their own header (Settings, Feeds, profiles) don't get a second back bar.
    await evaluate("showLocalPage(new URL('/settings/feeds', location.href))");
    for(let i=0;!(await evaluate("!!document.querySelector('#local-page iframe')?.contentDocument?.querySelector('#settings-feeds .feed-setting')"));i++){
      assert.ok(i<80,'Feeds page did not load');await sleep(50);
    }
    assert.equal(await evaluate("document.getElementById('local-page-back').hidden"),true,'one back control on the Feeds page');
    // Its back arrow leads to Settings, whose back arrow returns to the player.
    await evaluate("document.querySelector('#local-page iframe').contentDocument.querySelector('.page-header .back').click()");
    for(let i=0;!(await evaluate("location.pathname==='/settings' && !!document.querySelector('#local-page iframe')?.contentDocument?.querySelector('#auto-scroll-on')"));i++){
      assert.ok(i<80,'Feeds back arrow did not open Settings');await sleep(50);
    }
    await evaluate("document.querySelector('#local-page iframe').contentDocument.querySelector('.page-header .back').click()");
    for(let i=0;!(await evaluate("!document.getElementById('local-page')"));i++){
      assert.ok(i<80,'Settings back arrow did not return to reels');await sleep(50);
    }
    // Mount a real playable clip, then leave and return without replacing its
    // element, its playback position, its queue, or the comments drawer.
    await evaluate(`(async()=>{
      const canvas=document.createElement('canvas'); canvas.width=90;canvas.height=160;
      const context=canvas.getContext('2d'),stream=canvas.captureStream(10),chunks=[];
      const recorder=new MediaRecorder(stream,{mimeType:'video/webm'});
      recorder.ondataavailable=e=>chunks.push(e.data);
      const stopped=new Promise(resolve=>recorder.onstop=resolve); recorder.start();
      let frame=0;const timer=setInterval(()=>{context.fillStyle=frame++%2?'red':'blue';context.fillRect(0,0,90,160)},80);
      await new Promise(resolve=>setTimeout(resolve,1200));recorder.stop();await stopped;
      clearInterval(timer);stream.getTracks().forEach(track=>track.stop());
      const post=posts[current];post.video=URL.createObjectURL(new Blob(chunks,{type:'video/webm'}));post._rich=true;post._paused=false;
      setVideo(post,current,true);const video=document.querySelector('#s'+current+' video');
      if(video.readyState<1)await new Promise(resolve=>video.addEventListener('loadedmetadata',resolve,{once:true}));
      video.loop=true;video.currentTime=.25;await video.play();setCleanView(true);
      video.dispatchEvent(new PointerEvent('click',{bubbles:true,pointerType:'touch',clientX:150,clientY:400}));
      video.dispatchEvent(new PointerEvent('click',{bubbles:true,pointerType:'touch',clientX:150,clientY:400}));
      await new Promise(resolve=>setTimeout(resolve,350));
      if(video.paused || posts[current].feedback!=='like')throw new Error('Double tap must like without pausing');
      video.dispatchEvent(new PointerEvent('click',{bubbles:true,pointerType:'touch',clientX:150,clientY:400}));
      video.dispatchEvent(new PointerEvent('click',{bubbles:true,pointerType:'touch',clientX:150,clientY:400}));
      await new Promise(resolve=>setTimeout(resolve,350));
      if(posts[current].feedback!=='like')throw new Error('Double tap must not unlike');
      togglePlayback();
      if(document.getElementById('pause'+current).hidden)throw new Error('Paused icon missing');
      const seek=document.getElementById('seek'+current);
      updateSeekBar(current,video);
      if(seek.disabled)throw new Error('Seek bar unavailable after metadata');
      seek.value=750;seekReel(current,seek);
      if(Math.abs(video.currentTime-video.duration*.75)>.1||!video.paused)throw new Error('Paused seek forward failed');
      seek.value=250;seekReel(current,seek);
      if(Math.abs(video.currentTime-video.duration*.25)>.1||!video.paused)throw new Error('Paused seek backward failed');
      togglePlayback();await video.play();
      if(!document.getElementById('pause'+current).hidden)throw new Error('Pause icon remained while playing');
      post._comments=[];post._commentCursor=null;post.comment_count=0;toggleComments(current);
      window.navigationSnapshot={video,index:current,code:post.shortcode,posts:[...posts],time:video.currentTime,
        comments:document.getElementById('comments-panel').hidden};
      document.querySelector('a[href="/settings"]').click();
    })()`);
    for(let i=0;!(await evaluate("!!document.querySelector('#local-page iframe')?.contentDocument?.querySelector('#auto-scroll-on:not(:disabled)')"));i++){
      assert.ok(i<80,'Settings frame did not load');await sleep(50);
    }
    assert.equal(await evaluate('navigationSnapshot.video.paused'),true,'Settings pauses the underlying video');
    await evaluate('setTimeout(()=>setVideo(posts[current],current,true),20)');
    await sleep(80);
    assert.equal(await evaluate('navigationSnapshot.video.paused'),true,'a delayed media response cannot play under Settings');
    assert.equal(await evaluate('current===navigationSnapshot.index'),true);
    await evaluate("window.postMessage({type:'ig-local-navigation',href:location.origin+'/'},location.origin)");await sleep(50);
    assert.equal(await evaluate("!!document.getElementById('local-page')"),true,'untrusted message source cannot close the page');
    await evaluate("document.querySelector('#local-page iframe').contentDocument.querySelector('a[aria-label=\"Back to reels\"]').click()");
    for(let i=0;await evaluate("!!document.getElementById('local-page') || navigationSnapshot.video.paused");i++){
      assert.ok(i<80,'Player did not resume');await sleep(50);
    }
    assert.equal(await evaluate("document.querySelector('#s'+current+' video')===navigationSnapshot.video"),true,'same video element survives Settings');
    assert.equal(await evaluate('current===navigationSnapshot.index && posts[current].shortcode===navigationSnapshot.code'),true,'same reel survives Settings');
    assert.equal(await evaluate('navigationSnapshot.posts.every((post,index)=>posts[index]===post)'),true,'loaded queue is preserved');
    assert.equal(await evaluate("document.getElementById('comments-panel').hidden===navigationSnapshot.comments"),true,'comments stay open');
    assert.ok(await evaluate('Math.abs(navigationSnapshot.video.currentTime-navigationSnapshot.time)<.3'),'video resumes at the same position');
    assert.equal(await evaluate('navigationSnapshot.video.muted'),false,'actual playback resumes unmuted');
    await evaluate(`posts[current]._paused=true;navigationSnapshot.video.pause();document.querySelector('a[href="/settings"]').click()`);
    for(let i=0;!(await evaluate("!!document.querySelector('#local-page iframe')?.contentDocument?.querySelector('#auto-scroll-on:not(:disabled)')"));i++){
      assert.ok(i<80);await sleep(50);
    }
    await evaluate(`(() => {
      const child=document.querySelector('#local-page iframe').contentWindow;
      const original=child.fetch.bind(child);
      child.fetch=(url,options)=>url==='/settings/playback'?
        new Promise((resolve,reject)=>child.setTimeout(()=>original(url,options).then(resolve,reject),300)):original(url,options);
      child.document.getElementById('auto-scroll-on').click();
      history.back();
    })()`);
    await sleep(80);
    assert.equal(await evaluate("!!document.getElementById('local-page')"),true,'browser Back waits for pending Settings writes');
    for(let i=0;await evaluate("!!document.getElementById('local-page')");i++){assert.ok(i<80);await sleep(50);}
    await sleep(100);
    assert.equal(await evaluate('navigationSnapshot.video.paused && posts[current]._paused'),true,'Back preserves an explicit pause');
    assert.equal(await evaluate('autoScroll'),false,'immediate browser Back preserves the new auto-scroll choice');
    assert.equal(await evaluate("fetch('/settings/data').then(r=>r.json()).then(d=>d.auto_scroll)"),false);
    await evaluate("fetch('/settings/playback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({auto_scroll:true})})");
    await evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}))");
    for(let i=0;!(await evaluate('autoScroll'));i++){assert.ok(i<40,'cached page did not read playback preferences');await sleep(50);}
    // Search remains usable when account suggestions fail; status pages stay in
    // the mounted-player frame, including retry and return after an HTTP error.
    await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
    await evaluate(`window.statusSavedAccounts=[...feeded];feeded.clear();window.statusFetch=fetch;
      window.fetch=(path,options)=>path==='/settings/data'?Promise.resolve(new Response('{}',{status:503})):statusFetch(path,options);showSearch()`);
    for(let n=0;!(await evaluate(`document.querySelector('#reel-search [role=status]').textContent.includes('Couldn’t load')`));n++) {
      assert.ok(n<40,'search did not report unavailable suggestions');await sleep(50);
    }
    assert.equal(await evaluate(`document.querySelector('#reel-search').scrollWidth<=document.querySelector('#reel-search').clientWidth`),true,'search error fits phone width');
    assert.equal(await evaluate(`(() => {const dialog=document.getElementById('reel-search');
      const input=dialog.querySelector('input').getBoundingClientRect(), status=dialog.querySelector('[role=status]').getBoundingClientRect();
      return status.top>=input.bottom && status.top-input.bottom<40 && status.bottom<=dialog.querySelector('.search-results').getBoundingClientRect().top})()`),true,'Search error sits directly below input, above results');
    fs.writeFileSync('/tmp/ig-search-error-phone.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
    await evaluate(`window.fetch=statusFetch;statusSavedAccounts.forEach(name=>feeded.add(name));
      const field=document.querySelector('#reel-search input');field.value='retry_profile';field.dispatchEvent(new Event('input'));
      document.querySelector('#reel-search form').requestSubmit()`);
    for(let n=0;!(await evaluate(`!!document.querySelector('#local-page iframe')?.contentDocument?.getElementById('retry-page')`));n++) {
      assert.ok(n<60,'profile error page did not open');await sleep(50);
    }
    const statusLayout=await evaluate(`(() => {const d=document.querySelector('#local-page iframe').contentDocument;
      const buttons=[...d.querySelectorAll('.status-actions a')].map(a=>{const r=a.getBoundingClientRect();return {width:r.width,height:r.height,right:r.right,bottom:r.bottom}});
      return {overflow:d.documentElement.scrollWidth>d.documentElement.clientWidth,buttons,message:d.querySelector('.page-status').textContent}})()`);
    assert.equal(statusLayout.overflow,false,'profile error fits phone width');
    assert.ok(statusLayout.buttons.every(b=>b.height>=44&&b.right<=390&&b.bottom<=844),'status actions remain touch sized and visible');
    assert.ok(statusLayout.message.includes('Instagram is temporarily unavailable'));
    fs.writeFileSync('/tmp/ig-profile-error-phone.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
    await evaluate(`document.querySelector('#local-page iframe').contentDocument.getElementById('retry-page').click()`);
    for(let n=0;!(await evaluate(`!!document.querySelector('#local-page iframe')?.contentDocument?.querySelector('.card')`));n++) {
      assert.ok(n<60,'profile retry did not recover');await sleep(50);
    }
    assert.equal(await evaluate(`document.querySelector('#s'+current+' video')===navigationSnapshot.video`),true,'retry preserves underlying player');
    await evaluate('handleLocalBack()');
    for(let n=0;await evaluate(`!!document.getElementById('local-page')`);n++){assert.ok(n<60);await sleep(50);}
    await evaluate(`visitLocalPage('/u/missing_profile')`);
    for(let n=0;!(await evaluate(`!!document.querySelector('#local-page iframe')?.contentDocument?.querySelector('.page-status')`));n++){assert.ok(n<60);await sleep(50);}
    await evaluate(`document.querySelector('#local-page iframe').contentDocument.querySelector('.status-actions a[href="/"]').click()`);
    for(let n=0;await evaluate(`!!document.getElementById('local-page')`);n++){assert.ok(n<60);await sleep(50);}
    assert.equal(await evaluate(`document.querySelector('#s'+current+' video')===navigationSnapshot.video && current===navigationSnapshot.index && navigationSnapshot.video.paused`),true,'error-page Back retains same paused reel');
    // Notices hide by themselves unless they must stay (loading, waiting for a tap).
    await evaluate(`actionStatus('Saved', {hideAfterMs:50})`);
    await sleep(200);
    assert.equal(await evaluate(`document.getElementById('action-status').textContent`),'','short notice hides itself');
    await evaluate(`actionStatus('Loading…', {stay:true})`);
    await sleep(200);
    assert.equal(await evaluate(`document.getElementById('action-status').textContent`),'Loading…','staying notice remains');
    await evaluate(`actionStatus('Deleted x', {hideAfterMs:50}); const b=document.createElement('button'); b.textContent='Undo';
      document.getElementById('action-status').append(b); b.focus()`);
    await sleep(200);
    assert.equal(await evaluate(`document.getElementById('action-status').textContent`),'Deleted xUndo','notice stays while its Undo has focus');
    await evaluate(`document.activeElement.blur()`);
    await sleep(1200);
    assert.equal(await evaluate(`document.getElementById('action-status').textContent`),'','then hides once focus leaves');
    await send('Page.navigate',{url:new URL('/u/test_profile',url).href});
    for(let n=0;!(await evaluate(`typeof lb==='function' && document.querySelector('.card')`));n++) {
      assert.ok(n<50,'profile did not load');await sleep(100);
    }
    await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
    await send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:5});
    await evaluate('lb(0)');
    assert.equal(await evaluate(`document.querySelector('button#more')`),null,'profile has no Load more button');
    await touch('touchStart',[[180,500]]);await touch('touchMove',[[180,300]]);await touch('touchEnd',[]);
    assert.equal(await evaluate('cur'),1,'profile swipe up advances one post');
    await touch('touchStart',[[180,300]]);await touch('touchMove',[[180,500]]);await touch('touchEnd',[]);
    assert.equal(await evaluate('cur'),0,'profile swipe down goes back');
    assert.equal(await evaluate('lbEl.hidden'),false,'swipe down does not close profile viewer');
    await touch('touchStart',[[260,400]]);await touch('touchMove',[[80,400]]);await touch('touchEnd',[]);
    assert.equal(await evaluate('cur'),0,'sideways swipe does not change posts');
    await key('j');assert.equal(await evaluate('cur'),1,'J advances profile post');
    await key('k');assert.equal(await evaluate('cur'),0,'K goes back in profile');
    await evaluate('lb(99);step(1)');
    assert.equal(await evaluate('cur'),100,'profile viewer loads past the old end');
    assert.equal(await evaluate('store.length'),102,'profile pagination excludes duplicate posts');
    await send('Emulation.setDeviceMetricsOverride',{width:780,height:360,deviceScaleFactor:1,mobile:true});
    await sleep(100);
    const profileLandscape = await evaluate(`(() => {const media=document.getElementById('lbcontent').getBoundingClientRect();const details=document.querySelector('.lbmeta').getBoundingClientRect();return {height:media.height,width:media.width,details:details.width,right:details.right}})()`);
    assert.ok(profileLandscape.height>=350 && profileLandscape.width>=570,'landscape gives media the available height');
    assert.ok(profileLandscape.details<=200 && profileLandscape.right<=780,'landscape details fit beside the media');
    fs.writeFileSync('/tmp/ig-profile-landscape.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
    console.log('Reel navigation, profile vertical gestures, feeds, imports and playback passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
