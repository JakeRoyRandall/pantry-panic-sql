#!/usr/bin/env python3
import argparse
import math
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
    for row in db.execute('SELECT name, quantity, unit FROM current_stock ORDER BY name'):
        print(f"{row['name']}: {row['quantity']:g}{row['unit']}")

def shopping(db):
    print('SHOPPING LIST // buy before the recipe notices')
    for row in db.execute('SELECT name, to_buy, unit FROM shopping_list'):
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
      COALESCE(cs.quantity, 0) AS available,
      MAX(0, ri.quantity_per_serving * ? - COALESCE(cs.quantity, 0)) AS missing
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
    sub.add_parser('seed'); sub.add_parser('report'); sub.add_parser('shopping'); sub.add_parser('recipes')
    need = sub.add_parser('needs'); need.add_argument('recipe'); need.add_argument('servings', type=float)
    meal = sub.add_parser('plan'); meal.add_argument('recipe'); meal.add_argument('servings', type=float)
    sub.add_parser('planned'); sub.add_parser('clear-plan')
    use = sub.add_parser('move'); use.add_argument('name'); use.add_argument('delta', type=float); use.add_argument('reason', choices=['used', 'bought', 'expired'])
    try:
        args = parser.parse_args(); db = connect(args.db)
        if args.command == 'seed':
            seed(db); ensure_recipes(db); print('Seeded fictional pantry items.')
        else:
            ensure_recipes(db)
            if args.command == 'report': report(db)
            elif args.command == 'shopping': shopping(db)
            elif args.command == 'recipes': recipes(db)
            elif args.command == 'needs': needs(db, args.recipe, args.servings)
            elif args.command == 'plan': plan(db, args.recipe, args.servings)
            elif args.command == 'planned': planned(db)
            elif args.command == 'clear-plan': clear_plan(db)
            else: move(db, args.name, args.delta, args.reason); print('Stock movement recorded.')
    except (sqlite3.Error, OSError, ValueError) as error:
        if 'db' in locals(): db.rollback()
        parser.error(str(error))
    finally:
        if 'db' in locals(): db.close()

if __name__ == '__main__': main()
