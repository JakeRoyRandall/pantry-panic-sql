import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
from pantry import ensure_recipes, needs

class PantrySQLTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript((ROOT / 'schema.sql').read_text())
        self.db.executescript((ROOT / 'seed.sql').read_text())
        self.db.commit()
        ensure_recipes(self.db)

    def tearDown(self): self.db.close()

    def stock(self, name):
        return self.db.execute('SELECT quantity FROM current_stock WHERE name = ?', (name,)).fetchone()[0]

    def test_seed_and_ledger_math(self):
        self.assertEqual(self.stock('Flour'), 1450)
        self.assertEqual(self.stock('Coffee'), 40)
        self.assertEqual(self.db.execute('SELECT name FROM shopping_list').fetchone()[0], 'Coffee')

    def test_constraints_reject_bad_item_and_movement(self):
        with self.assertRaises(sqlite3.IntegrityError): self.db.execute("INSERT INTO pantry_items VALUES (99, 'Salt', 'cups', 2)")
        with self.assertRaises(sqlite3.IntegrityError): self.db.execute("INSERT INTO stock_movements(item_id, delta, reason) VALUES (1, 0, 'used')")
        with self.assertRaises(sqlite3.IntegrityError): self.db.execute("INSERT INTO stock_movements(item_id, delta, reason) VALUES (1, 'lots', 'bought')")
        with self.assertRaises(sqlite3.IntegrityError): self.db.execute("INSERT INTO stock_movements(item_id, delta, reason) VALUES (1, 5, 'used')")
        with self.assertRaises(sqlite3.IntegrityError): self.db.execute("INSERT INTO stock_movements(item_id, delta, reason) VALUES (1, 1e20, 'bought')")

    def test_ledger_is_append_only(self):
        with self.assertRaises(sqlite3.IntegrityError): self.db.execute("UPDATE stock_movements SET delta = 1 WHERE id = 1")
        with self.assertRaises(sqlite3.IntegrityError): self.db.execute("DELETE FROM stock_movements WHERE id = 1")

    def test_negative_stock_rolls_back_transaction(self):
        before = self.stock('Coffee')
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db:
                self.db.execute("INSERT INTO stock_movements(item_id, delta, reason) SELECT id, -100, 'used' FROM pantry_items WHERE name = 'Coffee'")
        self.assertEqual(self.stock('Coffee'), before)

    def test_fresh_schema_is_idempotent(self):
        self.db.executescript((ROOT / 'schema.sql').read_text())
        self.assertEqual(self.stock('Pasta'), 900)

    def test_inaccessible_database_is_clean_cli_error(self):
        result = subprocess.run([sys.executable, str(ROOT / 'pantry.py'), '--db', '/no/such/folder/pantry.db', 'report'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('Traceback', result.stderr)

    def test_recipe_needs_scales_and_reports_missing(self):
        rows = self.db.execute("""SELECT i.name, ri.quantity_per_serving * 3 AS need,
          MAX(0, ri.quantity_per_serving * 3 - cs.quantity) AS missing
          FROM recipe_ingredients ri JOIN recipes r ON r.id = ri.recipe_id
          JOIN pantry_items i ON i.id = ri.item_id JOIN current_stock cs ON cs.id = i.id
          WHERE r.name = 'Emergency tomato pasta' ORDER BY i.name""").fetchall()
        self.assertEqual([(r['name'], r['need'], r['missing']) for r in rows], [('Pasta', 240.0, 0.0), ('Tinned tomatoes', 3.0, 2.0)])
        with self.assertRaises(ValueError): needs(self.db, 'Emergency tomato pasta', 0)
        with self.assertRaises(ValueError): needs(self.db, 'Emergency tomato pasta', float('nan'))
        with self.assertRaises(ValueError): needs(self.db, 'Emergency tomato pasta', float('inf'))

    def test_recipe_without_ingredients_is_clear_error(self):
        self.db.execute("INSERT INTO recipes(name) VALUES ('Empty bowl')")
        with self.assertRaises(ValueError): needs(self.db, 'Empty bowl', 1)

    def test_recipe_quantity_rejects_infinity(self):
        with self.assertRaises(sqlite3.IntegrityError): self.db.execute("INSERT INTO recipe_ingredients VALUES (1, 1, 1e20)")

    def test_cli_rejects_non_finite_servings_without_traceback(self):
        for value in ('nan', 'inf'):
            result = subprocess.run([sys.executable, str(ROOT / 'pantry.py'), '--db', ':memory:', 'needs', 'Emergency tomato pasta', value], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('Traceback', result.stderr)

    def test_recipe_tables_upgrade_without_changing_ledger(self):
        before = self.stock('Flour')
        self.db.executescript((ROOT / 'schema.sql').read_text())
        self.assertEqual(self.stock('Flour'), before)

if __name__ == '__main__': unittest.main()
