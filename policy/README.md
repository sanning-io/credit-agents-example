# The lender's credit policy

`credit-policy.pdf` is one of the four case documents both agents read. It
is generated, never hand-edited: `credit-policy.html` is the source, and the PDF in
this folder is its rendered output.

To change this document, edit the HTML and render it again.
The text in a PDF is compressed and never appears in the raw bytes, so a `grep` over
the repo does not see what the PDF says.

## Regenerate it

```bash
pip install "weasyprint>=69"          # or: pip install -e ".[docs]"
python3 -m weasyprint policy/credit-policy.html policy/credit-policy.pdf
```

Run that from the repo root, and commit both files together.

The committed PDF was rendered with WeasyPrint 69.0. A different WeasyPrint version
renders the same source to a different byte stream, and can move a line break, so
check the result rather than assuming it: the two things that matter are the page
count (3) and the tests below.

The document's `/Title` metadata comes from the `<title>` element in the HTML, so a
change to the lender's name belongs in both the `<title>` and the body.

## What must not move

`tests/test_scenario_reconciliation.py` reads the policy's figures out of the PDF
itself, not out of the HTML: the minimum DSCR, the 24-month assessment window,
the deposit tier and the conditions available to an underwriter, and the general
rules on adjusted earnings and the checker's review.

Nothing in the code reads these figures. Each agent reads the policy's text and
applies it, so a change to the policy changes what the agents are asked, and
the code carries no copy of it.

**The policy is general.** It describes no case, and it tells the checker to
challenge nothing in particular. Version 3.3 has general rules that apply to
any application: section 4, adjusted earnings (an application may be approved
on adjusted earnings only where the checker agrees with every adjustment),
section 7, the checker's review (the checker agrees or challenges, the
underwriter answers once, and the checker concludes), and section 8, where an
application the two still disagree on is referred to a senior credit officer
with both views attached. A sentence in the policy that described this
example's case would tell the agents what to conclude. After any
regeneration:

```bash
pytest tests/test_scenario_reconciliation.py tests/test_policy_document_source.py
```

`applications/APP-2026-0412/annual-report.pdf` is made the same way: see
`applications/APP-2026-0412/README.md`.
