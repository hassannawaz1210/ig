import sqlite3
import unittest
from ig.migrations import migrate


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)

    def run_steps(self, *steps):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            migrate(self.db, steps)

    def test_ordered_upgrades_run_once_and_preserve_rows(self):
        def initial(db):
            db.execute('CREATE TABLE data (value TEXT)')
            db.execute("INSERT INTO data VALUES ('saved')")
        def upgrade(db):
            db.execute('ALTER TABLE data ADD COLUMN enabled INTEGER DEFAULT 1')
        self.run_steps(initial)
        self.run_steps(initial, upgrade)
        self.run_steps(initial, upgrade)
        self.assertEqual(self.db.execute('SELECT * FROM data').fetchall(), [('saved', 1)])
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0], 2)

    def test_failed_upgrade_rolls_back_schema_data_and_version(self):
        def initial(db):
            db.execute('CREATE TABLE data (value TEXT)')
        self.run_steps(initial)
        def broken(db):
            db.execute('ALTER TABLE data ADD COLUMN extra TEXT')
            db.execute("INSERT INTO data VALUES ('saved', 'extra')")
            raise ValueError('failed')
        with self.assertRaises(ValueError):
            self.run_steps(initial, broken)
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0], 1)
        self.assertEqual(len(self.db.execute('PRAGMA table_info(data)').fetchall()), 1)
        self.assertEqual(self.db.execute('SELECT * FROM data').fetchall(), [])

    def test_future_schema_rejected(self):
        self.db.execute('PRAGMA user_version = 99')
        with self.assertRaisesRegex(RuntimeError, 'newer app'):
            self.run_steps(lambda db: self.fail('must not run'))
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0], 99)
