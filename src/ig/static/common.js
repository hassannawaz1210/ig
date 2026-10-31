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
    #shortcut-help {color-scheme:dark;width:360px;max-width:calc(100vw - 64px);padding:24px;
      border:1px solid #34343e;border-radius:20px;background:#17171d;color:#f5f5f7;font:14px system-ui;}
    #shortcut-help::backdrop {background:#080810b8;backdrop-filter:blur(8px);}
    #shortcut-help h2 {margin:0 0 20px;font-size:20px;}
    #shortcut-help dl {display:grid;grid-template-columns:100px 1fr;gap:16px 10px;align-items:center;}
    #shortcut-help dt {color:#c4b5fd;} #shortcut-help dd {margin:0;color:#c4c4d0;}
    #shortcut-help kbd {padding:4px 8px;background:#2a2835;border:1px solid #45404f;border-radius:6px;}
    #shortcut-help button {border:0;border-radius:10px;padding:10px 16px;background:#302b48;color:#ddd2ff;cursor:pointer;float:right;}
  </style><h2 id="shortcut-title">Keyboard shortcuts</h2><dl>
    <dt><kbd>J</kbd> / <kbd>↓</kbd></dt><dd>Next reel</dd>
    <dt><kbd>K</kbd> / <kbd>↑</kbd></dt><dd>Previous reel</dd>
    <dt><kbd>Space</kbd></dt><dd>Pause / play</dd>
    <dt><kbd>M</kbd></dt><dd>Mute / unmute</dd>
    <dt><kbd>L</kbd></dt><dd>Like this reel locally</dd>
    <dt><kbd>N</kbd></dt><dd>Not interested in this reel</dd>
    <dt><kbd>S</kbd></dt><dd>Save / unsave in Saved reels</dd>
    <dt><kbd>D</kbd></dt><dd>Download this reel</dd>
    <dt><kbd>A</kbd></dt><dd>Add account to a feed</dd>
    <dt><kbd>R</kbd></dt><dd>Remove account from all feeds</dd>
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
async function chooseFeed(username) {
  if (document.getElementById('feed-picker')) return false;
  if (!document.getElementById('feed-picker-style')) {
    const style = document.createElement('style');
    style.id = 'feed-picker-style';
    style.textContent = `
      #feed-picker {color-scheme:dark;box-sizing:border-box;width:400px;max-width:calc(100vw - 32px);
        padding:0;border:1px solid #34343e;border-radius:22px;background:#17171d;color:#f5f5f7;
        box-shadow:0 28px 100px #0009;font:14px/1.5 system-ui,sans-serif;overflow:hidden;}
      #feed-picker::backdrop {background:#080810b8;backdrop-filter:blur(8px);}
      #feed-picker .fp-head {padding:24px 24px 18px;position:relative;}
      #feed-picker h2 {margin:0 36px 18px 0;font-size:21px;line-height:1.3;letter-spacing:-.4px;}
      #feed-picker .fp-close {position:absolute;right:18px;top:18px;width:32px;height:32px;
        border:0;border-radius:50%;background:#26262f;color:#a9a9b7;font-size:22px;cursor:pointer;}
      #feed-picker .fp-account {display:flex;align-items:center;gap:12px;}
      #feed-picker .fp-avatar {display:grid;place-items:center;width:42px;height:42px;
        border-radius:14px;background:#302b48;color:#c4b5fd;font-size:20px;font-weight:600;}
      #feed-picker .fp-username {font-weight:600;font-size:15px;overflow-wrap:anywhere;}
      #feed-picker .fp-hint {color:#9696a6;font-size:12px;margin-top:2px;}
      #feed-picker .fp-list {padding:0 12px 12px;max-height:45vh;overflow-y:auto;}
      #feed-picker .fp-row {width:100%;display:flex;align-items:center;gap:12px;text-align:left;
        padding:14px 12px;border:1px solid transparent;border-radius:12px;background:transparent;
        color:inherit;font:inherit;cursor:pointer;}
      #feed-picker .fp-row:hover:not(:disabled) {background:#24232f;border-color:#3b3552;}
      #feed-picker button:focus-visible {outline:2px solid #b9a3ff;outline-offset:-2px;}
      #feed-picker .fp-symbol {display:grid;place-items:center;width:36px;height:36px;
        border-radius:10px;background:#262630;color:#c4b5fd;font-size:18px;}
      #feed-picker .fp-name {font-weight:600;text-transform:capitalize;}
      #feed-picker .fp-count {color:#858596;font-size:12px;}
      #feed-picker .fp-action .icon {width:16px;height:16px;}
      #feed-picker .fp-close {display:grid;place-items:center;}
      #feed-picker .fp-action {margin-left:auto;color:#c4b5fd;font-size:13px;}
      #feed-picker .fp-row:disabled {cursor:default;opacity:.6;}
      #feed-picker .fp-footer {padding:14px 24px;border-top:1px solid #292932;color:#858596;font-size:12px;}
      #feed-picker .fp-status {margin:0;padding:0 24px 12px;color:#c4b5fd;}
      #feed-picker .fp-status:empty {display:none;}`;
    document.head.append(style);
  }
  const dialog = document.createElement('dialog');
  dialog.id = 'feed-picker';
  dialog.setAttribute('aria-labelledby', 'feed-picker-title');
  dialog.innerHTML = `<div class="fp-head">
    <h2 id="feed-picker-title">Add to a feed</h2>
    <button class="fp-close" aria-label="Close">${icon('close')}</button>
    <div class="fp-account"><span class="fp-avatar">${icon('user')}</span><div>
      <div class="fp-username"></div><div class="fp-hint">Choose a feed for this account</div>
    </div></div></div>
    <div class="fp-list" aria-label="Your feeds"></div>
    <p class="fp-status" role="status" aria-live="polite">Loading feeds…</p>
    <div class="fp-footer">Select a feed to save · Esc to close</div>`;
  document.body.append(dialog);
  dialog.querySelector('.fp-username').textContent = '@' + username;
  const list = dialog.querySelector('.fp-list');
  const status = dialog.querySelector('.fp-status');
  let saving = false;
  const result = new Promise(resolve => {
    dialog.addEventListener('close', () => { dialog.remove(); resolve(dialog.returnValue === 'added'); }, {once:true});
    dialog.querySelector('.fp-close').onclick = () => dialog.close('cancel');
  });
  dialog.showModal();
  try {
    const r = await fetch('/feeds?username=' + encodeURIComponent(username));
    if (!r.ok) throw new Error('Could not load feeds. Close and try again.');
    const feeds = await r.json();
    if (!dialog.isConnected) return result;
    status.textContent = '';
    feeds.forEach(feed => {
      const row = document.createElement('button');
      row.className = 'fp-row';
      row.innerHTML = `<span class="fp-symbol">${icon('folder')}</span><span><span class="fp-name"></span>
        <div class="fp-count"></div></span><span class="fp-action"></span>`;
      row.querySelector('.fp-name').textContent = feed.name;
      row.querySelector('.fp-count').textContent = feed.count + (feed.count === 1 ? ' account' : ' accounts');
      row.querySelector('.fp-action').innerHTML = (feed.saved ? icon('check') : icon('plus')) + (feed.saved ? ' Added' : ' Add');
      row.disabled = feed.saved;
      row.onclick = async () => {
        if (saving) return;
        saving = true;
        status.textContent = 'Adding to ' + feed.name + '…';
        try {
          const response = await fetch('/add', {method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({username, feed:feed.name})});
          if (!response.ok) throw new Error('Could not save. Try again.');
          notifyAccountChange(username, true);
          if (dialog.isConnected) dialog.close('added');
        } catch (error) { status.textContent = error.message; }
        finally { saving = false; }
      };
      list.append(row);
    });
    list.querySelector('button:not(:disabled)')?.focus();
  } catch (error) { status.textContent = error.message; }
  return result;
}
