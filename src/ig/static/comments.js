// Comment rows that look like Instagram's, shared by the reel player and the
// profile post viewer: the commenter's picture, name and how long ago, the
// comment, its reply count, and a heart with the like count on the right.
(function () {
  const escapeText = text => String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const withMentions = text => typeof linkifyMentions === 'function' ? linkifyMentions(text) : escapeText(text);
  const compact = n => Intl.NumberFormat(undefined, {notation: 'compact', maximumFractionDigits: 1}).format(n);
  const heart = () => typeof icon === 'function' ? icon('heart')
    : '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10Z"/></svg>';

  // "now", "5m", "3h", "2d", "4w", "1y", like Instagram's comment times.
  function commentAge(seconds, now = Date.now() / 1000) {
    if (!Number.isFinite(seconds)) return '';
    const age = Math.max(0, now - seconds);
    for (const [unit, size] of [['y', 31536000], ['w', 604800], ['d', 86400], ['h', 3600], ['m', 60]]) {
      if (age >= size) return Math.floor(age / size) + unit;
    }
    return 'now';
  }

  function commentHtml(comment) {
    const name = String(comment.username || '');
    const profile = '/u/' + encodeURIComponent(name.toLowerCase());
    const picture = typeof comment.profile_pic_url === 'string' && comment.profile_pic_url.startsWith('https://')
      ? `<img src="${escapeText(comment.profile_pic_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : '';
    const likes = Number(comment.like_count) > 0 ? compact(Number(comment.like_count)) : '';
    const replies = Number(comment.reply_count) > 0
      ? `<span class="comment-replies">${compact(Number(comment.reply_count))} ${Number(comment.reply_count) === 1 ? 'reply' : 'replies'}</span>` : '';
    const when = comment.created_at ? new Date(comment.created_at * 1000).toLocaleString() : '';
    // GIF comments have no text; Instagram shows the looping GIF in its place.
    const gif = typeof comment.gif === 'string' && comment.gif.startsWith('https://')
      ? `<img class="comment-gif" src="${escapeText(comment.gif)}" alt="GIF comment" loading="lazy" referrerpolicy="no-referrer">` : '';
    return `<article class="comment-row">
      <a class="comment-picture" href="${profile}" aria-hidden="true" tabindex="-1"><span>${escapeText(name.slice(0, 1).toUpperCase())}</span>${picture}</a>
      <div class="comment-main">
        <div class="comment-meta"><a class="comment-name" href="${profile}">${escapeText(name)}</a>${comment.is_verified ? '<span class="comment-verified" title="Verified" aria-label="Verified">✓</span>' : ''}${comment.is_owner ? '<span class="comment-creator">Creator</span>' : ''}<time title="${escapeText(when)}">${commentAge(comment.created_at)}</time></div>
        ${comment.text ? `<p class="comment-text">${withMentions(comment.text)}</p>` : ''}
        ${gif}
        ${comment.photo_hidden === true && !comment.text && !gif ? '<span class="comment-photo-note">Photo comment · only visible on Instagram</span>' : ''}
        ${replies}
      </div>
      <div class="comment-likes" aria-label="${Number(comment.like_count) || 0} likes">${heart()}<span>${likes}</span></div>
    </article>`;
  }

  // Grey placeholder rows while the first comments load.
  function commentPlaceholders(count = 6) {
    return '<div class="comment-placeholders" role="status" aria-label="Loading comments">' +
      '<div class="comment-row placeholder"><i class="comment-picture"></i><div class="comment-main"><i></i><i></i></div></div>'.repeat(count) +
      '</div>';
  }

  // Instagram's empty state: a title and a short line under it.
  function commentsEmptyHtml(title, detail = '') {
    return `<div class="comments-empty"><b>${escapeText(title)}</b>${detail ? `<span>${escapeText(detail)}</span>` : ''}</div>`;
  }

  Object.assign(window, {commentAge, commentHtml, commentPlaceholders, commentsEmptyHtml});
})();
