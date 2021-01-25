#!/usr/bin/env python3
import argparse
import csv
import math
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).parent

def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    columns = {row[1] for row in db.execute('PRAGMA table_info(pantry_items)')}
    if columns and 'reserve_quantity' not in columns:
        db.execute("ALTER TABLE pantry_items ADD COLUMN reserve_quantity REAL NOT NULL DEFAULT 0 CHECK (typeof(reserve_quantity) IN ('integer', 'real') AND reserve_quantity >= 0 AND reserve_quantity < 1000000000)")
        db.commit()
    for view in ('shopping_list', 'meal_plan_requirements', 'current_stock'):
        db.execute(f'DROP VIEW IF EXISTS {view}')
    db.executescript((ROOT / 'schema.sql').read_text())
    return db

def seed(db):
    if db.execute('SELECT COUNT(*) FROM pantry_items').fetchone()[0]:
        raise ValueError('database is already seeded; start with a fresh --db path')
    db.executescript((ROOT / 'seed.sql').read_text())
    db.commit()

def ensure_recipes(db):
    db.executescript("""
    INSERT OR IGNORE INTO recipes (name) VALUES ('Emergency tomato pasta'), ('Coffee and toast');
    INSERT OR IGNORE INTO recipe_ingredients (recipe_id, item_id, quantity_per_serving)
      SELECT r.id, i.id, 1 FROM recipes r, pantry_items i WHERE r.name = 'Emergency tomato pasta' AND i.name = 'Tinned tomatoes';
    INSERT OR IGNORE INTO recipe_ingredients (recipe_id, item_id, quantity_per_serving)
      SELECT r.id, i.id, 80 FROM recipes r, pantry_items i WHERE r.name = 'Emergency tomato pasta' AND i.name = 'Pasta';
    INSERT OR IGNORE INTO recipe_ingredients (recipe_id, item_id, quantity_per_serving)
      SELECT r.id, i.id, 18 FROM recipes r, pantry_items i WHERE r.name = 'Coffee and toast' AND i.name = 'Coffee';
    INSERT OR IGNORE INTO recipe_ingredients (recipe_id, item_id, quantity_per_serving)
      SELECT r.id, i.id, 60 FROM recipes r, pantry_items i WHERE r.name = 'Coffee and toast' AND i.name = 'Flour';
    """)
    db.commit()

def report(db):
    print('PANTRY PANIC // current stock')
    for row in db.execute('SELECT name, quantity, unit, reserve_quantity, usable_quantity FROM current_stock ORDER BY name'):
        print(f"{row['name']}: {row['quantity']:g}{row['unit']} (reserve {row['reserve_quantity']:g}{row['unit']}, usable {row['usable_quantity']:g}{row['unit']})")

def shopping(db, output_format='text'):
    rows = db.execute('SELECT name, quantity, unit, to_buy FROM shopping_list ORDER BY to_buy DESC, name').fetchall()
    if output_format == 'csv':
        writer = csv.writer(sys.stdout, lineterminator='\n')
        writer.writerow(['name', 'quantity', 'unit', 'to_buy'])
        writer.writerows([[row['name'], row['quantity'], row['unit'], row['to_buy']] for row in rows])
        return
    print('SHOPPING LIST // buy before the recipe notices')
    for row in rows:
        print(f"{row['name']}: {row['to_buy']:g}{row['unit']}")

def recipes(db):
    for row in db.execute('SELECT name FROM recipes ORDER BY name'):
        print(row['name'])

