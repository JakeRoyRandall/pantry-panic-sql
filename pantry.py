#!/usr/bin/env python3
import argparse
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).parent

def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript((ROOT / 'schema.sql').read_text())
    return db

def seed(db):
    if db.execute('SELECT COUNT(*) FROM pantry_items').fetchone()[0]:
        raise ValueError('database is already seeded; start with a fresh --db path')
    db.executescript((ROOT / 'seed.sql').read_text())
    db.commit()

def report(db):
    print('PANTRY PANIC // current stock')
    for row in db.execute('SELECT name, quantity, unit FROM current_stock ORDER BY name'):
        print(f"{row['name']}: {row['quantity']:g}{row['unit']}")

def shopping(db):
    print('SHOPPING LIST // buy before the recipe notices')
    for row in db.execute('SELECT name, to_buy, unit FROM shopping_list'):
        print(f"{row['name']}: {row['to_buy']:g}{row['unit']}")

def move(db, name, delta, reason):
    with db:
        item = db.execute('SELECT id FROM pantry_items WHERE name = ?', (name,)).fetchone()
        if item is None:
            raise ValueError(f'unknown pantry item: {name}')
        db.execute('INSERT INTO stock_movements(item_id, delta, reason) VALUES (?, ?, ?)', (item['id'], delta, reason))

def main():
    parser = argparse.ArgumentParser(description='A tiny SQL pantry inventory planner.')
    parser.add_argument('--db', default='pantry.db')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('seed'); sub.add_parser('report'); sub.add_parser('shopping')
    use = sub.add_parser('move'); use.add_argument('name'); use.add_argument('delta', type=float); use.add_argument('reason', choices=['used', 'bought', 'expired'])
    try:
        args = parser.parse_args(); db = connect(args.db)
        if args.command == 'seed': seed(db); print('Seeded fictional pantry items.')
        elif args.command == 'report': report(db)
        elif args.command == 'shopping': shopping(db)
        else: move(db, args.name, args.delta, args.reason); print('Stock movement recorded.')
    except (sqlite3.Error, OSError, ValueError) as error:
        if 'db' in locals(): db.rollback()
        parser.error(str(error))
    finally:
        if 'db' in locals(): db.close()

if __name__ == '__main__': main()
