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

You need Python 3.11 or later, a Sandbox API key and an OpenRouter API key. You
need no bucket: the content stays in `runs/logstore`. Sandbox records are not
evidence, because their stamps never reach the public record.

```bash
git clone https://github.com/sanning-io/credit-agents-example
cd credit-agents-example
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
cp .env.example .env
credit-demo keys  # writes underwriter.env, checker.env and evidence-builder.env
credit-demo       # starts all three services
```

1. In the [Sanning console](https://console.sanning.io), switch to the sandbox
   and create an API key under **Settings**. In `.env`, set `SANNING_API_KEY`
   to it and `OPENROUTER_API_KEY` to your model key.
1. Create a second Sandbox key, check **Hand-over** and nothing else, and set
   `SANNING_API_KEY` in `evidence-builder.env` to it.
1. Open http://127.0.0.1:8001 and click **Run the sample case**. It takes a few
   minutes. When it ends, click **Export audit trail**. The audit trail can
   take several minutes to be ready.
1. Drop the zip on the [Sanning verify page](https://verify.sanning.io). It
   reads **Verified** and marks the pack as Sandbox. The page checks the file
   in your browser and does not upload it.

To verify offline, run `sanning-proof case audit-trail-CASE_ID.zip`. A Sandbox
pack exits with code 3, a Live pack with 0. To see a failure, unzip it, change
one hex digit inside a `payload_hash` in `packs/checker/bundle.json`, and run
the command on the unzipped folder. It reads **Failed**.

## In your cloud, and Live

For containers, put the agents' bucket settings in `.env` and the evidence
builder's own bucket keys in `evidence-builder.env`. Then run
`docker compose up --build`. Then open http://127.0.0.1:8001.

On another host, make one service for each column of the table, all from the
one image, with a volume at `/app/runs` for each agent.

| Setting | Underwriter | Checker | Evidence builder |
| --- | --- | --- | --- |
| `SANNING_API_KEY` | the agents' key | the agents' key | its own key (**Hand-over** alone) |
| `OPENROUTER_API_KEY`, `MODEL` | yes | yes | not used |
| `SANNING_LOG_BUCKET`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | keys that can write | keys that can write | its own keys |
| `SANNING_AGENT_ID` | its own | its own | not used |
| `SANNING_SIGNING_SEED` | its own | its own | the hand-over seed |
| `SANNING_SIGNING_KEY_KIND` | not set | not set | `organization` |
| `UNDERWRITER_AGENT_ID`, `CHECKER_AGENT_ID` | not used | not used | the agents' IDs |
| `COUNTERPARTY_PUBLIC_KEY` | the checker's public key | the underwriter's public key | not used |
| `CHECKER_URL`, `EVIDENCE_BUILDER_URL` | their addresses | not used | not used |
| `DESK_URL` | not used | the underwriter's address | not used |
| Start command | `credit-demo underwriter` | `credit-demo checker` | `credit-demo evidence-builder` |

Both agents use one bucket, each in a folder named after its agent ID, and the
evidence builder has its own keys. The Quickstart lists the
[AWS permissions](https://docs.sanning.io/quickstart#the-exact-aws-permissions)
for an agent and for an evidence builder.

No page has a sign-in. Keep the underwriter's address private, and give the
checker and the evidence builder no address outside the host's private network.

**Live.** Live records are evidence, and their stamps cannot be withdrawn. You
need:

- A Live API key (`sanning_prod_`), made by an owner or an admin. See the
  [Quickstart](https://docs.sanning.io/quickstart#make-a-live-api-key).
- A bucket of its own.
- Fresh seeds: delete `underwriter.env`, `checker.env` and
  `evidence-builder.env`, then run `credit-demo keys` again.
- Live agent IDs, set in each agent's file and in `evidence-builder.env`.
- A Live **Hand-over** key for the evidence builder.

In Live, the audit trail can take up to 15 minutes while the stamps reach the
public record. To also check each stamp there, add
`--gateways https://arweave.net` to the offline command. See
[Verify an evidence pack](https://docs.sanning.io/evidence/verify).

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
- **An evidence builder** builds the case file: a pack of each agent's
  records, sealed with the organization's hand-over key, in one zip. See
  `src/credit_demo/evidence_builder/case_file.py`. The credit desk asks it for
  the file in `src/credit_demo/underwriter/audit_trail.py`.
  The evidence builder holds the organization's hand-over key and no agent's
  seed. To enroll and revoke that key, see
  [how to build an evidence pack](https://docs.sanning.io/evidence/build#how-to-build-an-evidence-pack).

Record content stays in your own storage. For each record, Sanning receives a
signed hash, not the content. This content includes a full copy of each
document that an agent reads or writes.

## What is real and what is simplified

- A real model makes every decision, and each agent signs its records with its
  own key.
- In Live, the stamps are on a public record, and the audit trail verifies
  offline, without Sanning.
- There is one sample case. It ends at **Sent for sign-off** or **Referred to a
  senior credit officer**, with no human approval step.
- The credit desk and the documents are part of the example. In production,
  they are your own systems.

## Explore it with an AI agent

`AGENTS.md` tells a coding agent how to install, test and run the example.
Some questions to start with:

- "Get this example running. Ask me for anything you need."
- "Explain how the two agents work a case, and where Sanning records each
  step."
- "Change one value in an audit trail and show me that verification fails."
- "Show me how to add Sanning's recorder to my own LangChain agent."
