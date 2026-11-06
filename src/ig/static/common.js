// SQLite owns playback preferences. Storage events only notify other open tabs.
let playbackWrite = Promise.resolve();
let playbackVersion = 0, playbackMigrationStarted = false;
function savePlaybackPreferences(changes) {
  const version = ++playbackVersion;
  const request = playbackWrite.catch(() => {}).then(async () => {
    const response = await fetch('/settings/playback', {method:'POST', keepalive:true,
      headers:{'Content-Type':'application/json'}, body:JSON.stringify(changes)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not remember playback preferences');
    const saved = {};
    for (const key of ['sound','auto_scroll']) if (key in changes) saved[key] = data[key];
    try { localStorage.setItem('ig-playback-change', JSON.stringify({changes:saved,at:Date.now()})); } catch (_) {}
    if (changes.migrate && version === playbackVersion)
      window.dispatchEvent(new CustomEvent('ig-playback-migrated',{detail:data}));
    return data;
  });
  playbackWrite = request;
  return request;
}
function initialPlaybackPreferences(data) {
  const legacy = {};
  for (const key of data.playback_missing || []) {
    if (!['sound','auto_scroll'].includes(key)) continue;
    try {
      const value = localStorage.getItem(key === 'sound' ? 'ig-sound-on' : 'ig-auto-scroll');
      if (value === 'true' || value === 'false') legacy[key] = value === 'true';
    } catch (_) {}
  }
  if (Object.keys(legacy).length && !playbackMigrationStarted) {
    playbackMigrationStarted = true;
    savePlaybackPreferences({...legacy,migrate:true}).catch(error => console.warn('Could not migrate playback preferences',error));
  }
  return {...data,...legacy};
}
let beforeLocalNavigation = async () => { await playbackWrite; };
window.flushLocalChanges = () => beforeLocalNavigation();
let routeLocalPage = null;
async function visitLocalPage(value) {
  const destination = new URL(value, location.href);
  await beforeLocalNavigation();
  if (window.parent !== window) {
    window.parent.postMessage({type:'ig-local-navigation',href:destination.href},location.origin);
    return;
  }
  if (routeLocalPage?.(destination)) return;
  location.href = destination.href;
}
let localNavigationPending = false;
document.addEventListener('click', async event => {
  const link = event.target.closest?.('a[href]');
  if (!link || event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey ||
      link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
  const destination = new URL(link.href, location.href);
  if (destination.origin !== location.origin ||
      !(/^\/(?:settings(?:\/feeds)?|reels|saved-reels)?\/?$/.test(destination.pathname) || /^\/(?:u|reel)\//.test(destination.pathname))) return;
  event.preventDefault();
  if (localNavigationPending) return;
  localNavigationPending = true;
  try { await visitLocalPage(destination.href); }
  catch (error) {
    const target = document.getElementById('action-status') || document.getElementById('settings-status');
    if (target) target.textContent = error.message + ' · try again before leaving';
  } finally { localNavigationPending = false; }
});
let accountChanged = () => {};
let accountsChanged = (usernames, saved) => { for (const username of usernames) accountChanged(username, saved); };
function notifyAccountsChange(usernames, saved) {
  if (!usernames.length) return;
  accountsChanged(usernames, saved);
  try { localStorage.setItem('ig-account-change', JSON.stringify({usernames, saved, at:Date.now()})); } catch (_) {}
}
function notifyAccountChange(username, saved) {
  accountChanged(username, saved);
  try { localStorage.setItem('ig-account-change', JSON.stringify({username, saved, at:Date.now()})); } catch (_) {}
}
if (typeof window !== 'undefined') window.addEventListener('storage', event => {
  if (event.key === 'ig-feed-import' && event.newValue) {
    try { window.dispatchEvent(new CustomEvent('ig-feeds-changed', {detail:JSON.parse(event.newValue)})); } catch (_) {}
    return;
  }
  if (event.key !== 'ig-account-change' || !event.newValue) return;
  try { const change = JSON.parse(event.newValue);
    if (Array.isArray(change.usernames)) accountsChanged(change.usernames, change.saved);
    else accountChanged(change.username, change.saved);
  } catch (_) {}
});
function showShortcuts() {
  if (document.getElementById('shortcut-help')) return;
  const dialog = document.createElement('dialog');
  dialog.id = 'shortcut-help';
  dialog.setAttribute('aria-labelledby', 'shortcut-title');
  dialog.innerHTML = `<style>
    #shortcut-help {color-scheme:dark;box-sizing:border-box;width:420px;max-width:calc(100vw - 24px);
      max-height:calc(100dvh - 24px - env(safe-area-inset-top) - env(safe-area-inset-bottom));padding:20px;overflow:hidden;
      border:1px solid #34343e;border-radius:20px;background:#17171d;color:#f5f5f7;font:14px system-ui;}
    #shortcut-help[open] {display:flex;flex-direction:column;}
    #shortcut-help::backdrop {background:#080810b8;backdrop-filter:blur(8px);}
    #shortcut-help h2 {margin:0 0 16px;font-size:20px;flex-shrink:0;}
    #shortcut-help dl {display:grid;grid-template-columns:110px minmax(0,1fr);gap:16px 10px;align-items:center;
      min-height:0;overflow-y:auto;overscroll-behavior:contain;margin:0 0 16px;padding:4px 4px 4px 0;}
    #shortcut-help dt {color:#c4b5fd;display:flex;flex-wrap:wrap;align-items:center;gap:4px;}
    #shortcut-help dd {margin:0;color:#c4c4d0;overflow-wrap:anywhere;}
    #shortcut-help kbd {white-space:nowrap;padding:4px 8px;background:#2a2835;border:1px solid #45404f;border-radius:6px;}
    #shortcut-help button {border:0;border-radius:10px;padding:10px 16px;background:#302b48;color:#ddd2ff;cursor:pointer;align-self:flex-end;flex-shrink:0;}
  </style><h2 id="shortcut-title">Keyboard shortcuts</h2><dl>
    <dt><kbd>J</kbd> / <kbd>↓</kbd></dt><dd>Next reel</dd>
    <dt><kbd>K</kbd> / <kbd>↑</kbd></dt><dd>Previous reel</dd>
    <dt><kbd>→</kbd> / <kbd>←</kbd></dt><dd>Next / previous reel from this creator; J/K returns to the mix</dd>
    <dt><kbd>Space</kbd></dt><dd>Pause / play</dd>
    <dt><kbd>H</kbd></dt><dd>Toggle Clean view</dd>
    <dt><kbd>M</kbd></dt><dd>Mute / unmute</dd>
    <dt><kbd>T</kbd></dt><dd>Toggle auto-scroll after each reel</dd>
    <dt><kbd>L</kbd></dt><dd>Like this reel locally</dd>
    <dt><kbd>N</kbd></dt><dd>Not interested in this reel</dd>
    <dt><kbd>S</kbd></dt><dd>Save / unsave in Saved reels</dd>
    <dt><kbd>D</kbd></dt><dd>Download this reel</dd>
    <dt><kbd>A</kbd></dt><dd>Add current account to a feed (or enter accounts when no reel is loaded)</dd>
    <dt><kbd>Shift+A</kbd></dt><dd>Enter accounts to add</dd>
    <dt><kbd>+</kbd></dt><dd>Create a feed</dd>
    <dt><kbd>Delete</kbd></dt><dd>Delete highlighted feed in the selector (Undo available)</dd>
    <dt><kbd>R</kbd></dt><dd>Remove account and stop its reels</dd>
    <dt><kbd>P</kbd></dt><dd>Open account profile</dd>
    <dt><kbd>C</kbd></dt><dd>Show / hide comments</dd>
    <dt><kbd>W</kbd></dt><dd>Why am I seeing this?</dd>
    <dt><kbd>B</kbd></dt><dd>Block account permanently</dd>
    <dt><kbd>Shift+B</kbd></dt><dd>Block for this session</dd>
    <dt><kbd>,</kbd></dt><dd>Choose feeds: ↑/↓ move, Space mixes, Enter selects only that feed, Esc closes</dd>
    <dt><kbd>?</kbd></dt><dd>Show shortcuts</dd>
    <dt><kbd>Esc</kbd></dt><dd>Close this window</dd>
  </dl><button type="button">Done</button>`;
  dialog.addEventListener('close', () => dialog.remove(), {once:true});
  dialog.querySelector('button').onclick = () => dialog.close();
  document.body.append(dialog); dialog.showModal();
}
function importFollowing() {
  const menu = document.getElementById('feed-menu');
  if (menu) menu.open = false;
  return chooseFeed('', false, true);
}
function notifyFeedImport(data) {
  const detail = {...data, imported:true, at:Date.now()};
  window.dispatchEvent(new CustomEvent('ig-feeds-changed', {detail}));
  try { localStorage.setItem('ig-feed-import', JSON.stringify(detail)); } catch (_) {}
}
function importFeeds() {
  if (document.getElementById('feed-picker')) return;
  const menu = document.getElementById('feed-menu');
  if (menu) menu.open = false;
  const dialog = document.createElement('dialog');
  dialog.id = 'feed-picker'; dialog.setAttribute('aria-labelledby', 'feed-picker-title');
  // Document providers can label JSON as generic or vendor-specific content.
  // Let users select the file; the preview endpoint validates its contents.
  dialog.innerHTML = feedPickerStyles() + `<form>
    <h2 id="feed-picker-title">Import backup</h2>
    <label>ig export<input type="file" name="backup"></label>
    <p class="fp-note">Choose an ig backup. Feeds and saved reels are merged; permanent blocks are kept.</p>
    <p class="fp-preview" hidden></p>
    <p class="fp-names" hidden style="max-height:140px;overflow:auto;overflow-wrap:anywhere"></p>
    <p class="fp-settings" hidden></p>
    <p class="fp-status" role="status"></p>
    <div class="fp-actions"><button type="button">Cancel</button><button type="submit" disabled>Import backup</button></div>
  </form>`;
  const form = dialog.querySelector('form'), file = form.elements.backup;
  const submit = form.querySelector('[type=submit]'), cancel = form.querySelector('[type=button]');
  const status = form.querySelector('.fp-status'), preview = form.querySelector('.fp-preview');
  const names = form.querySelector('.fp-names');
  const settings = form.querySelector('.fp-settings');
  const controller = new AbortController();
  let selected = null, saving = false, completed = false;
  dialog.addEventListener('close', () => {controller.abort(); dialog.remove();}, {once:true});
  dialog.addEventListener('cancel', event => {if (saving) event.preventDefault();});
  cancel.onclick = () => dialog.close();
  const postFile = async (path, data, signal) => {
    const response = await fetch(path, {method:'POST',body:data,headers:{'Content-Type':'application/json'},signal});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not import this file.');
    return result;
  };
  file.onchange = async () => {
    const chosen = file.files[0];
    if (!chosen) return;
    selected = null; submit.disabled = true; preview.hidden = true; names.hidden = true; settings.hidden = true;
    if (chosen.size > 5 * 1024 * 1024) { status.textContent = 'Choose an ig export smaller than 5 MB.'; return; }
    file.disabled = true; status.textContent = 'Reading feeds…';
    try {
      const data = await postFile('/feeds/import/preview', chosen, controller.signal);
      preview.textContent = `${data.feeds} feed${data.feeds === 1 ? '' : 's'} · ${data.accounts} account${data.accounts === 1 ? '' : 's'} · ${data.blocked} permanent block${data.blocked === 1 ? '' : 's'}`;
      const saved = data.saved_reels ?? 0;
      preview.textContent += ` · ${saved} saved reel${saved === 1 ? '' : 's'}`;
      names.textContent = data.names.map(name => name.replaceAll('_', ' ')).join(' · ');
      const labels = {sound:'sound',auto_scroll:'auto-scroll',scroll_animation:'reel transitions',selected_feeds:'selected feeds'};
      const applied = Array.isArray(data.settings) ? data.settings : [];
      settings.textContent = applied.length ? 'Replace current settings: ' + applied.map(key => labels[key] || key).join(', ') + '.' : '';
      settings.hidden = !applied.length;
      preview.hidden = false; names.hidden = !data.feeds; selected = chosen;
      status.textContent = ''; submit.disabled = false;
    } catch (error) { if (error.name !== 'AbortError') status.textContent = error.message; }
    finally {file.disabled = false;}
  };
  form.onsubmit = async event => {
    event.preventDefault();
    if (!selected || saving || completed || submit.disabled) return;
    saving = true; submit.disabled = true; cancel.disabled = true; file.disabled = true;
    status.textContent = 'Importing backup…';
    try {
      await beforeLocalNavigation();
      const data = await postFile('/feeds/import', selected);
      if (data.settings_values) {
        window.dispatchEvent(new CustomEvent('ig-playback-restored',{detail:data.settings_values}));
        try {
          const changes = {};
          for (const key of ['sound','auto_scroll']) if (typeof data.settings_values[key] === 'boolean') changes[key] = data.settings_values[key];
          if (Object.keys(changes).length) localStorage.setItem('ig-playback-change',JSON.stringify({changes,at:Date.now()}));
          if (data.settings_values.scroll_animation) localStorage.setItem('ig-scroll-animation',data.settings_values.scroll_animation);
        } catch (_) {}
      }
      completed = true; notifyFeedImport(data);
      status.textContent = `${data.feeds} feed${data.feeds === 1 ? '' : 's'} merged · ${data.added} account${data.added === 1 ? '' : 's'} added · ${data.blocked} block${data.blocked === 1 ? '' : 's'} added`;
      const saved = data.saved_reels_added ?? 0;
      status.textContent += ` · ${saved} saved reel${saved === 1 ? '' : 's'} added`;
      if (data.settings_applied?.length) status.textContent += ' · Settings applied';
      submit.hidden = true; cancel.textContent = 'Done';
    } catch (error) { status.textContent = error.message; }
    finally {saving = false; cancel.disabled = false; file.disabled = completed; submit.disabled = completed;}
  };
  document.body.append(dialog); dialog.showModal();
}
function feedPickerStyles() {
  return `<style>
    #feed-picker {color-scheme:dark;box-sizing:border-box;width:320px;max-width:calc(100vw - 24px);
      max-height:85dvh;overflow-y:auto;padding:14px;border:1px solid #393939;border-radius:6px;background:#181818;color:#eee;font:13px/1.5 system-ui;}
    #feed-picker::backdrop {background:#0009;}
    #feed-picker h2 {font-size:16px;font-weight:600;margin:0 0 10px;}
    #feed-picker label {display:block;margin-top:10px;color:#ccc;}
    #feed-picker input,#feed-picker select,#feed-picker textarea {box-sizing:border-box;display:block;width:100%;
      margin-top:5px;padding:9px;background:#111;border:1px solid #444;border-radius:3px;color:#eee;font:inherit;}
    #feed-picker textarea {resize:vertical;min-height:48px;}
    #feed-picker [hidden] {display:none;}
    #feed-picker .fp-note {color:#999;font-size:11px;margin:6px 0;}
    #feed-picker .fp-actions {display:flex;align-items:center;justify-content:flex-end;gap:8px;margin-top:18px;}
    #feed-picker button {padding:7px 12px;border:1px solid #444;border-radius:3px;background:#262626;color:#eee;font:inherit;cursor:pointer;}
    #feed-picker button[type=submit] {background:#eee;color:#111;border-color:#eee;}
    #feed-picker :focus-visible {outline:2px solid #bbb;outline-offset:2px;}
    #feed-picker button:disabled {opacity:.5;cursor:wait;}
    #feed-picker .fp-status {color:#ddd;margin:10px 0 0;}
    #feed-picker .fp-status:empty {display:none;}
    #feed-picker .fp-help {margin:10px 0;color:#bbb;font-size:12px;}
    #feed-picker .fp-help summary {cursor:pointer;}
    #feed-picker .fp-help ol {padding-left:20px;}
    #feed-picker .fp-import-link {padding:0;border:0;background:none;text-decoration:underline;color:#bbb;}
  </style>`;
}
async function chooseFeed(username = '', createNew = false, importing = false) {
  if (document.getElementById('feed-picker')) return false;
  const dialog = document.createElement('dialog');
  dialog.id = 'feed-picker';
  dialog.setAttribute('aria-labelledby', 'feed-picker-title');
  dialog.innerHTML = feedPickerStyles() + `<form>
    <h2 id="feed-picker-title">Add accounts</h2>
    ${importing ? `<label>Instagram export<input name="exportfile" type="file" accept=".zip,.json,.html,.htm,application/zip,application/json,text/html"></label>
    <details class="fp-help"><summary>Where do I get the file?</summary><ol>
      <li>In Instagram, open Accounts Center → Your information and permissions.</li>
      <li>Export or download your information. Choose Followers and following, All time, and JSON.</li>
      <li>Select the downloaded ZIP, or its following.json / following.html file here (up to 25 MB).</li>
    </ol><p>Processed by your local ig app; only account handles are saved.</p></details>` : ''}
    <label>Feed<select name="destination" aria-label="Feed" disabled></select></label>
    <label class="fp-new" hidden>Name<input name="feedname" maxlength="20" placeholder="Feed name" autocomplete="off"></label>
    <label>Instagram accounts<textarea name="accounts" rows="2" placeholder="@username or Instagram profile links"></textarea></label>
    ${importing ? '<p class="fp-note fp-count">Choose a file to preview accounts.</p><p class="fp-note">Existing and blocked accounts are skipped.</p>' : '<button type="button" class="fp-import-link">Import following from Instagram</button>'}
    <p class="fp-status" role="status">Loading feeds…</p>
    <div class="fp-actions"><button type="button">Cancel</button><button type="submit" disabled>Add accounts</button></div>
  </form>`;
  const form = dialog.querySelector('form');
  const select = form.elements.destination;
  const name = form.elements.feedname;
  const accounts = form.elements.accounts;
  const submit = form.querySelector('[type=submit]');
  const status = dialog.querySelector('.fp-status');
  const cancel = form.querySelector('.fp-actions [type=button]');
  const file = form.elements.exportfile;
  const count = dialog.querySelector('.fp-count');
  accounts.value = username ? '@' + username : '';
  let saving = false, previewing = false, feedsReady = false, previewReady = false, completed = false;
  const controller = new AbortController();
  const handles = () => [...new Set(accounts.value.split(/[\s,]+/).filter(Boolean).map(value => value.replace(/^@/, '').toLowerCase()))];
  const updateImport = () => {
    if (!importing) return;
    const total = handles().length;
    count.textContent = previewReady ? `${total} account${total === 1 ? '' : 's'} · edit this list before importing` : 'Choose a file to preview accounts.';
    if (total > 7500) count.textContent = 'Import up to 7,500 accounts at once. Remove some handles to continue.';
    submit.disabled = !feedsReady || !previewReady || !total || total > 7500 || saving || previewing || completed;
  };
  accounts.oninput = updateImport;
  function mode() {
    const creating = select.value === '';
    dialog.querySelector('.fp-new').hidden = !creating;
    name.required = creating;
    accounts.required = importing || !creating;
    select.closest('label').hidden = createNew;
    dialog.querySelector('h2').textContent = importing ? 'Import following' : creating ? 'New feed' : 'Add accounts';
    submit.textContent = importing ? 'Import' : creating ? 'Create' : 'Add';
  }
  select.onchange = () => { mode(); if (!select.value) name.focus(); };
  const result = new Promise(resolve => {
    dialog.addEventListener('close', () => { controller.abort(); dialog.remove(); resolve(dialog.returnValue === 'added'); }, {once:true});
  });
  cancel.onclick = () => dialog.close(completed ? 'added' : '');
  const importLink = form.querySelector('.fp-import-link');
  if (importLink) importLink.onclick = () => { dialog.close(); result.then(() => importFollowing()); };
  dialog.addEventListener('cancel', event => { if (saving) event.preventDefault(); });
  if (file) file.onchange = async () => {
    const selected = file.files[0];
    if (!selected) return;
    previewReady = false; accounts.value = ''; updateImport();
    if (selected.size > 25 * 1024 * 1024) {
      status.textContent = 'File exceeds 25 MB. Choose following.json / following.html from the ZIP, or export only Followers and following.';
      return;
    }
    previewing = true; file.disabled = true; status.textContent = 'Reading following…';
    try {
      const response = await fetch('/following/preview', {method:'POST', body:selected,
        headers:{'Content-Type':'application/octet-stream', 'X-File-Name':encodeURIComponent(selected.name)}, signal:controller.signal});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Could not read this export.');
      accounts.value = data.accounts.map(account => '@' + account).join('\n');
      accounts.rows = 5; accounts.placeholder = '@username'; previewReady = true;
      status.textContent = '';
    } catch (error) { if (error.name !== 'AbortError') status.textContent = error.message; }
    finally { previewing = false; file.disabled = false; updateImport(); }
  };
  form.addEventListener('keydown', event => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  form.onsubmit = async event => {
    event.preventDefault();
    if (saving || submit.disabled) return;
    saving = true;
    submit.disabled = true;
    select.disabled = true;
    cancel.disabled = true;
    if (file) file.disabled = true;
    status.textContent = 'Saving…';
    const creating = select.value === '';
    try {
      const response = await fetch(importing ? '/following/import' : '/feeds', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name:creating ? name.value : select.value, create:creating, accounts:importing ? handles() : accounts.value})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Could not save. Try again.');
      if (importing) notifyAccountsChange(data.accounts, true);
      else for (const account of data.accounts) notifyAccountChange(account, true);
      window.dispatchEvent(new CustomEvent('ig-feeds-changed', {detail:data}));
      if (importing) {
        completed = true;
        status.textContent = `${data.added} added to ${data.name.replaceAll('_', ' ')} · ${data.existing} already present · ${data.blocked} blocked skipped`;
        submit.hidden = true; cancel.textContent = 'Done';
        accounts.readOnly = true; name.disabled = true;
      } else if (dialog.isConnected) dialog.close(!username || data.accounts.includes(username.toLowerCase()) ? 'added' : 'saved');
    } catch (error) { status.textContent = error.message; }
    finally { saving = false; submit.disabled = false; select.disabled = completed; cancel.disabled = false;
      if (file) file.disabled = completed; updateImport(); }
  };
  document.body.append(dialog);
  dialog.showModal();
  try {
    const response = await fetch('/feeds');
    if (!response.ok) throw new Error('Could not load feeds. Close and try again.');
    const names = await response.json();
    if (!dialog.isConnected) return result;
    for (const value of names) {
      const option = document.createElement('option');
      option.value = value; option.textContent = value.replaceAll('_', ' ');
      select.append(option);
    }
    const option = document.createElement('option');
    option.value = ''; option.textContent = '+ Create new feed';
    select.append(option);
    if (createNew || !names.length) select.value = '';
    else if (typeof chosenFeeds !== 'undefined' && chosenFeeds?.length === 1 && names.includes(chosenFeeds[0])) select.value = chosenFeeds[0];
    feedsReady = true; select.disabled = false; submit.disabled = false;
    if (status.textContent === 'Loading feeds…') status.textContent = '';
    mode();
    updateImport();
    if (importing) file.focus();
    else if (!select.value) name.focus();
    else if (username) select.focus();
    else accounts.focus();
  } catch (error) { status.textContent = error.message; }
  return result;
}

function reelLinkCode(value) {
  let code = value.trim();
  if (/^[A-Za-z0-9_-]{5,30}$/.test(code)) return code;
  try {
    const url = new URL(code);
    if (!['http:', 'https:'].includes(url.protocol) ||
        !['instagram.com','www.instagram.com','m.instagram.com'].includes(url.host) || url.username || url.password) return null;
    return url.pathname.match(/^\/(?:reel|reels|p)\/([A-Za-z0-9_-]{5,30})\/?$/)?.[1] || null;
  } catch (_) { return null; }
}
function openReelLink() {
  if (document.body.classList.contains('android-app')) return showAndroidLinkSheet();
  const value = window.prompt('Instagram reel link or shortcode');
  if (value === null) return;
  const code = reelLinkCode(value);
  if (!code) { window.alert('Enter an Instagram reel/post link or shortcode'); return; }
  visitLocalPage('/reel/' + encodeURIComponent(code)).catch(error => window.alert(error.message));
}

function setupAndroidUI() {
  const menu = document.getElementById('feed-menu');
  menu.querySelector('summary').setAttribute('aria-label', 'Choose feeds');
  menu.querySelector('summary').removeAttribute('aria-keyshortcuts');
  menu.querySelector('summary').title = 'Choose feeds';
  document.getElementById('feed-create').textContent = 'Create feed';
  document.getElementById('feed-add').textContent = 'Add accounts';
  document.getElementById('mute').title = 'Mute or unmute';
  document.getElementById('mute').setAttribute('aria-label', 'Mute or unmute');
  document.querySelectorAll('a[target="_blank"]').forEach(link => link.removeAttribute('target'));
}

function androidSheet(id, title) {
  const previous = document.getElementById(id);
  previous?.close(); previous?.remove();
  const dialog = document.createElement('dialog');
  dialog.id = id;
  dialog.className = 'android-action-sheet';
  dialog.setAttribute('aria-labelledby', id + '-title');
  const heading = document.createElement('div');
  heading.className = 'sheet-heading';
  const text = document.createElement('h2');
  text.id = id + '-title';
  text.textContent = title;
  const close = document.createElement('button');
  close.type = 'button'; close.setAttribute('aria-label', 'Close'); close.innerHTML = icon('close');
  close.onclick = () => dialog.close();
  heading.append(text, close); dialog.append(heading);
  dialog.addEventListener('close', () => dialog.remove(), {once:true});
  dialog.addEventListener('click', event => {
    const bounds = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientY < bounds.top || event.clientY > bounds.bottom ||
        event.clientX < bounds.left || event.clientX > bounds.right)) dialog.close();
  });
  document.body.append(dialog);
  return dialog;
}
function showAndroidLinkSheet() {
  document.getElementById('feed-menu').open = false;
  const dialog = androidSheet('android-link-sheet', 'Open reel');
  const form = document.createElement('form');
  const input = document.createElement('input');
  input.type = 'text'; input.inputMode = 'url'; input.autocomplete = 'off';
  input.autocapitalize = 'none'; input.spellcheck = false;
  input.placeholder = 'Paste Instagram link'; input.setAttribute('aria-label', 'Instagram reel link or shortcode');
  const error = document.createElement('p'); error.className = 'link-error'; error.setAttribute('role', 'status');
  const submit = document.createElement('button'); submit.type = 'submit'; submit.className = 'link-submit'; submit.textContent = 'Open';
  form.append(input, error, submit); dialog.append(form);
  form.onsubmit = event => {
    event.preventDefault();
    const code = reelLinkCode(input.value);
    if (!code) { error.textContent = 'Use an Instagram reel link or shortcode.'; input.focus(); return; }
    dialog.close(); visitLocalPage('/reel/' + encodeURIComponent(code)).catch(error => window.alert(error.message));
  };
  dialog.showModal(); input.focus();
}
function showReelActions(index = current) {
  const post = posts[index];
  if (!post || isHidden(post)) return;
  const dialog = androidSheet('android-reel-actions', '@' + post.owner);
  const actions = document.createElement('div'); actions.className = 'sheet-actions';
  function action(label, symbol, callback) {
    const button = document.createElement('button'); button.type = 'button';
    button.innerHTML = icon(symbol);
    const text = document.createElement('span'); text.textContent = label;
    button.append(text);
    button.onclick = () => { dialog.close(); callback(); };
    actions.append(button);
  }
  action(cleanView ? 'Leave Clean view' : 'Clean view', 'play', toggleCleanView);
  action('Not interested', 'less', () => rateReel(index, 'dislike'));
  action('More from this creator', 'user', () => { if (current !== index) showSlide(index); browseCreator(1); });
  if (post.reason) action('Why this reel?', 'info', () => toggleWhy(index));
  action('Download video', 'download', () => downloadReel(index, document.getElementById('s'+index)?.querySelector('.download')));
  action('Block for this session', 'less', () => { if (current !== index) showSlide(index); blockCurrent(true); });
  action('Remove and block this creator', 'trash', () => confirmRemoveCreator(post.owner, document.getElementById('s'+index)?.querySelector('.account-action')));
  dialog.append(actions); dialog.showModal();
}

// ---- Helpers shared by the Settings and Feeds pages ----
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
async function settingsRequest(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  let data;
  try { data = await response.json(); }
  catch (error) { throw new Error(response.ok ? 'Unexpected reply from ig. Try again.' : 'Could not save. Try again.'); }
  if (!response.ok) throw new Error(data.error || 'Could not save. Try again.');
  return data;
}
// A status line that hides itself after a few seconds; one with an Undo button
// stays longer, and nothing hides while the pointer or focus is on it.
function autoHideStatus(status, {hideMs = 5000, undoHideMs = 10000} = {}) {
  let timer = null;
  const later = () => {
    clearTimeout(timer); timer = null;
    if (!status.textContent) return;
    const hide = () => {
      if (status.matches(':hover, :focus-within')) timer = setTimeout(hide, 1000);
      else { timer = null; status.replaceChildren(); }
    };
    timer = setTimeout(hide, status.querySelector('button') ? undoHideMs : hideMs);
  };
  new MutationObserver(later).observe(status, {childList:true, characterData:true, subtree:true});
  return {pending: () => timer !== null};
}

// ---- Android app update card ----
// MainActivity calls igShowAppUpdate(notice) in the top page with
// {message, action, dismissible, progress?, done?}, or null to hide it.
// Buttons are links the app intercepts (/app/update/action, /app/update/dismiss).
if (typeof window !== 'undefined' && window.top === window) {
  const UPDATE_DONE_HIDE_MS = 4000;
  let updateCard = null, updateHideTimer = null;
  const updateIcons = {
    working: '<span class="ig-update-spinner"></span>',
    done: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
    available: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5M5 19.5h14"/></svg>',
    failed: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5v5.5m0 3.2v.3"/></svg>',
  };
  function updateKind(notice) {
    if (notice.done) return 'done';
    if (notice.action === 'Retry') return 'failed';
    if (notice.action === 'Update' || notice.action === 'Install') return 'available';
    if (typeof notice.progress === 'number' || /…$/.test(notice.message)) return 'working';
    return 'done';
  }
  function updateStyles() {
    if (document.getElementById('ig-update-styles')) return;
    const style = document.createElement('style'); style.id = 'ig-update-styles';
    style.textContent = `
      #ig-update-card{position:fixed;z-index:1000;left:12px;right:12px;margin:0 auto;max-width:440px;box-sizing:border-box;
        bottom:calc(12px + env(safe-area-inset-bottom) + var(--android-nav-height,0px));
        display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:12px;padding:14px 14px 14px 16px;
        border:1px solid #333;border-radius:16px;background:#1c1c1f;color:#eee;font:15px/1.4 system-ui,sans-serif;
        box-shadow:0 10px 30px #0009;animation:ig-update-in .18s ease-out}
      #ig-update-card .ig-update-icon{display:grid;place-items:center;width:34px;height:34px;border-radius:50%;background:#2a2a2e;color:#ddd}
      #ig-update-card .ig-update-icon svg{width:19px;height:19px}
      #ig-update-card[data-kind=done] .ig-update-icon{background:#173a22;color:#5fd17f}
      #ig-update-card[data-kind=available] .ig-update-icon{background:#1d2a44;color:#8db4ff}
      #ig-update-card[data-kind=failed] .ig-update-icon{background:#3d1c1a;color:#ff8f86}
      #ig-update-card .ig-update-text{min-width:0;overflow-wrap:anywhere;font-weight:550}
      #ig-update-card .ig-update-percent{color:#999;font-weight:400;font-variant-numeric:tabular-nums}
      body:has(#ig-update-card) main{padding-bottom:120px}
      #ig-update-card .ig-update-bar{grid-column:1/-1;height:4px;border-radius:2px;background:#2e2e33;overflow:hidden}
      #ig-update-card .ig-update-bar span{display:block;height:100%;background:#8db4ff;transition:width .2s}
      #ig-update-card .ig-update-actions{display:flex;align-items:center;gap:4px}
      #ig-update-card a{display:grid;place-items:center;min-height:40px;border-radius:10px;color:inherit;text-decoration:none;font-weight:600}
      #ig-update-card .ig-update-primary{padding:0 16px;background:#eee;color:#111}
      #ig-update-card .ig-update-close{width:40px;color:#999;font-size:22px;font-weight:400}
      .ig-update-spinner{width:16px;height:16px;border:2px solid #555;border-top-color:#eee;border-radius:50%;animation:ig-update-spin .8s linear infinite}
      @keyframes ig-update-spin{to{transform:rotate(360deg)}}
      @keyframes ig-update-in{from{opacity:0;transform:translateY(8px)}}
      @media(prefers-reduced-motion:reduce){#ig-update-card{animation:none}.ig-update-spinner{animation-duration:2s}}`;
    document.head.append(style);
  }
  function hideUpdateCard() {
    clearTimeout(updateHideTimer); updateHideTimer = null;
    updateCard?.remove(); updateCard = null;
  }
  window.igAppUpdateNotice = null;
  window.igShowAppUpdate = notice => {
    window.igAppUpdateNotice = notice || null;
    window.dispatchEvent(new CustomEvent('ig-app-update', {detail: window.igAppUpdateNotice}));
    if (!notice?.message) { hideUpdateCard(); return; }
    updateStyles();
    if (!updateCard) {
      updateCard = document.createElement('div'); updateCard.id = 'ig-update-card';
      updateCard.setAttribute('role', 'status'); updateCard.setAttribute('aria-live', 'polite');
      document.body.append(updateCard);
    }
    const kind = updateKind(notice);
    updateCard.dataset.kind = kind;
    const icon = document.createElement('span'); icon.className = 'ig-update-icon'; icon.innerHTML = updateIcons[kind];
    const text = document.createElement('span'); text.className = 'ig-update-text';
    // The bar shows progress; keep the title short and the number beside it.
    text.textContent = typeof notice.progress === 'number' ? notice.message.replace(/\s*\d{1,3}%$/, '') : notice.message;
    if (typeof notice.progress === 'number') {
      const percent = document.createElement('span'); percent.className = 'ig-update-percent';
      percent.textContent = ` ${Math.round(notice.progress)}%`; text.append(percent);
    }
    const actions = document.createElement('span'); actions.className = 'ig-update-actions';
    if (notice.action) {
      const primary = document.createElement('a'); primary.className = 'ig-update-primary';
      primary.href = '/app/update/action'; primary.target = '_top'; primary.textContent = notice.action;
      actions.append(primary);
    }
    if (notice.dismissible || notice.done) {
      const close = document.createElement('a'); close.className = 'ig-update-close';
      close.textContent = '×'; close.setAttribute('aria-label', 'Dismiss');
      if (notice.done) { close.href = '#'; close.onclick = event => { event.preventDefault(); window.igShowAppUpdate(null); }; }
      else { close.href = '/app/update/dismiss'; close.target = '_top'; }
      actions.append(close);
    }
    updateCard.replaceChildren(icon, text, actions);
    if (typeof notice.progress === 'number') {
      const bar = document.createElement('span'); bar.className = 'ig-update-bar';
      const fill = document.createElement('span'); fill.style.width = Math.max(0, Math.min(100, notice.progress)) + '%';
      bar.append(fill); updateCard.append(bar);
    }
    clearTimeout(updateHideTimer);
    // The app hides "up to date" itself; one-off results (e.g. installed) hide here.
    updateHideTimer = notice.done ? setTimeout(() => window.igShowAppUpdate(null), UPDATE_DONE_HIDE_MS) : null;
  };
}
