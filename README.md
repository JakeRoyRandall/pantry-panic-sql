# Pantry Panic SQL

Pantry Panic SQL is a fictional, 2020-inspired pantry inventory planner created retrospectively in September 2026. The calendar date is deliberate authoring art, not a historical work record. It uses SQLite as the project language with a tiny Python standard-library runner.

The relational schema tracks pantry items and a stock-movement ledger. `current_stock` derives quantities from ledger movements, while `shopping_list` shows items below their reorder threshold. A trigger rejects movements that would create negative stock. Recipe requirements are normalized in `recipes` and `recipe_ingredients`; the CLI can calculate exact needs for a desired serving count without changing stock.

The ledger is append-only: corrections are new opposite movements. Database checks require finite bounded numeric quantities and match movement signs to their reasons (`bought`/`opening` add stock; `used`/`expired` remove it). Re-running `seed` against an existing database exits cleanly with an instruction to choose a fresh path.

Run from this folder:

```sh
python3 pantry.py --db pantry.db seed
python3 pantry.py --db pantry.db report
python3 pantry.py --db pantry.db shopping
python3 pantry.py --db pantry.db move Flour -200 used
python3 pantry.py --db pantry.db recipes
python3 pantry.py --db pantry.db needs 'Emergency tomato pasta' 3
python3 pantry.py --db pantry.db plan 'Emergency tomato pasta' 3
python3 pantry.py --db pantry.db planned
python3 pantry.py --db pantry.db clear-plan
```

To start fresh, remove the local `pantry.db` and rerun the seed command. Tests use an in-memory database and never touch that file:

```sh
python3 -m unittest -v tests/test_pantry.py
```

The sample foods and recipes are fictional planning data. This tool is inventory math and does not provide food-safety advice.

Standalone snapshot tests: `python3 test_pantry.py`. Git author dates are deliberately assigned for calendar art; committer timestamps record actual September 2026 creation.

Reserve ingredients with `python3 pantry.py --db pantry.db set-reserve Pasta 200`. Reserves use the item's existing unit. Raw stock and ledger history stay unchanged; recipe and saved-plan recommendations use `max(stock - reserve, 0)`. Existing databases migrate with reserve zero. `report` shows raw, reserved, and usable stock.

Run `python3 pantry.py --db pantry.db cook-plan` to consume the entire saved meal plan. Shared ingredients are aggregated, reserves are protected, and the plan clears only after all ledger debits succeed. An immediate transaction locks before checking availability; shortages, empty plans, or contention do not partially consume stock.

Export the shopping list with `python3 pantry.py --db pantry.db shopping --format csv`. CSV uses name, current quantity, unit, and to_buy columns, sorted by purchase quantity descending then name; empty lists emit only the header.

Create a recipe with `python3 pantry.py --db pantry.db add-recipe "Lunch" "Pasta=80" "Tinned tomatoes=1"`. Each quantity is per serving in the existing pantry item unit. Quote each argument when names contain spaces. Existing recipes, duplicate ingredients, unknown items, and invalid quantities are rejected; insertion is atomic.
