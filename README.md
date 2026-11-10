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

## The three services

Three services run from one image. The two agents sign their own records and
build no pack and no case file. A third service, the evidence builder, builds
the case file once a case closes.

| Service | What it does | What it holds |
| --- | --- | --- |
| Underwriter | Assesses the application. Its service also runs the credit desk. | Its API key, signing seed and model key; the checker's public key; bucket keys that can write. |
| Checker | Reviews the assessment. | The same kinds of setting, all its own; the underwriter's public key. |
| Evidence builder | Builds the case file for an auditor. | The organization's hand-over seed, its own API key, its own bucket keys, and the two agents' IDs. No agent's seed and no model key. |

In a real company, the compliance team builds the case file from the one log
store with the organization's hand-over key, and holds no agent's seed. The
evidence builder does the same: it seals both agents' packs with the hand-over
key, and each record stays signed by its own agent. The first case file that
reads Verified enrolls the hand-over key with Sanning, which emails every owner
of your organization. If the hand-over seed leaks, an owner or admin revokes the
hand-over key on the console's Signing keys page. Then make another seed
(`openssl rand -hex 32`), put it in `SANNING_SIGNING_SEED` in
`evidence-builder.env`, and restart the evidence builder. Sanning never enrolls
a revoked key again. If a different hand-over key is already active, the
evidence builder builds nothing and says so.

The credit desk stands in for your case system. In your setup, your case system or a job asks the evidence builder when a case closes. Agents never ask for case files.

The evidence builder does all its work while a call is open: each call builds
the case file once and replies with the file, or with "try again" while the
records are not stamped. It keeps nothing between calls. A host can stop it
when idle and start it on the next call, or leave it running. The credit desk
asks again when a call fails or the records are not ready.

## Run it yourself: three setups

