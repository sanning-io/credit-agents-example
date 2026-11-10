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

## Run it yourself: three setups

Start with the first. Each setup changes a few settings from the one before.
The code is the same in all three: the settings in `.env` decide where the
content goes and whether the records count as evidence. `.env.example` has one
block for each setup. Each agent runs on its own and reads the settings under
the names in the Sanning
[Quickstart](https://docs.sanning.io/quickstart#set-up-your-log-store).

The demo runs on macOS and Linux. On Windows, use WSL.

### On your machine

A Sandbox API key signs your records for real, but its stamps never reach the
public record, so nothing you test counts as evidence. The content stays on
local disk, in `runs/logstore`, with a folder for each agent, and you need no
bucket. A disk is lost with the machine, so use it for the Sandbox only. See
[Test in the Sandbox](https://docs.sanning.io/sandbox). You need Python 3.11 or
later and an OpenRouter API key.

1. In the [Sanning console](https://console.sanning.io), open the account menu
   and select **Switch to sandbox**. Open **Settings**, then **API keys**, and
   create a key. Leave **Verify** unchecked. The key starts with
   `sanning_test_`, and the console shows it once.
1. Clone the example, install it and copy the settings file:

   ```bash
   git clone https://github.com/sanning-io/credit-agents-example
   cd credit-agents-example
   python3 -m venv .venv && . .venv/bin/activate
   pip install -e .
   cp .env.example .env
   credit-demo keys  # writes underwriter.env and checker.env
   ```

   `.env` holds the settings both agents use. `underwriter.env` and
   `checker.env` hold each agent's own: its ID, its signing seed and the other
   agent's public key. Git ignores all three.

1. In `.env`, set `SANNING_API_KEY` to the Sandbox key and
   `OPENROUTER_API_KEY` to your model key.
1. Start both agents with `credit-demo`, then open http://127.0.0.1:8001 and
   click **Run the sample case**. The case takes a few minutes.
1. When the case ends, wait for **Export audit trail**, then click it. The
   audit trail can take up to 15 minutes to be ready. The page says the
   records are Sandbox records.
1. Drop the zip on the [Sanning verify page](https://verify.sanning.io). It
   reads **Verified** and marks the pack as Sandbox. The page checks the file
   in your browser and does not upload it.

To verify offline instead, run `sanning-proof case audit-trail-CASE_ID.zip`
with the case ID from the file name. A Sandbox pack exits with code 3, a Live
pack with 0. To see a failure, unzip it, change one hex digit inside a
`payload_hash` in `packs/checker/bundle.json`, and run the command on the
unzipped folder. It reads **Failed**.

### In your cloud, as a test

Nothing recorded here is evidence, so this is the safe way to test your host,
your bucket and your keys before Live. You keep the Sandbox key, run the
example in a container and put the content in a bucket.

Sanning supports AWS S3 today. Other providers are on the roadmap.

You need a host that runs containers, and a bucket name and keys on Amazon S3.
The keys must be able to create and write to the bucket (see
[the exact AWS permissions](https://docs.sanning.io/quickstart#the-exact-aws-permissions)).
The SDK creates the bucket, locked, on the first record, but only in
`us-east-1`. In another region, create the bucket yourself with Object Lock
on, before the first record. A bucket you made is used as it is, and
`credit-demo` warns if it lacks Object Lock, versioning or a retention rule.

What changes: in `.env`, set `SANNING_LOG_BUCKET`, `AWS_REGION`,
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`. These are the names in the
Sanning [Quickstart](https://docs.sanning.io/quickstart#set-up-your-log-store).
Both agents use the one bucket, and each writes under a folder named after its
agent ID, so give the two agents different IDs.

Each agent runs as its own container, from one image. The start command picks
the agent: `credit-demo underwriter` (the image's default) or
`credit-demo checker`. Each tests its own settings, then starts. `compose.yaml`
describes the two services and their private network, so this one command
builds the image and starts both:

```bash
docker compose up --build
```

Then open http://127.0.0.1:8001. Compose publishes only the underwriter's
port, and only on your own machine. The checker has no published port: the
underwriter reaches it by its service name on the compose network. Each agent
has a volume for its working files.

On another host, make one service for each agent from the same image. Give
each the settings in the table, and mount a volume at `/app/runs`. The volume
keeps the agent's cases and audit trail zips across a restart. The records and
their content are in Sanning and the bucket.

| Setting | Underwriter | Checker |
| --- | --- | --- |
| `SANNING_API_KEY`, `OPENROUTER_API_KEY`, `MODEL`, the bucket settings | yes | yes |
| `SANNING_AGENT_ID`, `SANNING_SIGNING_SEED` | its own | its own |
| `COUNTERPARTY_PUBLIC_KEY` | the checker's `public_key` | the underwriter's `public_key` |
| `CHECKER_URL` | the checker's private address | not used |
| `DESK_URL` | not used | the underwriter's private address |
| Start command | `credit-demo underwriter` | `credit-demo checker` |

`credit-demo keys` prints both public keys. Neither page has a sign-in. Keep
the underwriter's address private, or put a sign-in in front of it: anyone who
can reach it can run cases on your model key. Give the checker no address
outside the host's private network, so that only the underwriter reaches it.
The image listens on every interface, over IPv4 and IPv6, so the address is
what keeps each one private.

### Live

Live records are evidence: the stamps are published to the public record and
cannot be withdrawn. You need a Live API key and a bucket with keys, as in the
test above. The key starts with `sanning_prod_`. Only an owner or an admin can
make a Live key: in the console, select **Switch to live** and follow the
[Quickstart](https://docs.sanning.io/quickstart#make-a-live-api-key).

What changes: in `.env`, set `SANNING_API_KEY` to the Live key and the bucket
settings to a bucket of its own. A Live agent is a separate identity from a
Sandbox agent, so do not reuse a Sandbox seed or ID:

1. Delete `underwriter.env` and `checker.env`, then run `credit-demo keys`
   again. It writes a fresh pair of seeds and the public keys that go with them.
1. In each of the two files, set `SANNING_AGENT_ID` to a Live name of your own.

Without a bucket name, a Live key stops `credit-demo` at start-up.

A Live pack reads **Verified** and exits with code 0. To also check each stamp
on the public record, run
`sanning-proof case audit-trail-CASE_ID.zip --gateways https://arweave.net`,
or use any other Arweave gateway. See
[Verify an evidence pack](https://docs.sanning.io/evidence/verify).

## What is real and what is simplified

What is real:

- A real model makes every decision.
- Each agent signs its records with its own key.
- Both agents record every message that passes between them.
- In Live, the records are stamped on a public record.
- The audit trail verifies offline, without Sanning.

What is simplified for the demo:

- On your machine, one command starts both agents. In your cloud, each is its
  own container.
- The page has no sign-in. Anyone who can reach it can run cases on your model
  key. Keep it on your machine, or put a sign-in in front of it.
- There is one sample case.
- A case ends at **Sent for sign-off** or **Referred to a senior credit
  officer**. There is no human approval step.
- Each step waits for Sanning to confirm its record. Agents that must keep
  working when Sanning cannot be reached can turn on the SDK's local buffer
  (`FsRecordBuffer`), which saves each record on disk first.

## What this example simplifies

- **The credit desk runs inside the underwriter's service.** The credit desk
  is the case system that opens a case and keeps the list of its documents.
  Here it is part of the underwriter, not a system of its own.
- **The case documents are files inside the image.** There is no document
  store.

In production, both are the company's own systems. The checker reads them with
its own access, by the case ID, and never from a location the underwriting
agent sends.

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
