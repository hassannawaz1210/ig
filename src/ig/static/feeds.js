// Settings → Feeds: every feed with its accounts, searchable.
const boot = JSON.parse(document.getElementById('boot').textContent);
if (boot.android) document.body.classList.add('android-app');
let chosenFeeds = null;  // read by chooseFeed() to preselect a feed
let loadVersion = 0, feedsLoaded = false, feedsData = {}, feedTopics = {};
const status = document.getElementById('settings-status');
const statusHider = autoHideStatus(status);
const container = document.getElementById('settings-feeds');
const feedSearch = document.getElementById('feed-search');
const displayName = name => name.replaceAll('_', ' ');
function confirmSettingsAction(host, message, label, action) {
  host.querySelector('.confirmation')?.remove();
  const confirmation = element('div','confirmation');
  confirmation.setAttribute('role','group'); confirmation.setAttribute('aria-label',message);
  const cancel = element('button','', 'Cancel'); cancel.type='button';
  const confirm = element('button','confirm-remove',label); confirm.type='button';
  cancel.onclick = () => confirmation.remove();
  confirm.onclick = async () => {
    cancel.disabled = confirm.disabled = true;
    try { await action(); }
    catch (error) {status.textContent=error.message; cancel.disabled=confirm.disabled=false;}
  };
  confirmation.append(element('p','',message),cancel,confirm); host.append(confirmation); cancel.focus();
}
// ---- Topics from Instagram: suggestions and search (helpers in topics.js) ----
// A topic from the server: {name, slug, word, count_label, image, reason?}.
// Feeds store the topic's `word`.
const SEARCH_DELAY_MS = 200;
const topicEditors = new Map();      // feed name → its FeedTopicEditor
const suggestionCache = new Map();   // feed name → suggestions loaded this visit
const SUGGESTION_REASONS = {related:'Related to this feed', hashtag:'Popular hashtag', trending:'Trending now'};
function feedHasTopic(name, word) {
  word = normalizeTopicWord(word);
  return word === normalizeTopicWord(displayName(name)) || (feedTopics[name] || []).includes(word);
}
// "+ Edit · 454M": a topic that can be added; "✓ Edit · 454M" when the feed has it.
function topicOption(topic, {onFeed, disabled, onAdd}) {
  const button = element('button', 'topic-option' + (onFeed ? ' on-feed' : ''));
  button.type = 'button'; button.disabled = onFeed || disabled;
  const mark = element('span', 'topic-option-mark', onFeed ? '✓' : '+'); mark.setAttribute('aria-hidden', 'true');
  button.append(mark, element('span', 'topic-option-name', topic.name));
  if (topic.count) button.append(element('span', 'topic-option-count', '· ' + topic.count));
  const count = topic.countLabel ? `, ${topic.countLabel}` : '';
  button.setAttribute('aria-label', onFeed ? `${topic.name} is already in this feed` : `Add topic ${topic.name}${count}`);
  if (SUGGESTION_REASONS[topic.reason]) button.title = SUGGESTION_REASONS[topic.reason];
  // Keep focus in the topic box so the typed text is not added on blur.
  button.addEventListener('pointerdown', event => event.preventDefault());
  button.onclick = () => onAdd(topic);
  return button;
}
function skeletonChips(count, className) {
  const row = element('div', className);
  row.setAttribute('aria-hidden', 'true');
  for (let i = 0; i < count; i++) {
    const chip = element('span', 'skeleton topic-skeleton');
    chip.style.width = `${70 + (i * 37) % 60}px`;
    row.append(chip);
  }
  return row;
}

