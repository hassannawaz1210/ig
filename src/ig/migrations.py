"""Ordered SQLite schema upgrades, run inside the caller's transaction."""


def migrate(connection, steps):
    if not connection.in_transaction:
        raise RuntimeError('Migrations require an explicit transaction')
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if version > len(steps):
        raise RuntimeError('Database was created by a newer app version; upgrade the app')
    for index in range(version, len(steps)):
        steps[index](connection)
        connection.execute(f'PRAGMA user_version = {index + 1}')