Start with the first. Each setup changes a few settings from the one before.
The code is the same in all three: the settings decide where the content
goes and whether the records count as evidence. `.env.example` has one
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
   credit-demo keys  # writes underwriter.env, checker.env and evidence-builder.env
   ```

   `.env` holds the settings both agents use. `underwriter.env` and
   `checker.env` hold each agent's own: its ID, its signing seed and the other
   agent's public key. `evidence-builder.env` holds the evidence builder's
   own: the hand-over seed, the two agents' IDs and its own API key. The
   evidence builder never reads `.env`. Git ignores all four.

1. In `.env`, set `SANNING_API_KEY` to the Sandbox key and
   `OPENROUTER_API_KEY` to your model key.
1. Create a second Sandbox key for the evidence builder, and set
   `SANNING_API_KEY` in `evidence-builder.env` to it. In the console's Create
   key dialog, check **Hand-over** and nothing else: **Hand-over** replaces
   **Anchor** and cannot be combined with **Verify**. It is the one choice the
   evidence builder needs, because the key it makes holds `handover:enroll`,
   `anchor:read` and `anchor:write`: it enrolls the hand-over key (with the first case file that reads Verified), reads
   Sanning's index and anchors the declarations. The agents' key, made with
   **Anchor**, cannot enroll a hand-over key. The hand-over seed is already in
   `evidence-builder.env`. To make one yourself, run `openssl rand -hex 32`, as
   for an agent's seed.
1. Start all three services with `credit-demo`, then open
   http://127.0.0.1:8001 and click **Run the sample case**. The case takes a
   few minutes.
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
The SDK reads a record's pointer object before it writes the next one, so add
`s3:GetObject` on `arn:aws:s3:::BUCKET/*` to that list. Without it, every
record fails with AccessDenied.
The SDK creates the bucket, locked, on the first record, in the Region from
`AWS_REGION`. A bucket you made is used as it is, and `credit-demo` warns if
it lacks Object Lock, versioning or a retention rule.

What changes: in `.env`, set `SANNING_LOG_BUCKET`, `AWS_REGION`,
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`. These are the names in the
Sanning [Quickstart](https://docs.sanning.io/quickstart#set-up-your-log-store).
Recording and `bundle` both read them from the container, so nothing is copied
from one name to another. Set `AWS_ENDPOINT_URL_S3` only for a bucket that is
not on Amazon S3.
Both agents use the one bucket, and each writes under a folder named after its
agent ID, so give the two agents different IDs.

The evidence builder reads the same bucket with keys of its own, in
`evidence-builder.env` and never in `.env`. They need `s3:GetObject` and
`s3:PutObject` on `arn:aws:s3:::BUCKET/*`, and `s3:ListBucket` on
`arn:aws:s3:::BUCKET`, and nothing more. It reads each agent's records, and
adds the declarations it signs to each agent's folder. It never creates the
bucket. Set `UNDERWRITER_AGENT_ID` and `CHECKER_AGENT_ID` in the same file to
the two agents' IDs.

Each service runs as its own container, from one image. The start command
picks the service: `credit-demo underwriter` (the image's default),
`credit-demo checker` or `credit-demo evidence-builder`. Each tests its own
settings, then starts. `compose.yaml` describes the three services and their
private network, so this one command builds the image and starts all three:

```bash
docker compose up --build
```

Then open http://127.0.0.1:8001. Compose publishes only the underwriter's
port, and only on your own machine. The checker and the evidence builder have
no published port: the underwriter reaches each by its service name on the
compose network. Compose gives the evidence builder `evidence-builder.env` and
never `.env`. Each agent has a volume for its working files. The evidence
builder keeps nothing between calls, so it has none.

On another host, make one service for each from the same image. Give each the
settings in the table, and mount a volume at `/app/runs` for each agent. The
volume keeps the agent's cases and audit trail zips across a restart. The
records and their content are in Sanning and the bucket.

| Setting | Underwriter | Checker | Evidence builder |
| --- | --- | --- | --- |
| `SANNING_API_KEY` | the agents' key (**Anchor**) | the agents' key (**Anchor**) | its own key, made with **Hand-over** alone |
| `OPENROUTER_API_KEY`, `MODEL` | yes | yes | not used |
| Bucket settings | keys that can write | keys that can write | its own keys: get, put, list |
| `SANNING_AGENT_ID` | its own | its own | not used |
| `SANNING_SIGNING_SEED` | its own | its own | the hand-over seed |
| `SANNING_SIGNING_KEY_KIND` | not set | not set | `organization` |
| `UNDERWRITER_AGENT_ID`, `CHECKER_AGENT_ID` | not used | not used | the two agents' IDs |
| `COUNTERPARTY_PUBLIC_KEY` | the checker's `public_key` | the underwriter's `public_key` | not used |
| `CHECKER_URL` | the checker's private address | not used | not used |
| `EVIDENCE_BUILDER_URL` | the evidence builder's private address | not used | not used |
| `DESK_URL` | not used | the underwriter's private address | not used |
| Start command | `credit-demo underwriter` | `credit-demo checker` | `credit-demo evidence-builder` |

`credit-demo keys` prints the public keys. Make the hand-over seed with
`openssl rand -hex 32`, as for an agent's seed. An agent refuses to start with
`SANNING_SIGNING_KEY_KIND=organization`, and the evidence builder refuses to
start without it, so no agent holds the hand-over seed.

No page or endpoint has a sign-in. Keep the underwriter's address private, or
put a sign-in in front of it: anyone who can reach it can run cases on your
model key. Give the checker and the evidence builder no address outside the
host's private network, so that only the underwriter reaches them. The image
listens on every interface, over IPv4 and IPv6, so the address is what keeps
each one private.

### Live

Live records are evidence: the stamps are published to the public record and
cannot be withdrawn. You need a Live API key and a bucket with keys, as in the
test above. The key starts with `sanning_prod_`. Only an owner or an admin can
make a Live key: in the console, select **Switch to live** and follow the
[Quickstart](https://docs.sanning.io/quickstart#make-a-live-api-key).

What changes: in `.env`, set `SANNING_API_KEY` to the Live key and the bucket
settings to a bucket of its own. A Live agent is a separate identity from a
Sandbox agent, so do not reuse a Sandbox seed or ID:

1. Delete `underwriter.env`, `checker.env` and `evidence-builder.env`, then run
   `credit-demo keys` again. It writes a fresh pair of seeds, a fresh
   hand-over seed and the public keys that go with them.
1. In each agent's file, set `SANNING_AGENT_ID` to a Live name of your own.
   In `evidence-builder.env`, set `UNDERWRITER_AGENT_ID` and
   `CHECKER_AGENT_ID` to the same two names, and `SANNING_API_KEY` to a Live
   key of its own, made with **Hand-over** alone. Its first case file that reads
   Verified enrolls the Live hand-over key.

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

- On your machine, one command starts all three services. In your cloud, each
  is its own container.
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
- **An evidence builder** builds the case file: a pack of each agent's
  records, sealed with the organization's hand-over key, in one zip. See
  `src/credit_demo/evidence_builder/case_file.py`. The credit desk asks it for
  the file in `src/credit_demo/underwriter/audit_trail.py`.

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
