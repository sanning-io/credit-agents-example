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

## Run it yourself, in the Sandbox

Start in the Sandbox. A Sandbox API key signs your records for real, but its
stamps never reach the public record, so nothing you test counts as evidence.
The records' content stays on your disk, and you need no bucket. For how the
Sandbox differs from Live, see
[Test in the Sandbox](https://docs.sanning.io/sandbox).

You need Python 3.11 or later, a Sandbox API key and an OpenRouter API key.

1. In the [Sanning console](https://console.sanning.io), open the account menu
   and select **Switch to sandbox**. Open **Settings**, then **API keys**, and
   create a key. Leave **Verify** unchecked. The key starts with `sanning_test_`. The console
   shows it once.
1. Install the example and copy the settings file:

   ```bash
   python3 -m venv .venv && . .venv/bin/activate
   pip install -e .
   cp .env.example .env
   credit-demo keys >> .env  # one signing seed for each agent
   ```

1. In `.env`, set `SANNING_API_KEY` to the Sandbox key and
   `OPENROUTER_API_KEY` to your model key. Leave the bucket settings unset.
   `.env.example` lists every setting.
1. Start it with `credit-demo`, then open http://127.0.0.1:8001.

Each agent has its own agent ID and its own seed, and a Sandbox agent is a
separate identity from a Live one. `.env.example` gives the two Sandbox agents
the IDs `underwriter-sandbox` and `checker-sandbox`. Do not reuse a seed or an
ID between the Sandbox and Live.

Then:

1. Click **Run the sample case**. Each step appears as the agents take it,
   and the case takes a few minutes.
1. When the case ends, wait for **Export audit trail**, then click it. It is
   ready once Sanning has sealed the records, which takes a few minutes. The
   page says the records are Sandbox records.
1. Drop the zip on the [Sanning verify page](https://verify.sanning.io). It
   reads **Verified** and marks the pack as Sandbox. The page checks the file
   in your browser and does not upload it.

To verify the zip offline instead, run the published kernel on it:

```bash
npx @sanning/proof case audit-trail-CASE_ID.zip
```

Replace `CASE_ID` with the case ID from the file name. A Sandbox pack that
verifies exits with code 3, where a Live pack exits with 0. To see a failure,
unzip it, change one hex digit inside a `payload_hash` in
`packs/checker/bundle.json`, and run the same command on the unzipped folder.
It reads **Failed**.

The content of each record is in `runs/underwriter/logstore` and
`runs/checker/logstore`, and git ignores `runs/`. A folder is lost with the
machine, so it is for the Sandbox only.

## Run it in the cloud, in Live

Live records are evidence: the stamps are published to the public record and
cannot be withdrawn. Live needs the following:

- A Live API key, which starts with `sanning_prod_`. In the console, select
  **Switch to live** and create the key as in the
  [Quickstart](https://docs.sanning.io/quickstart#make-a-live-api-key).
- A fresh seed for each agent (`credit-demo keys` prints two), and Live agent
  IDs: leave the two `SANNING_AGENT_ID` settings unset, or set your own. Do not
  reuse the Sandbox seeds or IDs.
- An S3-compatible bucket with Object Lock on, and keys that can write to it:
  Amazon S3 or Cloudflare R2. The bucket holds the records' content. See
  [Set up your log store](https://docs.sanning.io/quickstart#set-up-your-log-store).

The code is the same as in the Sandbox, and the settings decide. With a bucket
set, the content goes to the bucket. With no bucket and a Sandbox key, it goes
to local disk. With no bucket and a Live key, `credit-demo` does not start, and
names the settings to add. The Dockerfile builds the image that runs it in a
container. Set the same settings as variables on your host.

A Live pack reads **Verified** and exits with code 0. To also check each stamp
on the public record, run the kernel with `--gateways`, as in
[Verify an evidence pack](https://docs.sanning.io/evidence/verify).

## What is real and what is simplified

What is real:

- A real model makes every decision.
- Each agent signs its records with its own key.
- Both agents record every message that passes between them.
- In Live, the records are stamped on a public record.
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