def needs(db, name, servings):
    if not math.isfinite(servings) or servings <= 0 or servings > 100000:
        raise ValueError('servings must be positive and at most 100000')
    recipe = db.execute('SELECT id FROM recipes WHERE name = ?', (name,)).fetchone()
    if recipe is None: raise ValueError(f'unknown recipe: {name}')
    if db.execute('SELECT 1 FROM recipe_ingredients WHERE recipe_id = ? LIMIT 1', (recipe['id'],)).fetchone() is None:
        raise ValueError(f'recipe has no ingredients: {name}')
    rows = db.execute('''SELECT i.name, i.unit, ri.quantity_per_serving * ? AS needed,
      COALESCE(cs.usable_quantity, 0) AS available,
      MAX(0, ri.quantity_per_serving * ? - COALESCE(cs.usable_quantity, 0)) AS missing
      FROM recipe_ingredients ri JOIN pantry_items i ON i.id = ri.item_id
      LEFT JOIN current_stock cs ON cs.id = i.id WHERE ri.recipe_id = ? ORDER BY i.name''', (servings, servings, recipe['id']))
    print(f"{name} for {servings:g} serving(s)")
    for row in rows:
        status = 'enough' if row['missing'] == 0 else f"missing {row['missing']:g}{row['unit']}"
        print(f"{row['name']}: need {row['needed']:g}{row['unit']}, have {row['available']:g}{row['unit']} ({status})")

def plan(db, name, servings):
    needs(db, name, servings)
    recipe = db.execute('SELECT id FROM recipes WHERE name = ?', (name,)).fetchone()
    with db:
        db.execute('INSERT INTO saved_meal_plan(recipe_id, servings) VALUES (?, ?) ON CONFLICT(recipe_id) DO UPDATE SET servings = excluded.servings', (recipe['id'], servings))
    print(f"Planned {name} for {servings:g} serving(s).")

def planned(db):
    entries = db.execute('SELECT r.name, p.servings FROM saved_meal_plan p JOIN recipes r ON r.id = p.recipe_id ORDER BY r.name').fetchall()
    if not entries:
        print('Meal plan is empty.'); return
    print('MEAL PLAN // aggregate pantry needs')
    for entry in entries: print(f"{entry['name']}: {entry['servings']:g} serving(s)")
    for row in db.execute('SELECT ingredient, needed, available, missing, unit FROM meal_plan_requirements ORDER BY ingredient'):
        status = 'enough' if row['missing'] == 0 else f"missing {row['missing']:g}{row['unit']}"
        print(f"{row['ingredient']}: need {row['needed']:g}{row['unit']}, have {row['available']:g}{row['unit']} ({status})")

def clear_plan(db):
    with db: db.execute('DELETE FROM saved_meal_plan')
    print('Meal plan cleared.')

def cook_plan(db):
    db.execute('BEGIN IMMEDIATE')
    try:
        if db.execute('SELECT COUNT(*) FROM saved_meal_plan').fetchone()[0] == 0:
            raise ValueError('meal plan is empty')
        rows = db.execute('SELECT i.id, ingredient, needed, available, missing FROM meal_plan_requirements r JOIN pantry_items i ON i.name = r.ingredient').fetchall()
        if not rows: raise ValueError('meal plan has no ingredients')
        shortages = [f"{row['ingredient']} missing {row['missing']:g}" for row in rows if row['missing'] > 0]
        if shortages: raise ValueError('cannot cook plan: ' + ', '.join(shortages))
        for row in rows:
            db.execute("INSERT INTO stock_movements(item_id, delta, reason) VALUES (?, ?, 'used')", (row['id'], -row['needed']))
        db.execute('DELETE FROM saved_meal_plan')
        db.commit()
    except Exception:
        db.rollback()
        raise
    print('Meal plan cooked: stock debited and plan cleared.')

def set_reserve(db, name, amount):
    if not math.isfinite(amount) or amount < 0 or amount >= 1000000000:
        raise ValueError('reserve must be finite and between 0 and 999999999')
    with db:
        changed = db.execute('UPDATE pantry_items SET reserve_quantity = ? WHERE name = ?', (amount, name)).rowcount
        if changed == 0: raise ValueError(f'unknown pantry item: {name}')
    print(f'Reserve set: {name} {amount:g}.')

