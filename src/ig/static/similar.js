// /u/<name>/similar: every account Instagram suggests for a profile, as
// larger cards with add to feed. Data from /related/<name> (with in_feed).
const boot = JSON.parse(document.getElementById('boot').textContent);
if (boot.android) document.body.classList.add('android-app');
const grid = document.getElementById('similar');
const status = document.getElementById('settings-status');
let accounts = [];

function addButton(account) {
  const button = element('button', 'similar-add');
  button.type = 'button';
  button.dataset.user = account.username.toLowerCase();
  showState(button, account.in_feed === true);
  button.onclick = async () => {
    if (button.dataset.state === 'in' || button.disabled) return;
    button.disabled = true;
    try { if (await chooseFeed(account.username)) showState(button, true); }
    catch (error) { console.error('Could not add @' + account.username, error); status.textContent = 'Could not add @' + account.username + '. Try again.'; }
    finally { button.disabled = false; }
  };
  return button;
}
function showState(button, inFeed) {
  button.dataset.state = inFeed ? 'in' : 'out';
  button.innerHTML = inFeed ? icon('check') + ' In feed' : icon('plus') + ' Add to feed';
}
function card(account) {
  const box = element('article', 'similar-card');
  const link = element('a', 'similar-link');
  link.href = '/u/' + encodeURIComponent(account.username.toLowerCase());
  const picture = element('span', 'similar-picture', account.username.slice(0, 1).toUpperCase());
  if (typeof account.profile_pic_url === 'string' && account.profile_pic_url.startsWith('https://')) {
    const img = document.createElement('img');
    img.alt = ''; img.loading = 'lazy'; img.referrerPolicy = 'no-referrer'; img.src = account.profile_pic_url;
    img.onerror = () => img.remove();
    picture.append(img);
  }
  const name = element('span', 'similar-name', account.username);
  if (account.is_verified) { const tick = element('span', 'verified', '✓'); tick.title = 'Verified'; name.append(tick); }
  link.append(picture, name, element('span', 'similar-full', account.full_name || ''));
  box.append(link, addButton(account));
  return box;
}
function showEmpty(text, retry) {
  const box = element('div', 'similar-empty', text);
  if (retry) {
    const button = element('button', '', 'Try again'); button.type = 'button'; button.onclick = load;
    box.append(document.createElement('br'), button);
  }
  grid.replaceChildren(box);
}
async function load() {
  grid.setAttribute('aria-busy', 'true');
  status.textContent = '';
  try {
    const response = await fetch('/related/' + encodeURIComponent(boot.username));
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.error) throw new Error(data.error || 'HTTP ' + response.status);
    accounts = (Array.isArray(data.accounts) ? data.accounts : []).filter(a => a && typeof a.username === 'string');
    if (!accounts.length) showEmpty(`Instagram has no suggestions for @${boot.username}.`);
    else grid.replaceChildren(...accounts.map(card));
  } catch (error) {
    console.error('Could not load suggested accounts', error);
    showEmpty(error.message && !/^HTTP/.test(error.message) ? error.message : 'Couldn’t load suggested accounts.', true);
  } finally { grid.setAttribute('aria-busy', 'false'); }
}
// Stay current when creators are added or feeds edited on other pages (common.js).
accountChanged = (username, saved) => {
  for (const button of grid.querySelectorAll('.similar-add'))
    if (button.dataset.user === String(username).toLowerCase()) showState(button, saved);
};
async function refreshStates() {
  try {
    const inFeeds = await loadFeedAccounts();
    for (const button of grid.querySelectorAll('.similar-add')) showState(button, inFeeds.has(button.dataset.user));
  } catch (error) { console.warn('Could not refresh which accounts are in feeds', error); }
}
feedListsEdited = refreshStates;
window.addEventListener('ig-page-return', refreshStates);
load();
