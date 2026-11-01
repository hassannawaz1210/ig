let accountChanged = () => {};
function notifyAccountChange(username, saved) {
  accountChanged(username, saved);
  try { localStorage.setItem('ig-account-change', JSON.stringify({username, saved, at:Date.now()})); } catch (_) {}
}
if (typeof window !== 'undefined') window.addEventListener('storage', event => {
  if (event.key !== 'ig-account-change' || !event.newValue) return;
  try { const change = JSON.parse(event.newValue); accountChanged(change.username, change.saved); } catch (_) {}
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
async function chooseFeed(username = '', createNew = false) {
  if (document.getElementById('feed-picker')) return false;
  const dialog = document.createElement('dialog');
  dialog.id = 'feed-picker';
  dialog.setAttribute('aria-labelledby', 'feed-picker-title');
  dialog.innerHTML = `<style>
    #feed-picker {color-scheme:dark;box-sizing:border-box;width:320px;max-width:calc(100vw - 24px);
      padding:14px;border:1px solid #393939;border-radius:6px;background:#181818;color:#eee;font:13px/1.5 system-ui;}
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
  </style><form>
    <h2 id="feed-picker-title">Add accounts</h2>
    <label>Feed<select name="destination" aria-label="Feed" disabled></select></label>
    <label class="fp-new" hidden>Name<input name="feedname" maxlength="20" placeholder="Feed name" autocomplete="off"></label>
    <label>Instagram accounts<textarea name="accounts" rows="2" placeholder="@username or Instagram profile links"></textarea></label>
    <p class="fp-status" role="status">Loading feeds…</p>
    <div class="fp-actions"><button type="button">Cancel</button><button type="submit" disabled>Add accounts</button></div>
  </form>`;
  const form = dialog.querySelector('form');
  const select = form.elements.destination;
  const name = form.elements.feedname;
  const accounts = form.elements.accounts;
  const submit = form.querySelector('[type=submit]');
  const status = dialog.querySelector('.fp-status');
  accounts.value = username ? '@' + username : '';
  let saving = false;
  function mode() {
    const creating = select.value === '';
    dialog.querySelector('.fp-new').hidden = !creating;
    name.required = creating;
    accounts.required = !creating;
    select.closest('label').hidden = createNew;
    dialog.querySelector('h2').textContent = creating ? 'New feed' : 'Add accounts';
    submit.textContent = creating ? 'Create' : 'Add';
  }
  select.onchange = () => { mode(); if (!select.value) name.focus(); };
  const result = new Promise(resolve => {
    dialog.addEventListener('close', () => { dialog.remove(); resolve(dialog.returnValue === 'added'); }, {once:true});
  });
  form.querySelector('[type=button]').onclick = () => dialog.close();
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
    status.textContent = 'Saving…';
    const creating = select.value === '';
    try {
      const response = await fetch('/feeds', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name:creating ? name.value : select.value, create:creating, accounts:accounts.value})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Could not save. Try again.');
      for (const account of data.accounts) notifyAccountChange(account, true);
      window.dispatchEvent(new CustomEvent('ig-feeds-changed', {detail:data}));
      if (dialog.isConnected) dialog.close(!username || data.accounts.includes(username.toLowerCase()) ? 'added' : 'saved');
    } catch (error) { status.textContent = error.message; }
    finally { saving = false; submit.disabled = false; select.disabled = false; }
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
    select.disabled = false; submit.disabled = false; status.textContent = '';
    mode();
    if (!select.value) name.focus();
    else if (username) select.focus();
    else accounts.focus();
  } catch (error) { status.textContent = error.message; }
  return result;
}
