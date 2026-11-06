// Real Settings page and API in an isolated test profile.
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

(async () => {
  const browser=await launch();
  try {
    const {send,evaluate}=browser;
    const waitFor=async(expression)=>{
      for(let i=0;!(await evaluate(expression));i++) {assert.ok(i<60,'Timed out: '+expression);await sleep(50);}
    };
    await send('Page.enable'); await send('Page.navigate',{url});
    await waitFor("document.getElementById('feeds-total')?.textContent==='3 feeds · 162 accounts'");
    assert.equal(await evaluate("document.querySelector('#scroll-mode input:checked').value"),'snappy');
    assert.equal(await evaluate("document.getElementById('feeds-total').textContent"),'3 feeds · 162 accounts');
    await evaluate(`(() => {
      const original=window.fetch;
      window.fetch=(url,options)=>url==='/settings/scroll'?
        new Promise((resolve,reject)=>window.releaseScrollWrite=()=>original(url,options).then(resolve,reject)):original(url,options);
      document.querySelector('input[value=smooth]').click();
      window.scrollFlushDone=false;
      window.flushLocalChanges().then(()=>window.scrollFlushDone=true);
      window.fetch=original;
    })()`);
    await sleep(80);
    assert.equal(await evaluate('scrollFlushDone'),false,'navigation waits for the pending transition preference');
    assert.equal(await evaluate("fetch('/settings/data').then(r=>r.json()).then(d=>d.scroll_animation)"),'snappy');
    await evaluate('releaseScrollWrite()');
    await waitFor("document.getElementById('settings-status').textContent==='Smooth scrolling enabled.'");
    await waitFor('scrollFlushDone');
    assert.ok(await evaluate('statusHider.pending()'),'status message is scheduled to hide');
    await sleep(5300);
    assert.equal(await evaluate("document.getElementById('settings-status').textContent"),'','status hides after a few seconds');
    assert.equal(await evaluate("getComputedStyle(document.getElementById('settings-status')).display"),'none','empty status takes no space');
    await send('Page.reload'); await waitFor("document.getElementById('feeds-total')?.textContent==='3 feeds · 162 accounts'");
    assert.equal(await evaluate("document.querySelector('#scroll-mode input:checked').value"),'smooth','scroll preference survives reload');
    assert.equal(await evaluate("document.getElementById('sound-on')"),null,'sound stays on the existing reel control');
    await evaluate("localStorage.setItem('ig-auto-scroll','true')");
    await send('Page.reload'); await waitFor("document.getElementById('feeds-total')?.textContent==='3 feeds · 162 accounts'");
    await evaluate('playbackWrite');
    assert.equal(await evaluate("document.getElementById('auto-scroll-on').checked"),true,'legacy choice migrated to SQLite');
    await evaluate("localStorage.clear(); document.getElementById('auto-scroll-on').click()");
    await waitFor("document.getElementById('settings-status').textContent==='Auto-scroll turned off.'");
    await evaluate("Storage.prototype.setItem=()=>{throw new Error('blocked')}; document.getElementById('auto-scroll-on').click()");
    await waitFor("document.getElementById('settings-status').textContent==='Auto-scroll turned on.'");
    await send('Page.reload'); await waitFor("document.getElementById('feeds-total')?.textContent==='3 feeds · 162 accounts'");
    assert.equal(await evaluate("document.getElementById('auto-scroll-on').checked"),true,'SQLite works when browser storage is blocked');
    await evaluate(`window.originalFetch=window.fetch; window.fetch=(url,options)=>url==='/settings/playback'?
      Promise.resolve(new Response(JSON.stringify({error:'Could not save playback preferences'}),{status:500})):originalFetch(url,options);
      document.getElementById('auto-scroll-on').click()`);
    await waitFor("document.getElementById('settings-status').textContent==='Could not save playback preferences'");
    assert.equal(await evaluate("document.getElementById('auto-scroll-on').checked"),true,'failed server save reverts switch');
    await evaluate('window.fetch=originalFetch');
    await evaluate("document.querySelector('input[value=snappy]').click()");
    await waitFor("document.getElementById('settings-status').textContent==='Snappy scrolling enabled.'");
    // Settings only links to the feed list; feeds are managed on their own page.
    assert.equal(await evaluate("document.querySelectorAll('.feed-setting').length"),0,'Settings does not list every feed');
    assert.equal(await evaluate("document.querySelector('a[href=\"/feeds/export\"]').textContent.includes('Export backup')"),true);
    await evaluate("document.querySelector('a[href=\"/settings/feeds\"]').click()");
    await waitFor("location.pathname==='/settings/feeds' && document.querySelectorAll('.feed-setting').length===3");
    assert.equal(await evaluate("document.getElementById('feeds-total').textContent"),'3 feeds · 162 accounts');
    assert.equal(await evaluate("document.querySelectorAll('.feed-setting[open]').length"),0,'feeds start collapsed');
    // Finding a feed by name or by one of its accounts.
    const search=async q=>evaluate(`(() => { const s=document.getElementById('feed-search'); s.value=${JSON.stringify(q)};
      s.dispatchEvent(new Event('input')); return [...document.querySelectorAll('.feed-setting:not([hidden])')].map(f=>f.dataset.name); })()`);
    assert.deepEqual(await search('mem'),['memes']);
    assert.deepEqual((await search('@shared')).sort(),['edits','memes']);
    assert.deepEqual(await search('zzz'),[]);
    assert.ok(await evaluate("!!document.querySelector('.no-match')"),'no matching feeds says so');
    await search('');
    assert.equal(await evaluate("document.querySelectorAll('.feed-setting:not([hidden])').length"),3);
    await evaluate("document.querySelector('[data-name=edits] summary').click()");
    await waitFor("document.querySelectorAll('[data-name=edits] .account-row').length===2");
    await evaluate("document.querySelector('[data-name=edits] button[aria-label=\"Remove @shared from edits\"]').click()");
    assert.equal(await evaluate("document.querySelectorAll('[data-name=edits] .account-row').length"),2,'remove waits for confirmation');
    await evaluate("document.querySelector('[data-name=edits] .confirmation button').click()");
    assert.equal(await evaluate("document.querySelectorAll('[data-name=edits] .account-row').length"),2,'cancel preserves membership');
    await evaluate("document.querySelector('[data-name=edits] button[aria-label=\"Remove @shared from edits\"]').click(); document.querySelector('[data-name=edits] .confirm-remove').click()");
    await waitFor("document.querySelectorAll('[data-name=edits] .account-row').length===1");
    const data=await evaluate("fetch('/settings/data').then(r=>r.json())");
    assert.deepEqual(data.feeds.edits,['alpha']);assert.deepEqual(data.feeds.memes,['shared']);
    await evaluate("document.querySelector('[data-name=edits] .feed-tools button').click()");
    await waitFor("!!document.querySelector('#feed-picker select:not(:disabled)')");
    assert.equal(await evaluate("document.querySelector('#feed-picker select').value"),'edits','Add chooses the requested feed');
    await evaluate("document.querySelector('#feed-picker textarea').value='@beta'; document.querySelector('#feed-picker form').requestSubmit()");
    await waitFor("!document.getElementById('feed-picker') && document.querySelectorAll('[data-name=edits] .account-row').length===2");
    await evaluate("document.querySelector('[data-name=edits] .delete-feed').click()");
    assert.ok(await evaluate("!!document.querySelector('[data-name=edits] .confirmation')"));
    await evaluate("document.querySelector('[data-name=edits] .confirmation button').click()");
    assert.ok(await evaluate("!!document.querySelector('[data-name=edits]')"),'cancel preserves feed');
    await evaluate("document.querySelector('[data-name=edits] .delete-feed').click(); document.querySelector('[data-name=edits] .confirm-remove').click()");
    await waitFor("!document.querySelector('[data-name=edits]')");
    await evaluate("document.querySelector('#settings-status button').click()");
    await waitFor("!!document.querySelector('[data-name=edits]')");
    await evaluate("document.getElementById('create-feed').click()");
    await waitFor("!!document.querySelector('#feed-picker select:not(:disabled)')");
    await evaluate("document.querySelector('#feed-picker input[name=feedname]').value='New feed'; document.querySelector('#feed-picker form').requestSubmit()");
    await waitFor("!!document.querySelector('[data-name=new_feed]')");
    await evaluate("document.querySelector('[data-name=large] summary').click()");
    await waitFor("document.querySelectorAll('[data-name=large] .account-row').length===100");
    await evaluate("document.querySelector('[data-name=large] .load-accounts').click()");
    assert.equal(await evaluate("document.querySelectorAll('[data-name=large] .account-row').length"),160);
    await evaluate("document.querySelector('[data-name=large] .account-search').value='creator159'; document.querySelector('[data-name=large] .account-search').dispatchEvent(new Event('input'))");
    assert.equal(await evaluate("document.querySelectorAll('[data-name=large] .account-row').length"),1);
    assert.equal(await evaluate("document.querySelector('[data-name=large] .account-row a').textContent"),'@creator159');
    for(const android of [false,true]) {
      await send('Emulation.setDeviceMetricsOverride',{width:android?390:1000,height:844,deviceScaleFactor:1,mobile:android});
      await evaluate(`document.body.classList.toggle('android-app',${android}); window.scrollTo(0,0)`);
      assert.ok(await evaluate('document.documentElement.scrollWidth<=innerWidth'),'Feeds page fits without horizontal scrolling');
      fs.writeFileSync('/tmp/ig-feeds-'+(android?'mobile':'desktop')+'.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
      await evaluate('void importFollowing()');await waitFor("document.querySelector('#feed-picker h2')?.textContent==='Import following'");
      assert.equal(await evaluate("document.querySelector('#feed-picker h2').textContent"),'Import following');
      if(android)assert.equal(await evaluate("document.getElementById('feed-picker').getBoundingClientRect().bottom"),844);
      await evaluate("document.getElementById('feed-picker').close()");await sleep(50);
      await evaluate('importFeeds()');
      assert.equal(await evaluate("document.querySelector('#feed-picker h2').textContent"),'Import backup');
      await evaluate("document.getElementById('feed-picker').close()");await sleep(50);
    }
    await evaluate("history.back()");
    await waitFor("location.pathname==='/settings' && document.getElementById('feeds-total')?.textContent==='4 feeds · 163 accounts'");
    console.log('Settings: playback persistence, membership removal, confirmations, add/create/delete/undo, large feeds, imports and mobile layout passed');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
