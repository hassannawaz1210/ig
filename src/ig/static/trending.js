// Trending (bottom bar): Instagram's Explore lists as topic cards, plus the
// profiles it highlights. A topic opens a sheet to watch its popular reels
// or add it to one of your feeds. Data: /topics/trending (topics.py).
const boot = JSON.parse(document.getElementById('boot').textContent);
if (boot.android) document.body.classList.add('android-app');
const status = document.getElementById('settings-status');
const grid = document.getElementById('trending');
const tabs = document.getElementById('trending-tabs');
const creatorsSection = document.getElementById('trending-creators');
const creatorsList = document.getElementById('trending-creators-list');
const displayName = name => name.replaceAll('_', ' ');
const searchInput = document.getElementById('trending-query');
let searchVersion = 0, searchController = null, searchTimer = null;
let lists = [], selected = Number(rememberedChoice('list', 0)) || 0, loadVersion = 0;
// Your feeds and their topic words, for the sheet's "Add to feed" list.
let feeds = null, feedTopics = {}, feedsRequest = null;

function loadFeeds(force = false) {
  if (feedsRequest && !force) return feedsRequest;
  feedsRequest = settingsRequest('/settings/data').then(data => {
    feeds = Object.keys(data.feeds || {}); feedTopics = data.feed_topics || {};
    return feeds;
  }).catch(error => { feedsRequest = null; throw error; });
  return feedsRequest;
}
const feedHasTopic = (name, word) => normalizeTopicWord(word) === normalizeTopicWord(displayName(name))
  || (feedTopics[name] || []).includes(normalizeTopicWord(word));

async function load() {
  const version = ++loadVersion;
  grid.setAttribute('aria-busy', 'true');
  try {
    const data = await loadTopicJSON('/topics/trending');
    if (version !== loadVersion) return;
    lists = (Array.isArray(data.sections) ? data.sections : [])
      .map(item => ({title: typeof item?.title === 'string' && item.title.trim() ? item.title.trim() : 'Trending',
                     topics: cleanTopics(item?.topics, 60)}))
      .filter(item => item.topics.length);
    selected = Math.min(selected, Math.max(lists.length - 1, 0));
    if (searchInput.value.trim()) return;
    if (!lists.length) grid.replaceChildren(element('p', 'trend-empty', 'Instagram isn’t showing anything trending right now.'));
    else render();
    renderCreators(Array.isArray(data.creators) ? data.creators : []);
  } catch (error) {
    if (version !== loadVersion || searchInput.value.trim()) return;
    console.warn('Could not load trending topics', error);
    tabs.hidden = true;
    grid.replaceChildren(noteWithRetry('trend-empty', `Couldn’t load trending topics. ${error.message}`, load));
  } finally { if (version === loadVersion) grid.setAttribute('aria-busy', 'false'); }
}

function render() {
  tabs.replaceChildren();
  tabs.hidden = lists.length < 2;
  lists.forEach((item, index) => {
    const tab = element('button', 'trending-tab', item.title); tab.type = 'button';
    tab.setAttribute('aria-pressed', String(index === selected));
    tab.onclick = () => { selected = index; rememberChoice('list', index); render(); window.scrollTo(0, 0); };
    tabs.append(tab);
  });
  if (!lists.length) {
    grid.replaceChildren(element('p', 'trend-empty', 'Instagram isn’t showing anything trending right now.'));
    return;
  }
  grid.setAttribute('aria-label', lists[selected].title);
  grid.replaceChildren(...lists[selected].topics.map(topicCard));
}

async function searchTrending() {
  clearTimeout(searchTimer);
  searchController?.abort();
  const version = ++searchVersion;
  const query = searchInput.value.trim();
  if (!query) { grid.setAttribute('aria-busy', 'false'); load(); return; }
  const controller = searchController = new AbortController();
  tabs.hidden = true; creatorsSection.hidden = true;
  grid.setAttribute('aria-label', 'Search results');
  grid.setAttribute('aria-busy', 'true');
  grid.replaceChildren(element('p', 'trend-empty', 'Searching…'));
  try {
    const data = await loadTopicJSON('/topics/search?q=' + encodeURIComponent(query), controller.signal);
    if (version !== searchVersion) return;
    const topics = cleanTopics(data.topics, 60);
    grid.replaceChildren(...(topics.length ? topics.map(topicCard) :
      [element('p', 'trend-empty', 'No topics found. Try another search.')]));
  } catch (error) {
    if (version !== searchVersion) return;
    grid.replaceChildren(noteWithRetry('trend-empty', `Couldn’t search topics. ${error.message}`, searchTrending));
  } finally { if (version === searchVersion) grid.setAttribute('aria-busy', 'false'); }
}
searchInput.addEventListener('input', () => {
  clearTimeout(searchTimer); searchController?.abort(); ++searchVersion;
  searchTimer = setTimeout(searchTrending, 350);
});
document.getElementById('trending-search').addEventListener('submit', event => {
  event.preventDefault(); searchTrending();
});

