import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ig import config, recommendations as rec
from ig.migrations import migrate


USER_MOVIE_EDITS = ['cinemaedits', 'cinematicedits', 'imdb', 'marvelstudios', 'movieclips', 'netflix']


class EditsAndGamingMigrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)
        patcher = patch('ig.config.CONFIG_DIR', self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_version_2(self, feeds):
        """A database as the previous release left it, before migration 3."""
        (self.path / 'feeds.json').write_text(json.dumps(feeds))
        db = sqlite3.connect(self.path / 'settings.sqlite3')
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                migrate(db, config.SETTINGS_MIGRATIONS[:2])
        finally:
            db.close()

    def version(self):
        with sqlite3.connect(self.path / 'settings.sqlite3') as db:
            return db.execute('PRAGMA user_version').fetchone()[0]

    def test_fresh_database_has_edit_pages_and_gaming(self):
        feeds = config.load_feeds()
        self.assertEqual(feeds['movie_edits'], ['v4ultfx', 'aradz.cc', 'gdb.vfx.ae'])
        self.assertEqual(feeds['gaming'], list(config.GAMING_STARTER_ACCOUNTS))
        self.assertEqual(self.version(), 3)

    def test_users_version_2_data_loses_studios_and_gains_gaming(self):
        self.make_version_2({'movie_edits': USER_MOVIE_EDITS, 'saved': ['houseofgaming'], 'memes': ['memes']})
        feeds = config.load_feeds()
        self.assertEqual(feeds['movie_edits'], ['cinemaedits', 'cinematicedits'])
        self.assertEqual(feeds['saved'], ['houseofgaming'])
        self.assertEqual(feeds['memes'], ['memes'])
        self.assertEqual(list(feeds)[-1], 'gaming')  # New feed goes after the user's own.
        self.assertEqual(feeds['gaming'], list(config.GAMING_STARTER_ACCOUNTS))

    def test_keeps_accounts_the_user_added_and_their_own_gaming_feed(self):
        self.make_version_2({'movie_edits': ['netflix', 'myedits', 'cinemaedits'],
                             'gaming': ['mygamer'], 'other': ['imdb']})
        feeds = config.load_feeds()
        self.assertEqual(feeds['movie_edits'], ['myedits', 'cinemaedits'])
        self.assertEqual(feeds['gaming'], ['mygamer'])
        self.assertEqual(feeds['other'], ['imdb'])  # Only movie_edits is cleaned.

    def test_works_when_movie_edits_was_deleted(self):
        self.make_version_2({'code': ['cs50']})
        self.assertEqual(config.load_feeds(), {'code': ['cs50'], 'gaming': list(config.GAMING_STARTER_ACCOUNTS)})

    def test_running_again_changes_nothing(self):
        self.make_version_2({'movie_edits': USER_MOVIE_EDITS})
        first = config.load_feeds()
        with config.edit_feeds() as feeds:
            feeds['movie_edits'].append('netflix')  # Re-added on purpose after upgrading.
            del feeds['gaming']
        self.assertEqual(config.load_feeds(), {'movie_edits': first['movie_edits'] + ['netflix']})
        self.assertEqual(self.version(), 3)


class EditsAndGamingTopicTests(unittest.TestCase):
    def matches(self, bio, topic):
        return rec.topic_evidence({'biography': bio, 'posts': []}, topic) == 'bio'

    def test_movie_edits_need_edit_evidence(self):
        for bio in ['Official account of Netflix. Movies and series', 'Movie clips and trailers',
                    'The best scenes from your favorite films', 'Cinema lover', 'Credits to owners']:
            self.assertFalse(self.matches(bio, 'movie_edits'), bio)
            self.assertFalse(self.matches(bio, 'edits'), bio)
        for bio in ['cinematic edits | ae', 'Film Edits', 'velocity edits on capcut', 'after effects',
                    'fan edit requests open', 'movie editor', 'scenepacks', '#movieedits daily']:
            self.assertTrue(self.matches(bio, 'movie_edits'), bio)
            self.assertTrue(self.matches(bio, 'edits'), bio)

    def test_edit_evidence_in_recent_captions(self):
        posts = [{'is_video': True, 'caption': 'Interstellar edit #velocity'}] * 3
        self.assertEqual(rec.topic_evidence({'biography': '', 'posts': posts}, 'movie_edits'), 'captions')
        posts = [{'is_video': True, 'caption': 'New trailer for the movie'}] * 3
        self.assertIsNone(rec.topic_evidence({'biography': '', 'posts': posts}, 'movie_edits'))

    def test_gaming_topic(self):
        for bio in ['All things video games.', 'Gaming since 2001', 'PS5 and Xbox clips',
                    'Twitch streamer', 'speedrunner', 'Nintendo fan', 'esports news']:
            self.assertTrue(self.matches(bio, 'gaming'), bio)
        for bio in ['Official account of Netflix. Movies and series', 'Gym and fitness']:
            self.assertFalse(self.matches(bio, 'gaming'), bio)


if __name__ == '__main__':
    unittest.main()
