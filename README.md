# Credit agents with a verifiable audit trail

This demo shows what verifiable evidence between two AI agents looks like. An
underwriting agent assesses a loan application, and a checker agent reviews
the assessment and agrees with it or challenges it. Every step each agent
takes, and every message between them, is signed by that agent and recorded
with [Sanning](https://sanning.io). Afterwards, anyone can verify the records
offline, without relying on Sanning.

The lender (Bruksvik) and the applicant (Nordvik Haulage AB) are invented.

## How the agents work the case

Both agents read the same documents and use the same calculator, and nothing
tells either one what to decide. The annual report includes a bad debt that
may or may not be a one-off, so the checker agent may challenge the
assessment. The underwriting agent then revises or holds its position, and the
case ends in approval with conditions or goes to a senior credit officer.

## Run it yourself

You need Python 3.11 or later, a Sanning API key, an OpenRouter API key and an
S3-compatible bucket. `.env.example` lists every setting.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
cp .env.example .env      # then fill in the required values
credit-demo keys >> .env  # creates a signing key for each agent
credit-demo               # then open http://127.0.0.1:8001
```

Then:

1. Click **Run the sample case**. Each step appears as the agents take it,
   and the case takes a few minutes.
1. When the case ends, wait for **Export audit trail**, then click it. It is
   ready once the records are stamped on the public record, which can take
   several minutes.
1. Drop the zip on the [Sanning verify page](https://verify.sanning.io). It
   reads **Verified**. The page checks the file in your browser and does not
   upload it. Records made with a `sanning_test_` key are sandbox records,
   and the page says so.

To verify the zip offline instead, run the published kernel on it:

```bash
npx @sanning/proof case audit-trail-CASE_ID.zip
```

Replace `CASE_ID` with the case ID from the file name. To see a failure,
unzip it, change one hex digit inside a `payload_hash` in
`packs/checker/bundle.json`, and run the same command on the unzipped folder.
It reads **Failed**.

## What is real and what is simplified

What is real:

- A real model makes every decision.
- Each agent signs its records with its own key.
- Both agents record every message that passes between them.
- The records are stamped on a public record.
- The audit trail verifies offline, without Sanning.

What is simplified for the demo:

- The documents sit in one folder per application, standing in for the
  lender's own document store.
- Both agents run on one machine, behind one small app with no login.
- There is one sample case.
- A case ends at **Sent for sign-off** or **Referred to a senior credit
  officer**. There is no human approval step.

## Using it with your own agents

Both agents are ordinary LangChain agents, built in
`src/credit_demo/common/turn.py`. The credit logic is the example's own, for
you to replace with yours: the document tools in
`src/credit_demo/common/case_tools.py` and the cover calculator in
`src/credit_demo/common/calculator.py`.

Sanning adds three pieces, wherever your agents run:

- **A recorder on each agent** signs a record of every step. See
  `src/credit_demo/common/sanning.py`.
- **Hand-off calls between the agents** record each message on both sides.
  See `src/credit_demo/underwriter/case.py` and
  `src/credit_demo/checker/web.py`.
- **A pack from each agent** holds its records for the reader, and the two
  packs travel in one zip. See `src/credit_demo/common/packs.py` and
  `src/credit_demo/underwriter/audit_trail.py`.

Record content stays in your own storage. For each record, Sanning receives a
signed hash, not the content. This content includes a full copy of each
document that an agent reads or writes.

## Explore it with an AI agent

`AGENTS.md` tells a coding agent how to install, test and run the example.
Some questions to start with:

- "Get this example running. Ask me for anything you need."
- "Explain how the two agents work a case, and where Sanning records each
  step."
- "Change one value in an audit trail and show me that verification fails."
- "Show me how to add Sanning's recorder to my own LangChain agent."
