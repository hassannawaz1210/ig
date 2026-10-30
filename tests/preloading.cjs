const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const elements = new Map();
const calls = [];
let response = {posts: [], has_more: true};
const timers = [];
function element(id) {
  if (id === 'feed-picker') return null;
  if (!elements.has(id)) elements.set(id, {
    textContent: id === 'seed' ? '[]' : '', style: {}, dataset: {},
    setAttribute(name, value) { this[name] = value; },
    querySelector: () => null, querySelectorAll: () => [],
    insertAdjacentHTML() {
      const video = {src: '', load() {this.loads = (this.loads || 0) + 1;},
        play() {this.paused = false; this.plays = (this.plays || 0) + 1; return Promise.resolve();},
        pause() {this.paused = true;}, removeAttribute() {this.src = null;}, remove() {},
        getAttribute() {return this.src;}};
      this.querySelector = () => video;
    },
  });
  return elements.get(id);
}
const ctx = vm.createContext({
  document: {getElementById: element, addEventListener() {}, querySelectorAll: () => []},
  IntersectionObserver: class {observe() {} unobserve() {}}, AbortSignal,
  sessionStorage: {data: {}, getItem(key) {return this.data[key] || null;},
    setItem(key, value) {this.data[key] = value;}},
  setTimeout(fn) {timers.push(fn); return timers.length;},
  fetch: async (url, options) => {
    calls.push({url, options});
    return {ok: true, json: async () => url.endsWith('/media') ? {video: 'https://example.test/video.mp4'} : response};
  },
});
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), ctx);
(async () => {
  vm.runInContext("posts.push({shortcode:'first'}, {shortcode:'second'}); slides=2; current=0;", ctx);
  await vm.runInContext('Promise.all([prepare(1), prepare(1)])', ctx);
  assert.equal(calls.filter(c => c.url.endsWith('/media')).length, 1, 'deduplicate in-flight media requests');
  const video = element('s1').querySelector('video');
  assert.equal(video.loads, 1, 'download video before it becomes visible');
  assert.equal(video.plays, undefined, 'preloaded video stays paused');
  await vm.runInContext('current=1; prepare(1)', ctx);
  assert.equal(video.loads, 1, 'activation does not restart the download');
  assert.equal(video.plays, 1);
  await vm.runInContext('more()', ctx);
  assert.equal(vm.runInContext('exhausted', ctx), false, 'temporary empty response is not exhaustion');
  assert.equal(timers.length, 1, 'retry without requiring another scroll');
  response = {posts: [], has_more: false};
  await vm.runInContext('more()', ctx);
  assert.equal(vm.runInContext('exhausted', ctx), true, 'honor actual exhaustion');
  vm.runInContext("posts[1].owner='creator';", ctx);
  vm.runInContext('togglePlayback()', ctx);
  assert.equal(video.paused, true, 'Space pauses current video');
  vm.runInContext('togglePlayback()', ctx);
  assert.equal(video.paused, false, 'Space resumes current video');
  vm.runInContext('toggleMute()', ctx);
  assert.equal(vm.runInContext('muted', ctx), false, 'mute toggles');
  assert.equal(element('mute')['aria-label'], 'Mute (M)');
  assert.ok(element('mute').innerHTML.includes('<svg'));
  assert.ok(!element('mute').innerHTML.includes('sound on'));
  const accountButtons = [0,1].map(i => {
    const slide = element('s'+i), original = slide.querySelector;
    const button = {style:{}, innerHTML:''};
    slide.querySelector = selector => selector === '.account-action' ? button : original(selector);
    return button;
  });
  vm.runInContext("posts[0].owner='creator'; accountChanged('creator', true);",ctx);
  assert.ok(accountButtons.every(button=>button.innerHTML.includes('in feed')), 'all reels update after save');
  response = {posts:[],has_more:false,feed_accounts:[]};
  await vm.runInContext('exhausted=false; more()',ctx);
  assert.ok(accountButtons.every(button=>button.innerHTML.includes('in feed')), 'old batches cannot undo a save');
  vm.runInContext(`
    let keyCalls = [];
    togglePlayback = () => keyCalls.push('play');
    toggleMute = () => keyCalls.push('mute');
    addCurrent = () => keyCalls.push('add');
    openProfile = () => keyCalls.push('profile');
    const realBlock = blockCurrent;
    blockCurrent = session => keyCalls.push(session ? 'session' : 'permanent');
  `, ctx);
  for (const [key, shiftKey] of [[' ',false],['m',false],['a',false],['p',false],['b',false],['B',true]]) {
    ctx.event = {key, shiftKey, target: {closest: () => null}, preventDefault() {this.prevented = true;}};
    vm.runInContext('handleReelKey(event)', ctx);
    assert.equal(ctx.event.prevented, true);
    vm.runInContext('releaseReelKey(event)', ctx);
  }
  assert.deepEqual(Array.from(vm.runInContext('keyCalls', ctx)), ['play','mute','add','profile','permanent','session']);
  ctx.event = {key:'m', code:'KeyM', target:{closest:()=>null}, preventDefault(){this.prevented=true;}};
  vm.runInContext('handleReelKey(event); handleReelKey(event)',ctx);
  assert.equal(vm.runInContext('keyCalls.length',ctx),7,'one action until physical key release');
  ctx.event.repeat = true; ctx.event.prevented = false;
  vm.runInContext('handleReelKey(event)',ctx);
  assert.equal(ctx.event.prevented,true,'repeated key must not trigger native browser scrolling');
  vm.runInContext('releaseReelKey(event); keyCalls.pop()',ctx);
  ctx.event = {key:'b', target:{closest:()=>({})}, preventDefault(){throw Error('typing intercepted');}};
  vm.runInContext('handleReelKey(event)',ctx);
  assert.equal(vm.runInContext('keyCalls.length',ctx),6);
  vm.runInContext(`
    blockCurrent = realBlock;
    more = () => {};
    maybeMore = () => {};
    showSlide = i => {current = i;};
  `,ctx);
  await vm.runInContext('blockCurrent(true)',ctx);
  assert.deepEqual(JSON.parse(ctx.sessionStorage.data['ig-session-blocked']), ['creator']);
  assert.equal(element('s1').hidden,true,'session block hides loaded clip');
  assert.equal(vm.runInContext("blockedAccounts.has('creator')",ctx),false,'session block is not permanent');
  assert.equal(calls.filter(c=>c.url === '/block').length,0,'session block does not write permanent list');
  vm.runInContext("current=0; posts[0].owner='another';",ctx);
  response = {blocked:['another']};
  await vm.runInContext('blockCurrent(false)',ctx);
  assert.equal(vm.runInContext("blockedAccounts.has('another')",ctx),true);
  assert.equal(element('s0').hidden,true,'permanent block hides loaded clip');
  assert.equal(calls.filter(c=>c.url === '/block').length,1);
  console.log('Reel preloading, keyboard shortcuts, and both block modes passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
