# AGENTS.md

This file tells a coding agent how to install, test and run this example, and
where to read or change it. `README.md` explains what the example shows. For
Sanning itself (the SDK, keys, buckets, verifying), read
https://docs.sanning.io/llms.txt.

The lender, the applicant and their documents are made up.

## Get it running

Work through the steps in order. A step is done when its check passes.

1. **Install and run the tests.** You need Python 3.11 or later.

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -e ".[dev]"
   .venv/bin/pytest -q
   ```

   Done when every test passes. The tests need no keys and call no model.

2. **Ask the person for what they need,** all at once, and say what each is
   for. You cannot create these accounts. Start in the Sandbox, the mode for
   testing: https://docs.sanning.io/sandbox.md.
   - **A Sandbox API key** (`sanning_test_`) with Anchor access, from
     console.sanning.io (Settings, API keys, in Sandbox mode).
   - **A model API key** for any OpenAI-compatible service. It pays for the
     model. For a service other than OpenAI, also its address, for
     `OPENAI_BASE_URL`. The default `MODEL`, `openai/gpt-5-mini`, is a gateway
     name: it works on the Vercel AI Gateway and OpenRouter, and with OpenAI's
     own address (an empty `OPENAI_BASE_URL`) it must be `gpt-5-mini`.
   - **A second Sanning API key for the evidence builder,** with
     **Build evidence** chosen as its permission. Never reuse the agents' key.

   A Sandbox key with no bucket keeps each agent's content on local disk, in
   `runs/logstore/AGENT_ID`. A Live key (`sanning_prod_`) needs a bucket and
   its keys, and start-up stops without one. `DEPLOY.md` covers buckets,
   containers and Live. The bucket permissions are in
   https://docs.sanning.io/quickstart.md. The evidence builder's own bucket
   keys need `s3:GetObject` and `s3:PutObject` on the bucket's objects and
   `s3:ListBucket` on the bucket. A Sandbox agent and a Live agent are
   separate identities: use a different seed and agent ID for each.

   Done when the person has the keys.

3. **Fill in the settings files.**

   ```bash
   cp .env.example .env
   .venv/bin/credit-demo keys
   ```

   `.env` holds the settings both agents use. `keys` writes `underwriter.env`
   and `checker.env` (each agent's ID, signing seed and the other agent's
   public key) and `evidence-builder.env` (the evidence seed, the two agents'
   IDs, an empty `SANNING_API_KEY=` line for you to fill with the evidence
   builder's own key, and commented-out bucket settings, for containers and
   hosts, to fill with the evidence builder's own bucket keys). Each agent's ID
   ends in a random part that both IDs share, so that a second copy in the same
   Sandbox does not enroll the same IDs under other seeds. The evidence builder
   never reads `.env`, so never copy a value from `.env` into
   `evidence-builder.env`. An agent's own file wins over `.env`, and a setting
   in the shell wins over every file: unset
   `SANNING_API_KEY` in the shell if another project set it. The table in
   `README.md` lists what each file holds, and `.env.example` lists the
   settings in `.env`. Git ignores all four files.

   Done when every required value is set.

4. **Start it.**

   ```bash
   .venv/bin/credit-demo
   ```

   This runs the underwriter, the checker and the evidence builder as three
   processes. Before it starts them, it tests each key, the bucket (when one is
   set) and the model, and stops with the setting to change. To run one
   service alone, as a host does, use `credit-demo underwriter`,
   `credit-demo checker` or `credit-demo evidence-builder`.

   Done when `curl -s http://127.0.0.1:8001/health` answers.

5. **Run the sample case.** In a browser, click **Run the sample case** at
   http://127.0.0.1:8001. Or:

   ```bash
   curl -s -o /dev/null -w '%{redirect_url}\n' -X POST http://127.0.0.1:8001/cases
   ```

   This prints the case's address. Its last part is the case ID. Follow the
   case in `runs/underwriter/CASE_ID/case.json`:
   - `status` goes from `running` to `concluded` in a few minutes, or to
     `stopped` with the reason in `error`.
   - Then `audit_trail_state` goes from `preparing` to `ready`, or to `failed`
     with the reason in `audit_trail_error`. The credit desk asks the evidence
     builder for the case file when the case closes, and asks again until the
     records are stamped. In Live that can take several minutes more.

   Done when `audit_trail_state` is `ready`. A real case costs model credit.

6. **Verify the audit trail.**

   ```bash
   curl -s -O -J http://127.0.0.1:8001/cases/CASE_ID/audit-trail
   .venv/bin/sanning-proof case audit-trail-CASE_ID.zip
   ```

   Exit code 0 is Verified. Exit code 3 is Verified for a Sandbox case, which
   is not evidence. Exit code 1 is Failed, 2 is Incomplete (the records are
   not stamped yet, so try again later) and 4 means the check could not run.
   The person can also drop the zip on https://verify.sanning.io. `README.md`
   shows how to make it read **Failed**.

## Reading and changing the code

- `README.md` names the files to read first: the three pieces Sanning adds,
  and the credit logic that is the example's own, for you to replace.
- To add Sanning to an agent of your own, start from
  https://docs.sanning.io/adapters/langchain.md and compare it with
  `src/credit_demo/common/turn.py`.
- Agents never build packs or case files. The evidence builder
  (`src/credit_demo/evidence_builder/`) does, in one `bundle --case` command
  with one `--agent` and one `--logs` for each agent, sealed with the evidence
  key (`SANNING_SIGNING_KEY_KIND=organization`). Start-up refuses an agent
  started with that kind, and an evidence builder started without it. In your
  own setup, a case system or a job asks the evidence builder when a case
  closes.
- The evidence builder does all its work while a call is open and keeps
  nothing after it replies. Add no background threads to it.
- No endpoint takes credentials. On a laptop all three services listen on
  127.0.0.1: keep them there unless something that authenticates sits in
  front. In a container they listen on every interface, so keep the
  underwriter's address private, and give the checker and the evidence builder
  no address outside the host's private network.
- Each agent reads the Sanning Quickstart's setting names
  (`SANNING_SIGNING_SEED`, `SANNING_LOG_BUCKET` and so on).
  `src/credit_demo/common/config.py` lists them.
- `tests/test_public_surface.py` keeps issue numbers and private references
  out of this public repo. Delete it from your own copy if it gets in the way.

## Changing this repo

This repo is public, so a branch name, commit message, pull request, comment
or review here is published. Write each one in this repo's own words:
describe the change, use the example's made-up names, and link only to pages
anyone can open. The one exception is a Linear issue: link it by its short
link, `https://linear.app/sanning-io/issue/SAN-123`, which carries no title.
