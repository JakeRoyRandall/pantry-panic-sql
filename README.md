# Pantry Panic SQL

Pantry Panic SQL is a fictional, 2020-inspired pantry inventory planner created retrospectively in September 2026. The calendar date is deliberate authoring art, not a historical work record. It uses SQLite as the project language with a tiny Python standard-library runner.

The relational schema tracks pantry items and a stock-movement ledger. `current_stock` derives quantities from ledger movements, while `shopping_list` shows items below their reorder threshold. A trigger rejects movements that would create negative stock.

The ledger is append-only: corrections are new opposite movements. Database checks require finite bounded numeric quantities and match movement signs to their reasons (`bought`/`opening` add stock; `used`/`expired` remove it). Re-running `seed` against an existing database exits cleanly with an instruction to choose a fresh path.

Run from this folder:

```sh
python3 pantry.py --db pantry.db seed
python3 pantry.py --db pantry.db report
python3 pantry.py --db pantry.db shopping
python3 pantry.py --db pantry.db move Flour -200 used
```

To start fresh, remove the local `pantry.db` and rerun the seed command. Tests use an in-memory database and never touch that file:

```sh
python3 -m unittest -v tests/test_pantry.py
```

The sample foods are fictional planning data. This tool is inventory math and does not provide food-safety advice.

Standalone snapshot tests: `python3 test_pantry.py`.

Standalone snapshot tests: `python3 test_pantry.py`. Git author dates are deliberately assigned for calendar art; committer timestamps record actual September 2026 creation.