// Tapping a card opens the topic's popular reels; the + in its corner adds
// the topic to a feed.
function topicCard(topic) {
  const card = element('div', 'trend-card-wrap');
  const button = element('button', 'trend-card'); button.type = 'button';
  button.setAttribute('aria-label', `Watch ${topic.name}` + (topic.countLabel ? `, ${topic.countLabel}` : ''));
  const picture = element('span', 'trend-picture');
  picture.append(element('span', 'trend-initial', topic.name.slice(0, 1).toUpperCase()));
  if (topic.image) {
    const image = document.createElement('img'); image.alt = ''; image.loading = 'lazy';
    image.referrerPolicy = 'no-referrer'; image.src = topic.image; image.onerror = () => image.remove();
    picture.append(image);
  }
  const text = element('span', 'trend-text');
  text.append(element('span', 'trend-name', topic.name));
  if (topic.count) text.append(element('span', 'trend-count', topic.count + ' posts'));
  button.append(picture, text);
  button.onclick = async () => {
    if (!topic.slug) { status.textContent = `${topic.name} has no page to open.`; return; }
    try { await visitLocalPage('/topic/' + encodeURIComponent(topic.slug)); }
    catch (error) { status.textContent = `Couldn’t open ${topic.name}: ${error.message}`; }
  };
  const add = element('button', 'trend-add'); add.type = 'button';
  add.innerHTML = icon('plus'); add.title = 'Add to a feed';
  add.setAttribute('aria-label', `Add ${topic.name} to a feed`); add.setAttribute('aria-haspopup', 'dialog');
  add.onclick = () => openTopicSheet(topic, add);
  card.append(button, add);
  return card;
}

function renderCreators(creators) {
  const valid = creators.filter(c => c && typeof c.username === 'string' && /^[A-Za-z0-9._]{1,30}$/.test(c.username));
  creatorsSection.hidden = !valid.length;
  creatorsList.replaceChildren(...valid.map(creator => {
    const link = element('a', 'trend-creator');
    link.href = '/u/' + encodeURIComponent(creator.username.toLowerCase());
    const picture = element('span', 'trend-creator-picture', creator.username.slice(0, 1).toUpperCase());
    if (typeof creator.profile_pic_url === 'string' && creator.profile_pic_url.startsWith('https://')) {
      const image = document.createElement('img'); image.alt = ''; image.loading = 'lazy';
      image.referrerPolicy = 'no-referrer'; image.src = creator.profile_pic_url; image.onerror = () => image.remove();
      picture.append(image);
    }
    link.append(picture, element('span', 'trend-creator-name', creator.username));
    if (creator.followers_label) link.append(element('span', 'trend-creator-count', creator.followers_label));
    return link;
  }));
}

// A topic's sheet: watch its popular reels, or add it to one of your feeds.
function openTopicSheet(topic, opener) {
  const dialog = androidSheet('topic-sheet', topic.name);
  dialog.classList.add('topic-sheet');
  if (topic.countLabel) dialog.append(element('p', 'topic-sheet-count', topic.countLabel));
  const sheetError = element('p', 'topic-sheet-error'); sheetError.setAttribute('role', 'alert');
  const watch = element('button', 'topic-sheet-action topic-sheet-watch'); watch.type = 'button';
  watch.innerHTML = icon('play'); watch.append(element('span', '', 'Watch popular reels'));
  if (!topic.slug) { watch.disabled = true; watch.title = 'This topic has no page to open.'; }
  watch.onclick = async () => {
    watch.disabled = true;
    try { await visitLocalPage('/topic/' + encodeURIComponent(topic.slug)); dialog.close(); }
    catch (error) { sheetError.textContent = `${error.message} · try again`; watch.disabled = false; }
  };
  const list = element('div', 'topic-sheet-feeds');
  list.append(element('p', 'muted', 'Loading your feeds…'));
  dialog.append(element('h3', 'topic-sheet-subtitle', 'Add to feed'), list, sheetError, watch);
  dialog.addEventListener('close', () => { if (opener?.isConnected) opener.focus(); }, {once:true});
  dialog.showModal();
  loadFeeds().then(names => {
    list.replaceChildren();
    if (!names.length) list.append(element('p', 'muted', 'No feeds yet. Create one first.'));
    for (const name of names) list.append(feedButton(name, topic, sheetError));
  }).catch(error => {
    console.warn('Could not load feeds for the topic sheet', error);
    list.replaceChildren(noteWithRetry('muted', 'Couldn’t load your feeds.', () => { dialog.close(); openTopicSheet(topic, opener); }));
  });
  return dialog;
}

function feedButton(name, topic, sheetError) {
  const button = element('button', 'topic-sheet-action topic-sheet-feed'); button.type = 'button';
  button.dataset.feed = name;
  const state = element('span', 'topic-sheet-state');
  const show = added => {
    button.disabled = added; button.classList.toggle('added', added);
    button.innerHTML = icon(added ? 'check' : 'plus');
    state.textContent = added ? 'Added' : '';
    button.append(element('span', 'topic-sheet-feed-name', displayName(name)), state);
    button.setAttribute('aria-label', added ? `${displayName(name)} already has ${topic.name}` : `Add ${topic.name} to ${displayName(name)}`);
  };
  show(feedHasTopic(name, topic.word));
  button.onclick = async () => {
    button.disabled = true; state.textContent = 'Adding…'; sheetError.textContent = '';
    try {
      const next = [...(feedTopics[name] || []), normalizeTopicWord(topic.word)];
      const data = await settingsRequest('/feeds/topics', {name, topics: next});
      if (!Array.isArray(data.topics)) throw new Error('Unexpected reply from ig. Try again.');
      feedTopics[name] = data.topics;
      notifyFeedsChange();
      show(true);
      status.textContent = `Added “${topic.name}” to ${displayName(name)}.`;
    } catch (error) {
      show(false);
      sheetError.textContent = error.message;
    }
  };
  return button;
}

// Feeds edited on another page: re-read them before the next sheet opens.
feedListsEdited = () => { if (feedsRequest) loadFeeds(true).catch(() => {}); };
window.addEventListener('ig-page-return', () => { if (searchInput.value.trim()) searchTrending(); else load(); if (feedsRequest) loadFeeds(true).catch(() => {}); });
load();
