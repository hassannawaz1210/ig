// Feeds page topic tools against stubbed topic routes (test_feed_topics_ui.py).
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
    '--remote-debugging-port=0', '--user-data-dir=' + profile, '--window-size=390,844', 'about:blank'],
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
  const handlers = new Map();
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.method && handlers.has(message.method)) handlers.get(message.method)(message.params);
  });
  const evaluate = async expression => {
    const result = await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
    if (result.exceptionDetails) throw new Error('page error: ' + JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  return {send, evaluate, on: (method, handler) => handlers.set(method, handler), close: async () => { socket.close(); await close(); }};
}

(async () => {
  const browser = await launch();
  try {
    const {send, evaluate} = browser;
    const waitFor = async (expression, message = expression) => {
      for (let i = 0; !(await evaluate(expression)); i++) { assert.ok(i < 100, 'Timed out: ' + message); await sleep(50); }
    };
    const setState = state => evaluate(`fetch('/test/state',{method:'POST',body:${JSON.stringify(JSON.stringify(state))}}).then(r=>r.ok)`);
    const log = () => evaluate("fetch('/test/log').then(r=>r.json())");
    const fitsWidth = () => evaluate('document.documentElement.scrollWidth<=innerWidth');
    // Record every topic save the page sends.
    await send('Page.addScriptToEvaluateOnNewDocument', {source: `
      window.savedTopics = []; window.pageErrors = [];
      window.addEventListener('error', event => window.pageErrors.push(String(event.message)));
      window.alert = () => window.pageErrors.push('alert called');
      const originalFetch = window.fetch;
      window.fetch = (url, options) => {
        if (url === '/feeds/topics') window.savedTopics.push(JSON.parse(options.body));
        return originalFetch(url, options);
      };`});
    // Topic pictures come from Instagram's https CDN; serve one here.
    const svg = Buffer.from("<svg xmlns='http://www.w3.org/2000/svg' width='8' height='8'><rect width='8' height='8' fill='red'/></svg>").toString('base64');
    browser.on('Fetch.requestPaused', ({requestId}) => send('Fetch.fulfillRequest', {requestId, responseCode: 200,
      responseHeaders: [{name: 'Content-Type', value: 'image/svg+xml'}], body: svg}).catch(() => {}));
    await send('Fetch.enable', {patterns: [{urlPattern: 'https://example.invalid/*'}]});
    await send('Emulation.setDeviceMetricsOverride', {width: 390, height: 844, deviceScaleFactor: 1, mobile: true});
    await send('Page.enable');
    // A feed's topics are on its own page, under the Topics tab.
    const openTopics = async name => {
      await send('Page.navigate', {url: url + '/settings/feeds/' + name});
      await waitFor(`!!document.querySelector('[data-name=${name}]')`);
      await evaluate("document.querySelector('.feed-tabs input[value=topics]').click()");
    };
    await send('Page.navigate', {url: url + '/settings/feeds'});
    await waitFor("document.querySelectorAll('.feed-row').length===2");

    assert.equal(await evaluate("document.getElementById('trending')"), null, 'trending moved to its own page');
    assert.ok(await fitsWidth(), 'no sideways page scroll at 390px');

    // Suggestions load only when a feed is opened.
    assert.equal((await log()).filter(entry => entry.path === '/feeds/topics/suggest').length, 0, 'no suggestions before opening a feed');
    await openTopics('edits');
    await waitFor("document.querySelectorAll('[data-name=edits] .topic-skeleton').length>0", 'loading chips while suggestions load');
    await waitFor("document.querySelectorAll('[data-name=edits] .suggestions .topic-option').length===3", 'suggestions render');
    assert.deepEqual(await evaluate("[...document.querySelectorAll('[data-name=edits] .suggestions .topic-option')].map(b=>b.textContent)"),
      ['+Movie edits· 12.5M', '+Anime· 454M', '+Cars'], 'counts only when known');
    assert.deepEqual((await log()).filter(entry => entry.path === '/feeds/topics/suggest').map(entry => entry.feed), ['edits']);
    assert.ok(await fitsWidth(), 'an open feed fits at 390px');

    // Adding a suggestion saves its word and moves it into the feed's chips.
    await evaluate("[...document.querySelectorAll('[data-name=edits] .suggestions .topic-option')].find(b=>b.textContent.includes('Anime')).click()");
    await waitFor("savedTopics.length===1 && !document.querySelector('[data-name=edits] .topic-input').disabled");
    assert.deepEqual(await evaluate('savedTopics[0]'), {name: 'edits', topics: ['movie', 'anime']});
    assert.deepEqual(await evaluate("[...document.querySelectorAll('[data-name=edits] .topic-chip:not(.fixed)')].map(c=>c.firstChild.textContent)"), ['movie', 'anime']);
    assert.deepEqual(await evaluate("[...document.querySelectorAll('[data-name=edits] .suggestions .topic-option-name')].map(n=>n.textContent)"), ['Movie edits', 'Cars']);
    assert.ok(await evaluate("JSON.parse(localStorage.getItem('ig-change-log')).some(change=>change.kind==='feeds')"), 'other pages hear about the change');

    // Search: debounced, and an older answer arriving late is ignored.
    const type = text => evaluate(`(() => { const input=document.querySelector('[data-name=edits] .topic-input'); input.focus();
      input.value=${JSON.stringify(text)}; input.dispatchEvent(new Event('input')); })()`);
    await type('ed');
    await sleep(300);   // the "ed" search is sent and takes 700ms
    await type('edit');
    await waitFor("document.querySelectorAll('[data-name=edits] .search-results .topic-option').length===2", 'search results render');
    await sleep(800);   // long after the "ed" answer would have arrived
    assert.deepEqual(await evaluate("[...document.querySelectorAll('[data-name=edits] .search-results .topic-option')].map(b=>[b.textContent,b.disabled])"),
      [['+Edit· 454M', false], ['✓Movie· 3.4K', true]], 'newest results only; topics on the feed are marked');
    assert.deepEqual((await log()).filter(entry => entry.path === '/topics/search').map(entry => entry.q), ['ed', 'edit'], 'typing is debounced');
    await evaluate("document.querySelector('[data-name=edits] .search-results .topic-option').click()");
    await waitFor("savedTopics.length===2 && !document.querySelector('[data-name=edits] .topic-input').disabled");
    assert.deepEqual(await evaluate('savedTopics[1]'), {name: 'edits', topics: ['movie', 'anime', 'edit']});
    assert.equal(await evaluate("document.querySelector('[data-name=edits] .topic-input').value"), '', 'search clears after adding');
    assert.ok(await evaluate("!!document.querySelector('[data-name=edits] .suggestions')"), 'suggestions return');

    // A failed search says so and Enter still adds the typed word.
    await type('fail');
    await waitFor("document.querySelector('[data-name=edits] .topic-note')?.textContent.startsWith('Couldn’t search topics')", 'search failure note');
    assert.ok(await evaluate("!!document.querySelector('[data-name=edits] .topic-note button')"), 'search failure offers a retry');
    await evaluate("document.querySelector('[data-name=edits] .topic-input').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))");
    await waitFor("savedTopics.length===3 && !document.querySelector('[data-name=edits] .topic-input').disabled");
    assert.deepEqual(await evaluate('savedTopics[2]'), {name: 'edits', topics: ['movie', 'anime', 'edit', 'fail']});

    // Suggestions that fail to load show a quiet note with a retry.
    await setState({suggest: 'fail'});
    await openTopics('memes');
    await waitFor("document.querySelector('[data-name=memes] .topic-note')?.textContent.startsWith('Couldn’t load suggestions.')", 'suggestion failure note');
    assert.equal(await evaluate("document.querySelectorAll('[data-name=memes] .topic-skeleton').length"), 0, 'no endless loading');
    await setState({suggest: 'ok'});
    await evaluate("document.querySelector('[data-name=memes] .topic-note button').click()");
    await waitFor("document.querySelectorAll('[data-name=memes] .suggestions .topic-option').length===1", 'retry loads suggestions');

    // Trending page (bottom bar): cards per list, tabs to switch, escaped text.
    await send('Page.navigate', {url: url + '/trending'});
    await waitFor("document.querySelectorAll('#trending .trend-card:not(.placeholder)').length===3", 'trending cards render');
    const items = await evaluate(`[...document.querySelectorAll('#trending .trend-card')].map(b=>({
      name:b.querySelector('.trend-name').textContent, count:b.querySelector('.trend-count')?.textContent||null,
      image:b.querySelector('img')?.getAttribute('src')||null}))`);
    assert.deepEqual(items, [
      {name: 'Edit', count: '454M posts', image: 'https://example.invalid/edit.jpg'},
      {name: 'Gym', count: '1.2K posts', image: null},
      {name: '<img src=x onerror=alert(1)>', count: null, image: null},
    ], 'names and compact counts; only https images');
    assert.equal(await evaluate("document.querySelectorAll('#trending img').length"), 1, 'API text is not parsed as HTML');
    assert.deepEqual(await evaluate("[...document.querySelectorAll('.trending-tab')].map(t=>[t.textContent,t.getAttribute('aria-pressed')])"),
      [['Trending now', 'true'], ['Music', 'false']]);
    await evaluate("document.querySelectorAll('.trending-tab')[1].click()");
    assert.deepEqual(await evaluate("[...document.querySelectorAll('#trending .trend-name')].map(n=>n.textContent)"), ['Phonk']);
    await evaluate("document.querySelectorAll('.trending-tab')[0].click()");
    assert.ok(await fitsWidth(), 'the trending page fits at 390px');

    // Topic sheet: add to a feed (feeds that have it are disabled), failures, then watch.
    await evaluate("document.querySelector('#trending .trend-add').click()");
    await waitFor("document.querySelectorAll('#topic-sheet .topic-sheet-feed').length===2", 'feeds listed in the sheet');
    assert.equal(await evaluate("document.querySelector('#topic-sheet h2').textContent"), 'Edit');
    assert.deepEqual(await evaluate("[...document.querySelectorAll('#topic-sheet .topic-sheet-feed')].map(b=>[b.dataset.feed,b.disabled])"),
      [['edits', true], ['memes', false]]);
    assert.equal(await evaluate("Math.round(document.getElementById('topic-sheet').getBoundingClientRect().bottom)"), 844, 'a bottom sheet on phones');
    await evaluate("document.querySelector('#topic-sheet [data-feed=memes]').click()");
    await waitFor("savedTopics.length===1 && document.querySelector('#topic-sheet [data-feed=memes] .topic-sheet-state').textContent==='Added'");
    assert.deepEqual(await evaluate('savedTopics[0]'), {name: 'memes', topics: ['edit']});
    assert.ok(await evaluate("JSON.parse(localStorage.getItem('ig-change-log')).some(change=>change.kind==='feeds')"), 'other pages hear about it');
    await evaluate("document.getElementById('topic-sheet').close()");
    await evaluate("document.querySelectorAll('.trending-tab')[1].click(); document.querySelector('#trending .trend-add').click()");
    await waitFor("document.querySelector('#topic-sheet h2')?.textContent==='Phonk' && document.querySelectorAll('#topic-sheet .topic-sheet-feed').length===2");
    await evaluate(`window.realFetch=window.fetch; window.fetch=(u,o)=>u==='/feeds/topics'?Promise.resolve(new Response('{"error":"Could not save topics."}',{status:500})):realFetch(u,o);
      document.querySelector('#topic-sheet [data-feed=memes]').click()`);
    await waitFor("document.querySelector('#topic-sheet .topic-sheet-error').textContent==='Could not save topics.'");
    assert.equal(await evaluate("document.querySelector('#topic-sheet [data-feed=memes]').disabled"), false, 'a failed add can be retried');
    await evaluate("window.fetch=window.realFetch; document.getElementById('topic-sheet').close(); document.querySelectorAll('.trending-tab')[0].click()");
    await evaluate("document.querySelector('#trending .trend-card').click()");
    await waitFor("location.pathname==='/topic/edit' && document.readyState==='complete'", 'tapping a card opens its reels');

    // The Feeds page shows the topic added from Trending.
    await openTopics('memes');
    await waitFor("[...document.querySelectorAll('[data-name=memes] .topic-chip')].some(c=>c.firstChild.textContent==='edit')", 'feed card shows the new topic');

    // Trending that fails offers a retry; an empty answer says so.
    await setState({trending: 'fail'});
    await send('Page.navigate', {url: url + '/trending'});
    await waitFor("document.querySelector('#trending .trend-empty')?.textContent.startsWith('Couldn’t load trending topics.')", 'trending failure note');
    await setState({trending: 'ok'});
    await evaluate("document.querySelector('#trending .trend-empty button').click()");
    await waitFor("document.querySelectorAll('#trending .trend-card').length===3", 'retry loads trending');
    await setState({trending: 'empty'});
    await send('Page.reload');
    await waitFor("document.querySelector('#trending .trend-empty')?.textContent.includes('isn’t showing anything')", 'empty trending says so');
    await setState({trending: 'ok'});
    await openTopics('edits');

    // Layout on both widths, in the Android app too.
    for (const [width, android] of [[390, true], [390, false], [1000, false]]) {
      await send('Emulation.setDeviceMetricsOverride', {width, height: 844, deviceScaleFactor: 1, mobile: width < 500});
      await evaluate(`document.body.classList.toggle('android-app',${android}); window.scrollTo(0,0)`);
      await waitFor("document.querySelectorAll('[data-name=edits] .suggestions .topic-option').length>0");
      assert.ok(await fitsWidth(), `no sideways page scroll at ${width}px`);
      if (width === 390) {
        const small = await evaluate("[...document.querySelectorAll('.topic-option')].filter(b=>b.getBoundingClientRect().height<48).length");
        assert.equal(small, 0, '48px touch targets on phones');
      }
    }
    assert.deepEqual(await evaluate('pageErrors'), [], 'no page errors');
    console.log('Feed topics and Trending page: cards and sheet, lazy suggestions, search, failures and phone layout passed');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
