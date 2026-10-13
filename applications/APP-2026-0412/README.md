# Application APP-2026-0412

This folder holds one application: `application.json` (who is applying, the
financing requested and the applicant's documents) and the two documents the
applicant supplied, `bank-statements.csv` and `annual-report.pdf`. It stands in for
the lender's document store, and both agents read the files where they are.

## The annual report

`annual-report.pdf` is one of the four case documents both agents read. It
is generated, never hand-edited: `annual-report.html` is the source, and the PDF in
this folder is its rendered output.

Editing the HTML and rendering again is the supported way to change this document,
the same as the credit policy's.

### The weak year

The year before the statements has EBITDA of SEK 1,600,000. Its administration
report names one item inside it: a food wholesaler customer went bankrupt in
March 2025 owing SEK 400,000, written off in full as a bad debt within
operating expenses. It also says the company wrote off SEK 150,000 owed by two
smaller customers the year before, and that the contract replacing the lost
work began in October 2025 for an initial term of twelve months. So the facts
point both ways: a bankruptcy looks like a one-off, but write-offs in two
years running and a short replacement contract do not. Whether that item will
recur is a judgement, and the example leaves it to the agents. Nothing else in this folder, in the prompts or in the credit policy
says how to judge it.

### Regenerate it

```bash
pip install "weasyprint>=69"          # or: pip install -e ".[docs]"
python3 -m weasyprint applications/APP-2026-0412/annual-report.html applications/APP-2026-0412/annual-report.pdf
```

Run that from the repo root, and commit both files together.

The committed PDF was rendered with WeasyPrint 69.0. A different WeasyPrint version
renders the same source to a different byte stream, and can move a line break, so
check the result rather than assuming it: the two things that matter are the page
count (3) and the tests below.

The document's `/Title` metadata comes from the `<title>` element in the HTML, so a
change to the applicant's name belongs in both the `<title>` and the body.

### What must not move

`tests/test_scenario_reconciliation.py` reads the prior year's turnover and EBITDA
out of the PDF itself, not out of the HTML: `test_annual_report_states_prior_year_revenue_and_ebitda`
is the proof that a regeneration moved no substance. After any regeneration:

```bash
pytest tests/test_scenario_reconciliation.py tests/test_annual_report_document_source.py
```
