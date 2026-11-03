const boot = JSON.parse(document.getElementById('boot').textContent);
if (boot.android) document.body.classList.add('android-app');
let chosenFeeds = null;
let settingsData = {feeds:{},scroll_animation:boot.scroll_animation || 'snappy'};
let loadVersion = 0, scrollVersion = 0, scrollSaving = false;
const status = document.getElementById('settings-status');
// The status line hides itself after a few seconds; one with an Undo button
// stays longer, and nothing hides while the pointer or focus is on it.
const STATUS_HIDE_MS = 5000, STATUS_UNDO_HIDE_MS = 10000;
let statusTimer = null;
function hideStatusLater() {
  clearTimeout(statusTimer);
  statusTimer = null;
  if (!status.textContent) return;
  const hide = () => {
    if (status.matches(':hover, :focus-within')) statusTimer = setTimeout(hide, 1000);
    else { statusTimer = null; status.replaceChildren(); }
  };
  statusTimer = setTimeout(hide, status.querySelector('button') ? STATUS_UNDO_HIDE_MS : STATUS_HIDE_MS);
}
new MutationObserver(hideStatusLater).observe(status, {childList:true, characterData:true, subtree:true});
const container = document.getElementById('settings-feeds');
const scrollMode = document.getElementById('scroll-mode');
const displayName = name => name.replaceAll('_', ' ');
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
async function settingsRequest(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Could not save. Try again.');
  return data;
}
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
function renderFeed(name, accounts, open) {
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
        status.textContent=`Restored ${displayName(name)}.`; await loadSettings(name); }
      catch (error) {status.textContent=error.message;}
    };
    status.append(undo); await loadSettings();
  });
  tools.append(add,remove); body.append(tools);
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
        status.textContent=`Removed @${account} from ${displayName(name)}.`; await loadSettings(name);
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
async function loadSettings(preferredFeed) {
  const version=++loadVersion;
  const currentScrollVersion=scrollVersion;
  container.setAttribute('aria-busy','true');
  try {
    const data=await settingsRequest('/settings/data');
    if (version!==loadVersion) return;
    if (currentScrollVersion!==scrollVersion || scrollSaving) data.scroll_animation=settingsData.scroll_animation;
    settingsData=data;
    if (!scrollSaving) {
      const radio=scrollMode.querySelector(`input[value="${data.scroll_animation==='smooth'?'smooth':'snappy'}"]`);
      radio.checked=true; scrollMode.disabled=false;
    }
    const open=new Set([...container.querySelectorAll('details[open]')].map(item=>item.dataset.name));
    if (preferredFeed) open.add(preferredFeed);
    const names=Object.keys(data.feeds);
    const total=new Set(Object.values(data.feeds).flat()).size;
    document.getElementById('feeds-total').textContent=`${names.length} feed${names.length===1?'':'s'} · ${total} account${total===1?'':'s'}`;
    container.replaceChildren();
    if (!names.length) container.append(element('p','muted empty','No feeds yet. Create one or import a file below.'));
    for (const name of names) container.append(renderFeed(name,data.feeds[name],open.has(name)));
  } catch (error) {if(version===loadVersion)status.textContent=error.message;}
  finally {if(version===loadVersion)container.setAttribute('aria-busy','false');}
}
scrollMode.onchange=async event=>{
  if (event.target.name!=='scroll'||scrollSaving) return;
  const previous=settingsData.scroll_animation,mode=event.target.value;
  scrollVersion++;
  scrollSaving=true; scrollMode.disabled=true;
  try {
    const data=await settingsRequest('/settings/scroll',{mode});
    settingsData.scroll_animation=data.scroll_animation;
    try {localStorage.setItem('ig-scroll-animation',data.scroll_animation);} catch (_) {}
    status.textContent=data.scroll_animation==='smooth'?'Smooth scrolling enabled.':'Snappy scrolling enabled.';
  } catch (error) {
    scrollMode.querySelector(`input[value="${previous==='smooth'?'smooth':'snappy'}"]`).checked=true;
    status.textContent=error.message;
  } finally {scrollSaving=false;scrollMode.disabled=false;}
};
// Sound and auto-scroll are per-device choices kept in localStorage; the reels
// page reads them on load and through storage events.
const deviceSwitches = {sound:document.getElementById('sound-on'), autoScroll:document.getElementById('auto-scroll-on')};
const deviceMessages = {sound:['Reels will play with sound.','Reels will play muted.'],
  autoScroll:['Auto-scroll turned on.','Auto-scroll turned off.']};
for (const [name, input] of Object.entries(deviceSwitches)) {
  input.checked = devicePreferences.get(name);
  input.onchange = () => {
    if (devicePreferences.set(name, input.checked)) status.textContent = deviceMessages[name][input.checked ? 0 : 1];
    else { input.checked = !input.checked; status.textContent = 'Could not save this setting in this browser.'; }
  };
}
document.getElementById('create-feed').onclick=()=>chooseFeed('',true);
window.addEventListener('ig-feeds-changed',event=>loadSettings(event.detail?.name));
window.addEventListener('storage',event=>{
  const preference=devicePreferences.nameFor(event.key);
  if(preference)deviceSwitches[preference].checked=event.newValue==='true';
  if(event.key==='ig-account-change')loadSettings();
  if(event.key==='ig-scroll-animation'&&['smooth','snappy'].includes(event.newValue)&&!scrollSaving){
    scrollVersion++;settingsData.scroll_animation=event.newValue;scrollMode.querySelector(`input[value="${event.newValue}"]`).checked=true;
  }
});
// Back navigation can restore this page from the browser cache; refresh device choices.
window.addEventListener('pageshow',event=>{
  if(event.persisted)for(const [name,input] of Object.entries(deviceSwitches))input.checked=devicePreferences.get(name);
});
loadSettings();