// Discovery topics: the feed name, plus extra words. ig looks up each one on
// Instagram's public topic pages to find creators outside your feeds. The box
// also suggests topics (loaded when the feed is opened) and searches
// Instagram's topics as you type.
class FeedTopicEditor {
  constructor(name, topics) {
    this.name = name;
    this.current = [...topics];
    this.saving = false;
    this.suggestions = {status:'idle', items:[], version:0};
    this.search = {query:'', status:'idle', items:[], version:0, controller:null, timer:null};
    this.box = element('div', 'feed-topics');
    const heading = element('div', 'feed-topics-heading');
    heading.append(element('span', '', 'Discovery topics'),
      element('small', '', 'New creators come from Instagram’s popular reels for these topics.'));
    this.chips = element('div', 'topic-chips');
    const input = this.input = element('input', 'topic-input'); input.type = 'text'; input.maxLength = 40;
    input.placeholder = 'Add or search topics'; input.autocomplete = 'off'; input.spellcheck = false;
    input.setAttribute('aria-label', `Add or search discovery topics for ${displayName(name)}`);
    this.options = element('div', 'topic-extra');
    this.options.setAttribute('aria-live', 'polite');
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ',') { event.preventDefault(); this.addTyped(); }
      else if (event.key === 'Escape' && input.value) { event.preventDefault(); input.value = ''; this.queryChanged(); }
      else if (event.key === 'Backspace' && !input.value && this.current.length) this.save(this.current.slice(0, -1));
    });
    input.addEventListener('input', () => this.queryChanged());
    input.addEventListener('blur', event => {
      // Typed text is kept as a topic, unless focus moved to a topic button.
      if (this.saving || !input.value.trim() || this.box.contains(event.relatedTarget)) return;
      this.addTyped({focusInput:false});
    });
    this.render();
    this.box.append(heading, this.chips, this.options);
  }
  has(word) {
    word = normalizeTopicWord(word);
    return word === normalizeTopicWord(displayName(this.name)) || this.current.includes(word);
  }
  render() {
    this.chips.replaceChildren();
    const fixed = element('span', 'topic-chip fixed', displayName(this.name)); fixed.title = 'From the feed name';
    this.chips.append(fixed);
    for (const word of this.current) {
      const chip = element('span', 'topic-chip', word);
      const remove = element('button', 'topic-remove', '×'); remove.type = 'button';
      remove.setAttribute('aria-label', `Remove topic ${word}`);
      remove.disabled = this.saving;
      remove.onclick = () => this.save(this.current.filter(item => item !== word));
      chip.append(remove); this.chips.append(chip);
    }
    this.chips.append(this.input);
    this.renderOptions();
  }
  // Search results while there is a query, otherwise suggestions.
  renderOptions() {
    const box = this.options;
    box.replaceChildren();
    const add = topic => this.addTopic(topic);
    const query = this.searchQuery();
    if (query) {
      const {status: state, items} = this.search;
      box.setAttribute('aria-busy', String(state === 'loading'));
      if (state === 'failed') {
        box.append(noteWithRetry('topic-note', `Couldn’t search topics. Press Enter to add “${query}” as typed.`, () => this.runSearch(query)));
      } else if (state === 'loading' && !items.length) {
        box.append(element('span', 'sr-only', 'Searching topics…'), skeletonChips(3, 'topic-options'));
      } else if (state === 'ready' && !items.length) {
        box.append(element('p', 'topic-note', `No matching topics. Press Enter to add “${query}”.`));
      } else if (items.length) {
        box.append(element('span', 'topic-extra-label', 'Matching topics'));
        const row = element('div', 'topic-options search-results');
        for (const topic of items) row.append(topicOption(topic, {onFeed:this.has(topic.word), disabled:this.saving, onAdd:add}));
        box.append(row);
      }
      return;
    }
    const {status: state, items} = this.suggestions;
    box.setAttribute('aria-busy', String(state === 'loading'));
    if (state === 'loading') {
      box.append(element('span', 'topic-extra-label', 'Suggested'), element('span', 'sr-only', 'Loading suggestions…'),
        skeletonChips(4, 'topic-options'));
    } else if (state === 'failed') {
      box.append(noteWithRetry('topic-note', 'Couldn’t load suggestions.', () => this.loadSuggestions()));
    } else if (state === 'ready') {
      const open = items.filter(topic => !this.has(topic.word));
      if (!open.length) { box.append(element('p', 'topic-note', items.length ? 'No more suggestions right now.' : 'No suggestions right now.')); return; }
      box.append(element('span', 'topic-extra-label', 'Suggested'));
      const row = element('div', 'topic-options suggestions');
      for (const topic of open) row.append(topicOption(topic, {onFeed:false, disabled:this.saving, onAdd:add}));
      box.append(row);
    }
  }
  // The feed card was opened: show suggestions, loading them the first time.
  opened() {
    if (this.suggestions.status !== 'idle') return;
    const cached = suggestionCache.get(this.name);
    if (cached) { this.suggestions = {...this.suggestions, status:'ready', items:cached}; this.renderOptions(); }
    else this.loadSuggestions();
  }
  async loadSuggestions() {
    const version = ++this.suggestions.version;
    this.suggestions = {status:'loading', items:[], version};
    this.renderOptions();
    try {
      const data = await loadTopicJSON('/feeds/topics/suggest?feed=' + encodeURIComponent(this.name));
      if (version !== this.suggestions.version) return;
      const items = cleanTopics(data.suggestions, 12);
      suggestionCache.set(this.name, items);
      this.suggestions = {status:'ready', items, version};
    } catch (error) {
      if (version !== this.suggestions.version) return;
      console.warn(`Could not load topic suggestions for ${this.name}`, error);
      this.suggestions = {status:'failed', items:[], version};
    }
    this.renderOptions();
  }
  searchQuery() {
    const text = this.input.value.trim().replace(/\s+/g, ' ');
    return text.length >= 2 && !text.includes(',') ? text : '';
  }
  queryChanged() {
    const search = this.search;
    clearTimeout(search.timer); search.timer = null;
    const query = this.searchQuery();
    if (query === search.query && search.status !== 'idle') return;
    search.controller?.abort(); search.controller = null;
    search.version++;   // any answer still on its way is now stale
    search.query = query;
    if (!query) { search.status = 'idle'; search.items = []; }
    else {
      search.status = 'loading';
      search.timer = setTimeout(() => this.runSearch(query), SEARCH_DELAY_MS);
    }
    this.renderOptions();
  }
  async runSearch(query) {
    const search = this.search;
    clearTimeout(search.timer); search.timer = null;
    search.controller?.abort();
    const version = ++search.version;
    const controller = search.controller = new AbortController();
    search.status = 'loading'; this.renderOptions();
    try {
      const data = await loadTopicJSON('/topics/search?q=' + encodeURIComponent(query), controller.signal);
      if (version !== search.version) return;
      search.items = cleanTopics(data.topics, 10); search.status = 'ready';
    } catch (error) {
      if (version !== search.version) return;
      console.warn(`Could not search topics for “${query}”`, error);
      search.items = []; search.status = 'failed';
    }
    search.controller = null;
    this.renderOptions();
  }
  clearSearch() {
    this.input.value = '';
    this.queryChanged();
  }
  addTyped({focusInput = true} = {}) {
    const words = this.input.value.split(',').map(normalizeTopicWord).filter(Boolean);
    this.clearSearch();
    const next = [...new Set([...this.current, ...words])];
    if (next.length !== this.current.length) this.save(next, {focusInput});
  }
  addTopic(topic) {
    const focusInput = document.activeElement === this.input;
    this.clearSearch();
    return this.addWord(topic.word, {focusInput});
  }
  // Add one topic word. Resolves to {ok, message?}.
  async addWord(word, options = {}) {
    word = normalizeTopicWord(word);
    if (this.has(word)) return {ok:true};
    return this.save([...this.current, word], options);
  }
  // Save the feed's topic words. Errors go to the page status unless
  // report is false (the caller shows them). Resolves to {ok, message?}.
  async save(next, {focusInput = true, report = true} = {}) {
    if (this.saving) return {ok:false, message:'Still saving the last change. Try again in a moment.'};
    this.saving = true; this.input.disabled = true;
    const previous = this.current; this.current = next; this.render();
    let result;
    try {
      const data = await settingsRequest('/feeds/topics', {name:this.name, topics:next});
      if (!Array.isArray(data.topics)) throw new Error('Unexpected reply from ig. Try again.');
      this.current = data.topics; feedTopics[this.name] = data.topics;
      notifyFeedsChange();
      result = {ok:true};
    } catch (error) {
      this.current = previous;
      if (report) status.textContent = error.message;
      result = {ok:false, message:error.message};
    } finally {
      this.saving = false; this.input.disabled = false; this.render();
      if (focusInput) this.input.focus();
    }
    return result;
  }
}

