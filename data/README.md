# Data

The files in `raw/` were provided by the technical challenge: six CSV tables (categories, customers, orders, order items, products and promotions) and the store policy manual, as a PDF.

`policies.md` is the manual extracted from the PDF, with one section per chapter, and `policies_notes.json` records the project's decisions about the contradictions in the manual. To generate the Markdown again, run `make policies` and review the result. The manual is in Portuguese, like the store and its customers, and it is kept that way on purpose: the agent quotes it literally.

`policy_rules.json` holds the numbers of the manual that the code uses (deadlines, PIX discount, installment tiers, shipping and opening hours). It records the `sha256` of the `policies.md` it came from: if the manual changes, `emporio --check-data` and the test `tests/unit/data/test_policy_rules.py` fail until someone checks the values and updates the hash (`sha256sum data/policies.md`).
