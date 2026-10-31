import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from ig import config, feeds, history


# Settings migration 3 adds this feed to every upgraded database.
GAMING = {'gaming': list(config.GAMING_STARTER_ACCOUNTS)}


class StorageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)
        for target, value in [('ig.config.CONFIG_DIR', self.path),
                              ('ig.history.HISTORY_JSON', self.path / 'history.json')]:
            mock = patch(target, value)
            mock.start()
            self.addCleanup(mock.stop)
        feeds.POOLS.clear()

    def write(self, name, data):
        (self.path / name).write_text(json.dumps(data))

    def test_migration_preserves_data_and_does_not_reimport(self):
        self.write('feeds.json', {'empty': [], 'code': {'accounts': ['cs50'], 'weight': 0}})
        self.write('blocked.json', ['UNWANTED'])
        self.write('preferences.json', {'prefer_memes': True})
        self.assertEqual(config.load_feeds(), {'empty': [], 'code': ['cs50'], **GAMING})
        self.assertEqual(config.blocked_accounts(), {'unwanted'})
        self.assertTrue(config.prefer_memes())
        config.save_feeds({})
        config.set_blocked('unwanted', False)
        config.set_prefer_memes(False)
        self.assertEqual(config.load_feeds(), {})
        self.assertEqual(config.blocked_accounts(), set())
        self.assertFalse(config.prefer_memes())
        self.assertTrue((self.path / 'feeds.json').exists())

    def test_adopts_unversioned_database_without_reimport(self):
        self.write('feeds.json', {'code': ['old']})
        config.save_feeds({'code': ['current']})
        with sqlite3.connect(self.path / 'settings.sqlite3') as db:
            db.execute('PRAGMA user_version = 0')
        self.assertEqual(config.load_feeds(), {'code': ['current'], **GAMING})
        with sqlite3.connect(self.path / 'settings.sqlite3') as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], len(config.SETTINGS_MIGRATIONS))

    def test_failed_migration_rolls_back_and_can_retry(self):
        self.write('feeds.json', {'custom': ['cs50']})
        self.write('blocked.json', 'invalid')
        with self.assertRaises(ValueError):
            config.load_feeds()
        self.write('blocked.json', [])
        self.assertEqual(config.load_feeds(), {'custom': ['cs50'], **GAMING})

    def test_concurrent_edits_preserve_every_membership(self):
        config.save_feeds({'code': []})
        def add(i):
            with config.edit_feeds() as saved:
                saved['code'].append(f'account{i}')
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(add, range(20)))
        self.assertEqual(set(config.load_feeds()['code']), {f'account{i}' for i in range(20)})
        with self.assertRaises(RuntimeError):
            with config.edit_feeds() as saved:
                saved.clear()
                raise RuntimeError('cancel')
        self.assertEqual(len(config.load_feeds()['code']), 20)

    def test_running_pool_sees_database_changes(self):
        config.save_feeds({'code': ['cs50']})
        old = feeds.feed_pool(feeds.MIX)
        config.save_feeds({'code': ['freecodecamp']})
        new = feeds.feed_pool(feeds.MIX)
        self.assertIsNot(old, new)
        self.assertEqual(set(new['seeds']), {'freecodecamp'})

    def test_history_import_once_and_concurrent_saves(self):
        self.write('history.json', {'codes': ['old'], 'last_owner': 'cs50'})
        self.assertEqual(history.load_history(), {'codes': ['old'], 'last_owner': 'cs50'})
        def save(i):
            history.save_history({'codes': [], 'last_owner': None}, [{'shortcode': str(i), 'owner': 'owner'}])
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(save, range(20)))
        self.write('history.json', {'codes': ['should_not_import'], 'last_owner': 'other'})
        self.assertEqual(set(history.load_history()['codes']), {'old'} | {str(i) for i in range(20)})

    def test_feed_selection_migration_preserves_existing_meme_preference(self):
        config.save_feeds({'memes': ['brainrot'], 'electronics': ['adafruit']})
        config.set_prefer_memes(True)
        with sqlite3.connect(self.path / 'settings.sqlite3') as db:
            db.execute('DROP TABLE text_preferences')
            db.execute('PRAGMA user_version = 1')
        self.assertEqual(config.selected_feeds(), ['memes'])
        self.assertEqual(config.load_feeds(), {'memes': ['brainrot'], 'electronics': ['adafruit'], **GAMING})
        config.set_selected_feeds(['electronics'])
        self.assertEqual(config.selected_feeds(), ['electronics'])
