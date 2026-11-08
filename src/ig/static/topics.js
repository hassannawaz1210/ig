// Topics from Instagram, shared by the Feeds and Trending pages.
// A topic from the server: {name, slug, word, count_label, image, reason?};
// feeds store the topic's `word`.
const TOPIC_TIMEOUT_MS = 15000;
const normalizeTopicWord = word => String(word).trim().toLowerCase().replace(/\s+/g, ' ');
// GET a topics route. Throws an Error with a short message on failure; a
// request cancelled through `signal` throws too, and callers ignore it.
async function loadTopicJSON(path, signal) {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, TOPIC_TIMEOUT_MS);
  const cancel = () => controller.abort();
  signal?.addEventListener('abort', cancel, {once:true});
  try {
    let response;
    try { response = await fetch(path, {cache:'no-store', signal:controller.signal}); }
    catch (error) {
      if (timedOut) throw new Error('Instagram took too long to answer.');
      if (signal?.aborted) throw error;
      throw new Error('Could not reach ig.');
    }
    let data = null;
    try { data = await response.json(); } catch (_) { /* reported below */ }
    if (!response.ok) throw new Error((data && typeof data.error === 'string' && data.error) || `HTTP ${response.status}`);
    if (!data || typeof data !== 'object') throw new Error(timedOut ? 'Instagram took too long to answer.' : 'Unexpected reply from ig.');
    return data;
  } finally { clearTimeout(timer); signal?.removeEventListener('abort', cancel); }
}
// "454M posts" → "454M", "1,234 posts" → "1.2K". Unknown shapes show no count.
function compactCount(label) {
  if (typeof label !== 'string') return null;
  const text = label.trim().replace(/\s*(?:posts?|reels?)$/i, '');
  if (/^\d+(?:\.\d+)?\s*[KMB]$/i.test(text)) return text.replace(/\s+/g, '').toUpperCase();
  const plain = text.replace(/[,\s]/g, '');
  if (/^\d{1,15}$/.test(plain)) return new Intl.NumberFormat('en', {notation:'compact', maximumFractionDigits:1}).format(Number(plain));
  return null;
}
function cleanTopic(raw) {
  if (!raw || typeof raw !== 'object' || typeof raw.word !== 'string') return null;
  const word = normalizeTopicWord(raw.word);
  if (!word || word.length > 40) return null;
  const label = typeof raw.count_label === 'string' && raw.count_label.trim() ? raw.count_label.trim() : null;
  return {
    word,
    name: typeof raw.name === 'string' && raw.name.trim() ? raw.name.trim() : word,
    slug: typeof raw.slug === 'string' && raw.slug.trim() ? raw.slug.trim() : null,
    countLabel: label, count: compactCount(label),
    image: typeof raw.image === 'string' && raw.image.startsWith('https://') ? raw.image : null,
    reason: typeof raw.reason === 'string' ? raw.reason : null,
  };
}
function cleanTopics(list, limit) {
  if (!Array.isArray(list)) return [];
  const seen = new Set(), topics = [];
  for (const raw of list) {
    const topic = cleanTopic(raw);
    if (!topic || seen.has(topic.word)) continue;
    seen.add(topic.word); topics.push(topic);
    if (topics.length >= limit) break;
  }
  return topics;
}
function noteWithRetry(className, message, retry) {
  const note = element('p', className, message);
  if (retry) {
    const button = element('button', '', 'Try again'); button.type = 'button';
    button.onclick = retry; note.append(' ', button);
  }
  return note;
}
