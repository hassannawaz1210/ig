// Changes made on one page reach the others: at once through the storage
// event, and on return for a page the browser froze (back/forward cache).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function page(storage) {
  const listeners = {};
  const target = {
    addEventListener: (type, fn) => (listeners[type] ||= []).push(fn),
    removeEventListener() {}, dispatchEvent: event => { for (const fn of listeners[event.type] || []) fn(event); return true; },
  };
  const element = () => ({style:{}, classList:{add(){}, remove(){}, toggle(){}, contains:()=>false}, setAttribute(){},
    addEventListener(){}, append(){}, querySelector:()=>null, querySelectorAll:()=>[]});
  const document = {...target, body: element(), documentElement: element(), createElement: element,
    getElementById: () => null, querySelector: () => null, querySelectorAll: () => [], hidden: false};
  const ctx = vm.createContext({console, setTimeout, clearTimeout, JSON, Date, Event: class { constructor(type) { this.type = type; } },
    CustomEvent: class { constructor(type, o) { this.type = type; this.detail = o?.detail; } },
    localStorage: storage, sessionStorage: storage, document, location: {origin: 'http://ig.test', href: 'http://ig.test/'},
    navigator: {userAgent: 'node'}, history: {state: null}, fetch: async () => ({ok: true, json: async () => ({})})});
  ctx.window = Object.assign(ctx, target, {parent: null});
  ctx.window.parent = ctx.window;
  vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), ctx);
  return {run: code => vm.runInContext(code, ctx), fire: (type, extra = {}) => target.dispatchEvent({type, ...extra})};
}

const store = new Map();
const storage = {getItem: k => store.has(k) ? store.get(k) : null, setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k)};
const profile = page(storage), player = page(storage);
profile.run(`window.seen = []; accountsChanged = (names, saved) => seen.push(['accounts', names.join(), saved]);
  reelChanged = (code, change) => seen.push(['reel', code, change.saved ?? change.feedback]); feedListsEdited = () => seen.push(['feeds']);`);

// Live: another page's change arrives through the storage event.
player.run(`notifyReelChange('abc', {saved: true})`);
profile.fire('storage', {key: 'ig-change-log', newValue: store.get('ig-change-log')});
assert.deepEqual(JSON.parse(JSON.stringify(profile.run('seen'))), [['reel', 'abc', true]]);

// Frozen: the profile is left (back/forward cache), a creator is added on
// another profile and a feed is edited; coming back replays both, once.
let tick = Date.now(); while (Date.now() === tick) {}
profile.fire('pagehide');
tick = Date.now(); while (Date.now() === tick) {}
player.run(`notifyAccountChange('friend', true); notifyFeedsChange()`);
profile.run('seen.length = 0');
profile.fire('pageshow', {persisted: true});
assert.deepEqual(JSON.parse(JSON.stringify(profile.run('seen'))), [['accounts', 'friend', true], ['feeds']],
  'a page restored by Back catches up with what changed while it was away');
profile.run('seen.length = 0');
profile.fire('pageshow', {persisted: false});
assert.deepEqual(JSON.parse(JSON.stringify(profile.run('seen'))), [], 'nothing replayed twice');

// Storage failures never break the page making the change.
const broken = page({getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); }, removeItem() {}});
const warn = console.warn; console.warn = () => {};
broken.run(`notifyReelChange('abc', {saved: false})`);
broken.fire('pagehide'); broken.fire('pageshow', {persisted: true});
console.warn = warn;
console.log('Page sync: live changes, replay after Back, storage failures passed');
