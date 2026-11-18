# invoice: a tiny invoice calculator

Small sample project used by the Bhima demo. Modules:

- `invoice/models.py`: `LineItem` and `Invoice` dataclasses
- `invoice/pricing.py`: subtotals and bulk discounts
- `invoice/tax.py`: GST by category
- `invoice/currency.py`: money formatting
- `invoice/parsing.py`: read line items from CSV text

Run the tests with `python -m pytest -q`.
