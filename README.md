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

## Run it yourself, in three steps

Adopt it in this order. The code is the same at every step: the settings in
`.env` decide where the content goes and whether the records count as
evidence. `.env.example` has one block for each step.

### On your machine

A Sandbox API key signs your records for real, but its stamps never reach the
public record, so nothing you test counts as evidence. The content stays on
local disk, in `runs/*/logstore`, and you need no bucket. A disk is lost with
the machine, so use it for the Sandbox only. See
[Test in the Sandbox](https://docs.sanning.io/sandbox). You need Python 3.11 or
later and an OpenRouter API key.

1. In the [Sanning console](https://console.sanning.io), open the account menu
   and select **Switch to sandbox**. Open **Settings**, then **API keys**, and
   create a key. Leave **Verify** unchecked. The key starts with
   `sanning_test_`, and the console shows it once.
1. Install the example and copy the settings file:

   ```bash
   python3 -m venv .venv && . .venv/bin/activate
   pip install -e .
   cp .env.example .env
   credit-demo keys >> .env  # one signing seed for each agent
   ```

1. In `.env`, set `SANNING_API_KEY` to the Sandbox key and
   `OPENROUTER_API_KEY` to your model key. `OPENROUTER_BASE_URL` points it at any
   OpenAI-compatible service.
1. Start it with `credit-demo`, then open http://127.0.0.1:8001 and click
   **Run the sample case**. The case takes a few minutes. One copy runs per
   `runs/` folder: a second copy on the same folder refuses to start.
1. When the case ends, wait for **Export audit trail**, then click it. The
   page says the records are Sandbox records.
1. Drop the zip on the [Sanning verify page](https://verify.sanning.io). It
   reads **Verified** and marks the pack as Sandbox. The page checks the file
   in your browser and does not upload it.

To verify offline instead, run `npx @sanning/proof case audit-trail-CASE_ID.zip`
with the case ID from the file name. A Sandbox pack exits with code 3, a Live
pack with 0. To see a failure, unzip it, change one hex digit inside a
`payload_hash` in `packs/checker/bundle.json`, and run the command on the
unzipped folder. It reads **Failed**.

### In your cloud, as a test

Nothing recorded here is evidence, so this is the safe way to test your host,
your bucket and your keys before Live. You keep the Sandbox key, run the
example in a container and put the content in a bucket.

You need a host that runs containers, and a bucket name and keys on Amazon S3
or Cloudflare R2. The keys must be able to create and write to the bucket (see
[the exact AWS permissions](https://docs.sanning.io/quickstart#the-exact-aws-permissions)).
The Sanning SDK creates it, locked, on the first record. A bucket you made is
used as it is, and `credit-demo` warns if it lacks Object Lock, versioning or a
retention rule.

What changes: in `.env`, set `SANNING_S3_BUCKET`, `SANNING_S3_ENDPOINT`,
`SANNING_S3_ACCESS_KEY_ID` and `SANNING_S3_SECRET_ACCESS_KEY`, and for Amazon
S3 also `SANNING_S3_REGION`. The content goes to the bucket. Then build the
image from the `Dockerfile` and run it:

```bash
docker build -t credit-demo .
docker run --env-file .env -p 8001:8001 -v credit-runs:/app/runs credit-demo
```

On another host, set the same settings as variables, expose port 8001 and mount
a volume at `/app/runs`. The volume keeps each agent's cases and audit trail
zips across a restart. The records and their content are in Sanning and the
bucket. The page has no login: put something that authenticates in front of it.

### Live

Live records are evidence: the stamps are published to the public record and
cannot be withdrawn. You need a Live API key (it starts with `sanning_prod_`;
in the console, select **Switch to live** and follow the
[Quickstart](https://docs.sanning.io/quickstart#make-a-live-api-key)) and a
bucket name with keys, as in the test above.

What changes: in `.env`, set `SANNING_API_KEY` to the Live key and the bucket
settings to a bucket of its own. Replace both seeds with a fresh pair
(`credit-demo keys` prints one) and remove the two `SANNING_AGENT_ID` lines, or
set Live names of your own. A Live agent is a separate identity from a Sandbox
agent, so do not reuse a Sandbox seed or ID. Without a bucket name, a Live key
stops `credit-demo` at start-up.

A Live pack reads **Verified** and exits with code 0. To also check each stamp
on the public record, add `--gateways`, as in
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
- Each step waits for Sanning to confirm its record. Agents that must keep
  working when Sanning cannot be reached can turn on the SDK's local buffer
  (`FsRecordBuffer`), which saves each record on disk first.

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
