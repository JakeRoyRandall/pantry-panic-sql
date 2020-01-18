import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
from pantry import add_item, add_recipe, backup_database, check_database, connect, cook_plan, ensure_recipes, history, needs, plan, report, set_reserve, shopping

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

    def test_report_includes_reserve_columns(self):
        import io
        output = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(output): report(self.db)
        self.assertIn('reserve', output.getvalue())

    def test_shopping_csv_quotes_names_and_separates_numeric_fields(self):
        import csv, io, contextlib
        names = ['Comma, flour', 'Quote " flour', 'Line\nflour']
        for index, name in enumerate(names, 10):
            self.db.execute('INSERT INTO pantry_items(id, name, unit, reorder_at) VALUES (?, ?, ?, ?)', (index, name, 'g', 10))
            self.db.execute("INSERT INTO stock_movements(item_id, delta, reason) VALUES (?, 1, 'opening')", (index,))
        self.db.commit(); output = io.StringIO()
        with contextlib.redirect_stdout(output): shopping(self.db, 'csv')
        rows = list(csv.reader(io.StringIO(output.getvalue())))
        self.assertEqual(rows[0], ['name', 'quantity', 'unit', 'to_buy'])
        self.assertEqual({row[0] for row in rows[1:]}, set(names) | {'Coffee', 'Tinned tomatoes'})
        comma_row = next(row for row in rows[1:] if row[0] == 'Comma, flour')
        self.assertEqual(comma_row[2:], ['g', '9.0'])

    def test_empty_shopping_csv_is_header_only(self):
        import io, contextlib
        self.db.execute('UPDATE pantry_items SET reorder_at = 0'); self.db.commit(); output = io.StringIO()
        with contextlib.redirect_stdout(output): shopping(self.db, 'csv')
        self.assertEqual(output.getvalue(), 'name,quantity,unit,to_buy\n')

    def test_add_recipe_is_atomic_and_usable(self):
        add_recipe(self.db, 'Balcony pasta', ['Pasta=80', 'Tinned tomatoes=1'])
        row = self.db.execute("SELECT unit, quantity_per_serving FROM recipe_requirements WHERE recipe = 'Balcony pasta' ORDER BY ingredient").fetchall()
        self.assertEqual([(r['unit'], r['quantity_per_serving']) for r in row], [('g', 80.0), ('each', 1.0)])
        plan(self.db, 'Balcony pasta', 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM saved_meal_plan').fetchone()[0], 1)

    def test_add_recipe_rejects_unknown_duplicate_and_existing_without_partial_rows(self):
        before = self.db.execute('SELECT COUNT(*) FROM recipes').fetchone()[0]
        with self.assertRaises(ValueError): add_recipe(self.db, 'Broken', ['Pasta=80', 'No item=1'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM recipes').fetchone()[0], before)
        with self.assertRaises(ValueError): add_recipe(self.db, 'Duplicate', ['Pasta=80', 'Pasta=40'])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM recipes WHERE name = 'Duplicate'").fetchone()[0], 0)
        with self.assertRaises(sqlite3.IntegrityError): add_recipe(self.db, 'Emergency tomato pasta', ['Pasta=1'])

    def test_add_item_records_initial_stock_and_supports_recipe(self):
        add_item(self.db, 'Chili flakes', 'g', minimum=20, initial=50)
        row = self.db.execute("SELECT quantity, reorder_at, unit FROM current_stock WHERE name = 'Chili flakes'").fetchone()
        self.assertEqual((row['quantity'], row['reorder_at'], row['unit']), (50.0, 20.0, 'g'))
        add_recipe(self.db, 'Spicy pasta', ['Chili flakes=2'])
        self.assertEqual(self.db.execute("SELECT unit FROM recipe_requirements WHERE recipe = 'Spicy pasta'").fetchone()[0], 'g')

    def test_add_item_validation_and_duplicate_are_atomic(self):
        before = self.db.execute('SELECT COUNT(*) FROM pantry_items').fetchone()[0]
        with self.assertRaises(ValueError): add_item(self.db, 'Bad item', 'g', minimum=float('inf'), initial=10)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM pantry_items').fetchone()[0], before)
        with self.assertRaises(sqlite3.IntegrityError): add_item(self.db, 'Pasta', 'g', initial=10)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM stock_movements WHERE reason = 'opening' AND delta = 10").fetchone()[0], 0)

    def test_history_filter_limit_order_and_json(self):
        import io, contextlib, json
        output = io.StringIO()
        with contextlib.redirect_stdout(output): history(self.db, 'Flour', 2, 'json')
        records = json.loads(output.getvalue())
        self.assertEqual(len(records), 2); self.assertEqual(records[0]['item'], 'Flour'); self.assertGreater(records[0]['id'], records[1]['id'])
        self.assertEqual({record['reason'] for record in records}, {'opening', 'used'})
        with self.assertRaises(ValueError): history(self.db, 'No item', 1)
        with self.assertRaises(ValueError): history(self.db, None, 0)

    def test_backup_restores_state_and_refuses_existing_destination(self):
        import tempfile, os
        source_fd, source = tempfile.mkstemp(); os.close(source_fd)
        dest_fd, dest = tempfile.mkstemp(); os.close(dest_fd)
        try:
            source_db = connect(source); source_db.executescript((ROOT / 'seed.sql').read_text()); source_db.commit(); ensure_recipes(source_db); set_reserve(source_db, 'Pasta', 100); plan(source_db, 'Emergency tomato pasta', 2); source_db.commit()
            os.unlink(dest); backup_database(source_db, dest)
            restored = connect(dest)
            self.assertEqual(restored.execute("SELECT quantity FROM current_stock WHERE name = 'Pasta'").fetchone()[0], 900)
            self.assertEqual(restored.execute("SELECT reserve_quantity FROM pantry_items WHERE name = 'Pasta'").fetchone()[0], 100)
            self.assertEqual(restored.execute('SELECT COUNT(*) FROM saved_meal_plan').fetchone()[0], 1)
            with self.assertRaises(ValueError): backup_database(source_db, dest)
            self.assertTrue(os.path.getsize(dest) > 0)
            restored.close(); source_db.close()
        finally:
            for path in (source, dest):
                if os.path.exists(path): os.unlink(path)

    def test_backup_includes_wal_committed_data(self):
        import tempfile, os
        source_fd, source = tempfile.mkstemp(); os.close(source_fd); dest = source + '.copy'
        try:
            source_db = connect(source); source_db.execute('PRAGMA journal_mode=WAL'); source_db.executescript((ROOT / 'seed.sql').read_text()); source_db.commit(); source_db.execute("INSERT INTO pantry_items(name, unit, reorder_at) VALUES ('WAL spice', 'g', 1)"); source_db.commit(); backup_database(source_db, dest)
            copied = connect(dest); self.assertEqual(copied.execute("SELECT name FROM pantry_items WHERE name = 'WAL spice'").fetchone()[0], 'WAL spice'); copied.close(); source_db.close()
        finally:
            for path in (source, dest, source + '-wal', source + '-shm'):
                if os.path.exists(path): os.unlink(path)

    def test_check_reports_healthy_and_empty_recipe(self):
        check_database(self.db)
        self.db.execute("INSERT INTO recipes(name) VALUES ('No ingredients')"); self.db.commit()
        with self.assertRaises(ValueError): check_database(self.db)

    def test_check_reports_corrupt_foreign_key(self):
        import tempfile, os
        fd, path = tempfile.mkstemp(); os.close(fd)
        try:
            db = connect(path); db.execute('PRAGMA foreign_keys = OFF'); db.execute("INSERT INTO stock_movements(item_id, delta, reason) VALUES (999, 1, 'opening')"); db.commit(); db.close()
            corrupted = connect(path)
            with self.assertRaises(ValueError): check_database(corrupted)
            corrupted.close()
        finally:
            if os.path.exists(path): os.unlink(path)

    def test_check_cli_is_read_only_and_missing_file_stays_missing(self):
        import hashlib, subprocess, sys, tempfile, os
        fd, path = tempfile.mkstemp(); os.close(fd)
        missing = path + '.missing'
        try:
            db = connect(path); db.executescript((ROOT / 'seed.sql').read_text()); db.commit(); db.close()
            before = hashlib.sha256(Path(path).read_bytes()).digest()
            result = subprocess.run([sys.executable, str(ROOT / 'pantry.py'), '--db', path, 'check'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0); self.assertEqual(hashlib.sha256(Path(path).read_bytes()).digest(), before)
            missing_result = subprocess.run([sys.executable, str(ROOT / 'pantry.py'), '--db', missing, 'check'], capture_output=True, text=True)
            self.assertNotEqual(missing_result.returncode, 0); self.assertFalse(os.path.exists(missing)); self.assertNotIn('Traceback', missing_result.stderr)
        finally:
            if os.path.exists(path): os.unlink(path)
            if os.path.exists(missing): os.unlink(missing)

    def test_constraints_reject_bad_item_and_movement(self):
        with self.assertRaises(sqlite3.IntegrityError): self.db.execute("INSERT INTO pantry_items(id, name, unit, reorder_at) VALUES (99, 'Salt', 'cups', 2)")
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

    def test_meal_plan_aggregates_shared_ingredients_and_replaces_entries(self):
        self.db.execute("INSERT INTO recipes(name) VALUES ('Floury tomato toast')")
        rid = self.db.execute("SELECT id FROM recipes WHERE name = 'Floury tomato toast'").fetchone()[0]
        flour = self.db.execute("SELECT id FROM pantry_items WHERE name = 'Flour'").fetchone()[0]
        tomato = self.db.execute("SELECT id FROM pantry_items WHERE name = 'Tinned tomatoes'").fetchone()[0]
        self.db.executemany('INSERT INTO recipe_ingredients VALUES (?, ?, ?)', [(rid, flour, 100), (rid, tomato, 1)])
        self.db.execute("INSERT INTO saved_meal_plan VALUES ((SELECT id FROM recipes WHERE name = 'Emergency tomato pasta'), 2)")
        self.db.execute("INSERT INTO saved_meal_plan VALUES (?, 3)", (rid,))
        row = self.db.execute("SELECT needed, available, missing FROM meal_plan_requirements WHERE ingredient = 'Tinned tomatoes'").fetchone()
        self.assertEqual((row['needed'], row['available'], row['missing']), (5.0, 1.0, 4.0))
        self.db.execute("INSERT INTO saved_meal_plan VALUES ((SELECT id FROM recipes WHERE name = 'Emergency tomato pasta'), 1) ON CONFLICT(recipe_id) DO UPDATE SET servings = excluded.servings")
        self.assertEqual(self.db.execute("SELECT servings FROM saved_meal_plan WHERE recipe_id = (SELECT id FROM recipes WHERE name = 'Emergency tomato pasta')").fetchone()[0], 1)

    def test_meal_plan_persists_reload_and_clears_without_stock_mutation(self):
        before = self.stock('Pasta')
        self.db.execute("INSERT INTO saved_meal_plan VALUES ((SELECT id FROM recipes WHERE name = 'Emergency tomato pasta'), 2)")
        self.db.commit()
        self.db.executescript((ROOT / 'schema.sql').read_text())
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM saved_meal_plan').fetchone()[0], 1)
        self.db.execute('DELETE FROM saved_meal_plan'); self.db.commit()
        self.assertEqual(self.stock('Pasta'), before)

    def test_plan_rejects_non_finite_and_empty_recipe(self):
        with self.assertRaises(ValueError): plan(self.db, 'Emergency tomato pasta', float('nan'))
        self.db.execute("INSERT INTO recipes(name) VALUES ('Empty plan')")
        with self.assertRaises(ValueError): plan(self.db, 'Empty plan', 1)

    def test_reserve_is_subtracted_once_and_can_exceed_stock(self):
        set_reserve(self.db, 'Pasta', 1000)
        row = self.db.execute("SELECT quantity, reserve_quantity, usable_quantity FROM current_stock WHERE name = 'Pasta'").fetchone()
        self.assertEqual((row['quantity'], row['reserve_quantity'], row['usable_quantity']), (900.0, 1000.0, 0.0))
        self.db.execute("INSERT INTO saved_meal_plan VALUES ((SELECT id FROM recipes WHERE name = 'Emergency tomato pasta'), 2)")
        need = self.db.execute("SELECT missing FROM meal_plan_requirements WHERE ingredient = 'Pasta'").fetchone()[0]
        self.assertEqual(need, 160.0)

    def test_reserve_rejects_non_finite_and_unknown_items(self):
        with self.assertRaises(ValueError): set_reserve(self.db, 'Pasta', float('nan'))
        with self.assertRaises(ValueError): set_reserve(self.db, 'No such item', 1)

    def test_legacy_database_migrates_reserve_without_changing_ledger(self):
        import tempfile, os
        fd, path = tempfile.mkstemp(); os.close(fd)
        try:
            old = sqlite3.connect(path)
            old.executescript("CREATE TABLE pantry_items (id INTEGER PRIMARY KEY, name TEXT, unit TEXT, reorder_at REAL); CREATE TABLE stock_movements (id INTEGER PRIMARY KEY, item_id INTEGER, delta REAL, reason TEXT); INSERT INTO pantry_items VALUES (1, 'Legacy flour', 'g', 10); INSERT INTO stock_movements VALUES (1, 1, 25, 'opening');")
            old.commit(); old.close()
            migrated = connect(path)
            row = migrated.execute("SELECT quantity, reserve_quantity FROM current_stock WHERE name = 'Legacy flour'").fetchone()
            self.assertEqual((row['quantity'], row['reserve_quantity']), (25.0, 0.0))
            migrated.close()
        finally:
            os.unlink(path)

    def test_cook_plan_debits_shared_requirements_and_clears_once(self):
        self.db.execute("INSERT INTO saved_meal_plan VALUES ((SELECT id FROM recipes WHERE name = 'Emergency tomato pasta'), 0.5)")
        self.db.commit()
        before_pasta, before_tomato = self.stock('Pasta'), self.stock('Tinned tomatoes')
        cook_plan(self.db)
        self.assertEqual((self.stock('Pasta'), self.stock('Tinned tomatoes')), (before_pasta - 40, before_tomato - 0.5))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM saved_meal_plan').fetchone()[0], 0)
        with self.assertRaises(ValueError): cook_plan(self.db)

    def test_cook_shortage_rolls_back_ledger_and_plan_and_respects_reserve(self):
        set_reserve(self.db, 'Pasta', 850)
        self.db.execute("INSERT INTO saved_meal_plan VALUES ((SELECT id FROM recipes WHERE name = 'Emergency tomato pasta'), 1)")
        self.db.commit()
        before = self.stock('Pasta')
        with self.assertRaises(ValueError): cook_plan(self.db)
        self.assertEqual(self.stock('Pasta'), before)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM saved_meal_plan').fetchone()[0], 1)

    def test_cook_plan_contention_is_locked_before_read_check_debit(self):
        import tempfile, os
        fd, path = tempfile.mkstemp(); os.close(fd)
        try:
            first = connect(path); first.executescript((ROOT / 'seed.sql').read_text()); first.commit(); ensure_recipes(first)
            first.execute("INSERT INTO saved_meal_plan VALUES ((SELECT id FROM recipes WHERE name = 'Emergency tomato pasta'), 1)"); first.commit()
            second = connect(path); second.execute('PRAGMA busy_timeout = 50')
            before = second.execute("SELECT quantity FROM current_stock WHERE name = 'Pasta'").fetchone()[0]
            first.execute('BEGIN IMMEDIATE')
            with self.assertRaises(sqlite3.OperationalError): cook_plan(second)
            self.assertEqual(second.execute('SELECT quantity FROM current_stock WHERE name = \'Pasta\'').fetchone()[0], before)
            self.assertEqual(second.execute('SELECT COUNT(*) FROM saved_meal_plan').fetchone()[0], 1)
            first.rollback(); second.close(); first.close()
        finally:
            os.unlink(path)

if __name__ == '__main__': unittest.main()
