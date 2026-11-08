"""@usernames in bios link to profiles inside ig."""
import unittest

from ig.views import link_mentions


class MentionTests(unittest.TestCase):
    def test_mentions_become_profile_links(self):
        self.assertEqual(link_mentions('Footballer @TeamCR7 & @nike.'),
                         'Footballer <a class="mention" href="/u/teamcr7">@TeamCR7</a> &amp; '
                         '<a class="mention" href="/u/nike">@nike</a>.')

    def test_text_stays_escaped_and_emails_stay_plain(self):
        self.assertEqual(link_mentions('mail a@b.com'), 'mail a@b.com')
        self.assertEqual(link_mentions('<script>@x</script>'),
                         '&lt;script&gt;<a class="mention" href="/u/x">@x</a>&lt;/script&gt;')
        self.assertEqual(link_mentions(''), '')
        self.assertEqual(link_mentions(None), '')