def add_recipe(db, name, specifications):
    if not name.strip() or not specifications: raise ValueError('recipe name and at least one ingredient are required')
    ingredients = []; seen = set()
    for specification in specifications:
        if '=' not in specification: raise ValueError(f'invalid ingredient specification: {specification}')
        item_name, raw_amount = specification.rsplit('=', 1)
        if not item_name: raise ValueError('ingredient name cannot be empty')
        try: amount = float(raw_amount)
        except ValueError: raise ValueError(f'invalid quantity for {item_name}')
        if not math.isfinite(amount) or amount <= 0 or amount >= 1000000000: raise ValueError('ingredient quantities must be finite and between 0 and 999999999')
        item = db.execute('SELECT id FROM pantry_items WHERE name = ?', (item_name,)).fetchone()
        if item is None: raise ValueError(f'unknown pantry item: {item_name}')
        if item['id'] in seen: raise ValueError(f'duplicate ingredient: {item_name}')
        seen.add(item['id']); ingredients.append((item['id'], amount))
    with db:
        cursor = db.execute('INSERT INTO recipes(name) VALUES (?)', (name,))
        db.executemany('INSERT INTO recipe_ingredients(recipe_id, item_id, quantity_per_serving) VALUES (?, ?, ?)', [(cursor.lastrowid, item_id, amount) for item_id, amount in ingredients])
    print(f'Recipe added: {name}.')

def add_item(db, name, unit, minimum=0, initial=0):
    if not name.strip(): raise ValueError('item name is required')
    for label, amount in (('minimum', minimum), ('initial', initial)):
        if not math.isfinite(amount) or amount < 0 or amount >= 1000000000: raise ValueError(f'{label} must be finite and between 0 and 999999999')
    with db:
        cursor = db.execute('INSERT INTO pantry_items(name, unit, reorder_at, reserve_quantity) VALUES (?, ?, ?, 0)', (name, unit, minimum))
        if initial > 0: db.execute("INSERT INTO stock_movements(item_id, delta, reason) VALUES (?, ?, 'opening')", (cursor.lastrowid, initial))
    print(f'Item added: {name}.')

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
    sub.add_parser('seed'); sub.add_parser('report')
    shop = sub.add_parser('shopping'); shop.add_argument('--format', choices=['text', 'csv'], default='text')
    sub.add_parser('recipes')
    need = sub.add_parser('needs'); need.add_argument('recipe'); need.add_argument('servings', type=float)
    meal = sub.add_parser('plan'); meal.add_argument('recipe'); meal.add_argument('servings', type=float)
    sub.add_parser('planned'); sub.add_parser('clear-plan'); sub.add_parser('cook-plan')
    use = sub.add_parser('move'); use.add_argument('name'); use.add_argument('delta', type=float); use.add_argument('reason', choices=['used', 'bought', 'expired'])
    reserve = sub.add_parser('set-reserve'); reserve.add_argument('name'); reserve.add_argument('amount', type=float)
    recipe_add = sub.add_parser('add-recipe'); recipe_add.add_argument('name'); recipe_add.add_argument('ingredients', nargs='+')
    item_add = sub.add_parser('add-item'); item_add.add_argument('name'); item_add.add_argument('unit', choices=['g', 'ml', 'each']); item_add.add_argument('--minimum', type=float, default=0); item_add.add_argument('--initial', type=float, default=0)
    try:
        args = parser.parse_args(); db = connect(args.db)
        if args.command == 'seed':
            seed(db); ensure_recipes(db); print('Seeded fictional pantry items.')
        else:
            ensure_recipes(db)
            if args.command == 'report': report(db)
            elif args.command == 'shopping': shopping(db, args.format)
            elif args.command == 'recipes': recipes(db)
            elif args.command == 'needs': needs(db, args.recipe, args.servings)
            elif args.command == 'plan': plan(db, args.recipe, args.servings)
            elif args.command == 'planned': planned(db)
            elif args.command == 'clear-plan': clear_plan(db)
            elif args.command == 'cook-plan': cook_plan(db)
            elif args.command == 'set-reserve': set_reserve(db, args.name, args.amount)
            elif args.command == 'add-recipe': add_recipe(db, args.name, args.ingredients)
            elif args.command == 'add-item': add_item(db, args.name, args.unit, args.minimum, args.initial)
            else: move(db, args.name, args.delta, args.reason); print('Stock movement recorded.')
    except (sqlite3.Error, OSError, ValueError) as error:
        if 'db' in locals(): db.rollback()
        parser.error(str(error))
    finally:
        if 'db' in locals(): db.close()

if __name__ == '__main__': main()
