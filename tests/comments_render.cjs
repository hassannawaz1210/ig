// Instagram-style comment rows: times, escaping, pictures, replies, likes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ctx = vm.createContext({Intl, Date, Number, String, Math});
ctx.window = ctx;
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), ctx);
const now = 1_800_000_000;
assert.deepEqual([30, 300, 7200, 3 * 86400, 15 * 86400, 400 * 86400].map(age => ctx.commentAge(now - age, now)),
  ['now', '5m', '2h', '3d', '2w', '1y']);
assert.equal(ctx.commentAge(undefined), '');
const row = ctx.commentHtml({username: 'Fan.Page', text: '<b>hi</b>', like_count: 1234, reply_count: 2, is_owner: true,
  is_verified: true, created_at: Math.floor(Date.now() / 1000) - 7200, profile_pic_url: 'https://cdn.test/p.jpg'});
assert.ok(row.includes('href="/u/fan.page"'), 'name links to the profile');
assert.ok(row.includes('&lt;b&gt;hi&lt;/b&gt;') && !row.includes('<b>hi'), 'comment text is escaped');
assert.ok(row.includes('src="https://cdn.test/p.jpg"'), 'picture shown');
assert.ok(row.includes('2 replies') && row.includes('Creator') && row.includes('Verified') && row.includes('>2h<'));
assert.match(row, /<span>1\.2K<\/span>/, 'compact like count by the heart');
const plain = ctx.commentHtml({username: 'x', text: 'ok', like_count: 0, profile_pic_url: 'javascript:alert(1)'});
assert.ok(!plain.includes('javascript:') && !plain.includes('repl') && plain.includes('<span></span>'),
  'unsafe pictures dropped; no replies or count when there are none');
assert.ok(ctx.commentsEmptyHtml('No comments yet', '<i>x</i>').includes('&lt;i&gt;'));
const gifRow = ctx.commentHtml({username: 'g', text: '', gif: 'https://cdn.fbsbx.com/real.gif'});
assert.ok(gifRow.includes('class="comment-gif" src="https://cdn.fbsbx.com/real.gif"') && !gifRow.includes('comment-text'), 'GIF in place of text');
assert.ok(!ctx.commentHtml({username: 'g', text: '', gif: 'http://bad/x.gif'}).includes('comment-gif'), 'only https GIFs');
assert.ok(ctx.commentHtml({username: 'p', text: '', photo_hidden: true}).includes('Photo comment · only visible on Instagram'));
assert.ok(!ctx.commentHtml({username: 't', text: 'hi', photo_hidden: false}).includes('Photo comment'));
console.log('Comment rows passed');