function renderFeed(name, accounts, open, topics = []) {
  const details = element('details','feed-setting'); details.dataset.name=name; details.open=open;
  const summary = element('summary');
  summary.append(element('span','feed-name',displayName(name)),
    element('span','feed-count',`${accounts.length} account${accounts.length===1?'':'s'}`));
  const body = element('div','feed-body');
  const tools = element('div','feed-tools');
  const add = element('button','','Add accounts'); add.type='button';
  add.onclick = () => {chosenFeeds=[name]; chooseFeed();};
  const remove = element('button','delete-feed','Delete feed'); remove.type='button';
  remove.onclick = () => confirmSettingsAction(body,`Delete “${displayName(name)}”?`, 'Delete feed', async () => {
    const data = await settingsRequest('/feeds/delete',{name});
    notifyFeedsChange();
    status.textContent=`Deleted ${displayName(name)}. `;
    const undo = element('button','','Undo'); undo.type='button';
    undo.onclick = async () => {
      undo.disabled=true;
      try { await settingsRequest('/feeds/restore',{deletion_id:data.deletion_id}); notifyFeedsChange();
        status.textContent=`Restored ${displayName(name)}.`; await loadFeeds(name); }
      catch (error) {status.textContent=error.message;}
    };
    status.append(undo); await loadFeeds();
  });
  const topicEditor = new FeedTopicEditor(name, topics); topicEditors.set(name, topicEditor);
  tools.append(add,remove); body.append(tools, topicEditor.box);
  const search = element('input','account-search'); search.type='search';
  search.placeholder='Find an account'; search.setAttribute('aria-label',`Find an account in ${displayName(name)}`);
  search.autocomplete='off'; search.spellcheck=false;
  if (accounts.length) body.append(search);
  const list = element('ul','account-list');
  const more = element('button','load-accounts','Show more'); more.type='button';
  let shown=100, rendered=false;
  const renderAccounts = () => {
    rendered=true; list.replaceChildren();
    const query=search.value.trim().toLowerCase().replace(/^@/,'');
    const matching=accounts.filter(account=>account.includes(query));
    for (const account of matching.slice(0,shown)) {
      const row=element('li','account-row');
      const link=element('a','',`@${account}`); link.href='/u/'+encodeURIComponent(account);
      const button=element('button','remove-account'); button.type='button'; button.innerHTML=icon('less');
      button.setAttribute('aria-label',`Remove @${account} from ${displayName(name)}`);
      button.onclick=()=>confirmSettingsAction(row,`Remove @${account} from ${displayName(name)}?`,'Remove from this feed',async()=>{
        await settingsRequest('/feeds/accounts/remove',{name,username:account});
        notifyFeedsChange();   // the creator may still be in another feed
        status.textContent=`Removed @${account} from ${displayName(name)}.`; await loadFeeds(name);
      });
      row.append(link,button); list.append(row);
    }
    if (!matching.length) list.append(element('li','muted',accounts.length?'No matching accounts.':'No accounts yet.'));
    more.hidden=matching.length<=shown;
    if (!more.hidden) more.textContent=`Show more (${matching.length-shown} remaining)`;
  };
  more.onclick=()=>{shown+=100;renderAccounts();};
  search.oninput=()=>{shown=100;renderAccounts();};
  details.addEventListener('toggle',()=>{if(!details.open)return; if(!rendered)renderAccounts(); topicEditor.opened();});
  if (open) {renderAccounts(); topicEditor.opened();}
  body.append(list,more); details.append(summary,body);
  return details;
}
// Show only feeds whose name (or one of whose accounts) matches the search.
function filterFeeds() {
  const query = feedSearch.value.trim().toLowerCase().replace(/^@/, '');
  let shown = 0;
  for (const item of container.querySelectorAll('.feed-setting')) {
    const name = item.dataset.name;
    const match = !query || displayName(name).toLowerCase().includes(query) ||
      (feedsData[name] || []).some(account => account.includes(query)) ||
      (feedTopics[name] || []).some(word => word.includes(query));
    item.hidden = !match; if (match) shown++;
  }
  container.querySelector('.no-match')?.remove();
  if (query && !shown && Object.keys(feedsData).length) container.append(element('p','muted empty no-match','No feeds match.'));
}
async function loadFeeds(preferredFeed) {
  const version = ++loadVersion;
  container.setAttribute('aria-busy','true');
  try {
    const data = await settingsRequest('/settings/data');
    if (version !== loadVersion) return;
    feedsData = data.feeds; feedTopics = data.feed_topics || {}; feedsLoaded = true;
    const open = new Set([...container.querySelectorAll('details[open]')].map(item => item.dataset.name));
    if (preferredFeed) open.add(preferredFeed);
    const names = Object.keys(data.feeds);
    const total = new Set(Object.values(data.feeds).flat()).size;
    document.getElementById('feeds-total').textContent = `${names.length} feed${names.length===1?'':'s'} · ${total} account${total===1?'':'s'}`;
    feedSearch.hidden = names.length < 2;
    container.replaceChildren(); topicEditors.clear();
    if (!names.length) container.append(element('p','muted empty','No feeds yet. Create one, or import from Settings.'));
    for (const name of names) container.append(renderFeed(name, data.feeds[name], open.has(name), data.feed_topics?.[name] || []));
    filterFeeds();
  } catch (error) { if (version === loadVersion) status.textContent = error.message; }
  finally { if (version === loadVersion) container.setAttribute('aria-busy','false'); }
}
feedSearch.oninput = filterFeeds;
document.getElementById('create-feed').onclick = () => chooseFeed('', true);
window.addEventListener('ig-feeds-changed', event => loadFeeds(event.detail?.name));
// Accounts added or feeds edited on another page (common.js).
accountsChanged = () => loadFeeds(); feedListsEdited = () => loadFeeds();
// Back navigation can restore this page without fresh server data.
window.addEventListener('pageshow', event => { if (event.persisted) loadFeeds(); });
loadFeeds();
