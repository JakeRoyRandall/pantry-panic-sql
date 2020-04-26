import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

class PantrySQLTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript((ROOT / 'schema.sql').read_text())
        self.db.executescript((ROOT / 'seed.sql').read_text())
        self.db.commit()

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






if __name__ == '__main__': unittest.main()
