PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS pantry_items (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  unit TEXT NOT NULL CHECK (unit IN ('g', 'ml', 'each')),
  reorder_at REAL NOT NULL CHECK (typeof(reorder_at) IN ('integer', 'real') AND reorder_at >= 0 AND reorder_at <= 1000000000),
  CHECK (length(trim(name)) > 0)
);

CREATE TABLE IF NOT EXISTS stock_movements (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES pantry_items(id) ON DELETE CASCADE,
  delta REAL NOT NULL CHECK (typeof(delta) IN ('integer', 'real') AND delta <> 0 AND delta > -1000000000 AND delta < 1000000000),
  reason TEXT NOT NULL CHECK (reason IN ('opening', 'used', 'bought', 'expired') AND ((reason IN ('opening', 'bought') AND delta > 0) OR (reason IN ('used', 'expired') AND delta < 0))),
  happened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE VIEW IF NOT EXISTS current_stock AS
SELECT i.id, i.name, i.unit, i.reorder_at,
       COALESCE(SUM(m.delta), 0) AS quantity
FROM pantry_items i LEFT JOIN stock_movements m ON m.item_id = i.id
GROUP BY i.id;

CREATE VIEW IF NOT EXISTS shopping_list AS
SELECT name, unit, quantity, reorder_at,
       ROUND(reorder_at - quantity, 1) AS to_buy
FROM current_stock
WHERE quantity < reorder_at
ORDER BY to_buy DESC, name;

CREATE TABLE IF NOT EXISTS recipes (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  CHECK (length(trim(name)) > 0)
);

CREATE TABLE IF NOT EXISTS recipe_ingredients (
  recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  item_id INTEGER NOT NULL REFERENCES pantry_items(id),
  quantity_per_serving REAL NOT NULL CHECK (typeof(quantity_per_serving) IN ('integer', 'real') AND quantity_per_serving > 0 AND quantity_per_serving < 1000000000),
  PRIMARY KEY (recipe_id, item_id)
);

CREATE VIEW IF NOT EXISTS recipe_requirements AS
SELECT r.name AS recipe, i.name AS ingredient, ri.quantity_per_serving, i.unit
FROM recipes r JOIN recipe_ingredients ri ON ri.recipe_id = r.id JOIN pantry_items i ON i.id = ri.item_id;

CREATE TRIGGER IF NOT EXISTS prevent_negative_stock
AFTER INSERT ON stock_movements
BEGIN
  SELECT CASE WHEN (SELECT COALESCE(SUM(delta), 0) FROM stock_movements WHERE item_id = NEW.item_id) < 0
    THEN RAISE(ABORT, 'stock cannot go below zero') END;
END;

CREATE TRIGGER IF NOT EXISTS ledger_is_append_only_update
BEFORE UPDATE ON stock_movements
BEGIN SELECT RAISE(ABORT, 'stock ledger is append-only'); END;

CREATE TRIGGER IF NOT EXISTS ledger_is_append_only_delete
BEFORE DELETE ON stock_movements
BEGIN SELECT RAISE(ABORT, 'stock ledger is append-only'); END;
