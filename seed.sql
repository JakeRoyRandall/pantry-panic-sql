INSERT INTO pantry_items (name, unit, reorder_at) VALUES
  ('Flour', 'g', 1000),
  ('Tinned tomatoes', 'each', 2),
  ('Coffee', 'g', 250),
  ('Pasta', 'g', 500);

INSERT INTO stock_movements (item_id, delta, reason) SELECT id, 1800, 'opening' FROM pantry_items WHERE name = 'Flour';
INSERT INTO stock_movements (item_id, delta, reason) SELECT id, 1, 'opening' FROM pantry_items WHERE name = 'Tinned tomatoes';
INSERT INTO stock_movements (item_id, delta, reason) SELECT id, 120, 'opening' FROM pantry_items WHERE name = 'Coffee';
INSERT INTO stock_movements (item_id, delta, reason) SELECT id, 900, 'opening' FROM pantry_items WHERE name = 'Pasta';
INSERT INTO stock_movements (item_id, delta, reason) SELECT id, -350, 'used' FROM pantry_items WHERE name = 'Flour';
INSERT INTO stock_movements (item_id, delta, reason) SELECT id, -80, 'used' FROM pantry_items WHERE name = 'Coffee';
