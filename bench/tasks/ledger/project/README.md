# ledger

Small helpers for reading bank CSV exports and summarizing spending.

Amounts in exports look like `$1,200.00`, `(3.50)` for a debit, or `12`.
Rows are dicts with `date` (ISO), `amount`, and `category` keys.
