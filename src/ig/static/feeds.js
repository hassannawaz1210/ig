// Settings → Feeds: every feed with its accounts, searchable.
const boot = JSON.parse(document.getElementById('boot').textContent);
if (boot.android) document.body.classList.add('android-app');
let chosenFeeds = null;  // read by chooseFeed() to preselect a feed
let loadVersion = 0, feedsData = {}, feedTopics = {};
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
// Discovery topics: the feed name, plus extra words. ig looks up each one on
// Instagram's public topic pages to find creators outside your feeds.
function renderTopics(name, topics) {
  const box = element('div','feed-topics');
  const heading = element('div','feed-topics-heading');
  heading.append(element('span','', 'Discovery topics'),
    element('small','', 'New creators come from Instagram’s popular reels for these topics.'));
  const chips = element('div','topic-chips');
  const input = element('input','topic-input'); input.type='text'; input.maxLength=40;
  input.placeholder='Add a topic'; input.autocomplete='off'; input.spellcheck=false;
  input.setAttribute('aria-label',`Add a discovery topic to ${displayName(name)}`);
  let current = [...topics], saving = false;
  const render = () => {
    chips.replaceChildren();
    const fixed = element('span','topic-chip fixed',displayName(name)); fixed.title='From the feed name';
    chips.append(fixed);
    for (const word of current) {
      const chip = element('span','topic-chip', word);
      const remove = element('button','topic-remove','×'); remove.type='button';
      remove.setAttribute('aria-label',`Remove topic ${word}`);
      remove.onclick = () => save(current.filter(item => item !== word));
      chip.append(remove); chips.append(chip);
    }
    chips.append(input);
  };
  async function save(next) {
    if (saving) return;
    saving = true; input.disabled = true;
    const previous = current; current = next; render();
    try {
      const data = await settingsRequest('/feeds/topics', {name, topics: next});
      current = data.topics; feedTopics[name] = data.topics;
    } catch (error) { current = previous; status.textContent = error.message; }
    finally { saving = false; input.disabled = false; render(); input.focus(); }
  }
  const add = () => {
    const words = input.value.split(',').map(word => word.trim().toLowerCase().replace(/\s+/g,' ')).filter(Boolean);
    input.value = '';
    const next = [...new Set([...current, ...words])];
    if (next.length !== current.length) save(next);
  };
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ',') { event.preventDefault(); add(); }
    else if (event.key === 'Backspace' && !input.value && current.length) save(current.slice(0, -1));
  });
  input.addEventListener('blur', () => { if (input.value.trim()) add(); });
  render();
  box.append(heading, chips);
  return box;
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
    status.textContent=`Deleted ${displayName(name)}. `;
    const undo = element('button','','Undo'); undo.type='button';
    undo.onclick = async () => {
      undo.disabled=true;
      try { await settingsRequest('/feeds/restore',{deletion_id:data.deletion_id});
        status.textContent=`Restored ${displayName(name)}.`; await loadFeeds(name); }
      catch (error) {status.textContent=error.message;}
    };
    status.append(undo); await loadFeeds();
  });
  tools.append(add,remove); body.append(tools, renderTopics(name, topics));
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
  details.addEventListener('toggle',()=>{if(details.open&&!rendered)renderAccounts();});
  if (open) renderAccounts();
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
    feedsData = data.feeds; feedTopics = data.feed_topics || {};
    const open = new Set([...container.querySelectorAll('details[open]')].map(item => item.dataset.name));
    if (preferredFeed) open.add(preferredFeed);
    const names = Object.keys(data.feeds);
    const total = new Set(Object.values(data.feeds).flat()).size;
    document.getElementById('feeds-total').textContent = `${names.length} feed${names.length===1?'':'s'} · ${total} account${total===1?'':'s'}`;
    feedSearch.hidden = names.length < 2;
    container.replaceChildren();
    if (!names.length) container.append(element('p','muted empty','No feeds yet. Create one, or import from Settings.'));
    for (const name of names) container.append(renderFeed(name, data.feeds[name], open.has(name), data.feed_topics?.[name] || []));
    filterFeeds();
  } catch (error) { if (version === loadVersion) status.textContent = error.message; }
  finally { if (version === loadVersion) container.setAttribute('aria-busy','false'); }
}
feedSearch.oninput = filterFeeds;
document.getElementById('create-feed').onclick = () => chooseFeed('', true);
window.addEventListener('ig-feeds-changed', event => loadFeeds(event.detail?.name));
window.addEventListener('storage', event => { if (event.key === 'ig-account-change') loadFeeds(); });
// Back navigation can restore this page without fresh server data.
window.addEventListener('pageshow', event => { if (event.persisted) loadFeeds(); });
loadFeeds();
