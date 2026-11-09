# AGENTS.md

This repo is a working example: two LangChain agents work a loan application,
and Sanning records every step so that anyone can verify the records offline.
`README.md` explains what it shows. This file is for an AI coding agent
helping someone run the example, read it or adapt it.

For Sanning itself (the SDK, keys, the bucket, verifying), read
https://docs.sanning.io/llms.txt, an index of Sanning's documentation for
agents.

## Get it running

Work through the steps in order. Each step is done when its check passes.

1. **Install and run the tests.** You need Python 3.11 or later. Every
   command here calls the tools in `.venv/bin/` directly, so it works in a
   shell that has not activated the environment.

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -e ".[dev]"
   .venv/bin/pytest -q
   ```

   Done when every test passes. The tests need no keys, call no model and
   cost nothing, so they prove the install before anyone signs up for
   anything.

2. **Ask the person for what they need,** all at once, saying what each is
   for. The person creates these accounts; you cannot. Start in the Sandbox,
   the mode for testing: https://docs.sanning.io/sandbox.md.
   - **A Sandbox API key** (`sanning_test_`) with Anchor access, from
     console.sanning.io (account menu, Switch to sandbox, then Settings, API
     keys). It records each step, and its records are never evidence.
   - **An OpenRouter API key.** It pays for the model, a small amount per
     case.

   With a Sandbox key and no bucket, each agent keeps its content on local
   disk, in `runs/logstore/AGENT_ID`. A Live key (`sanning_prod_`) also needs
   **a bucket** name and keys that can create and write to it. Sanning
   supports AWS S3 today; other providers are on the roadmap. Both agents use
   the one bucket, and each writes under a folder named after its agent ID.
   In `us-east-1`, a bucket that does not exist yet is fine: the SDK creates
   it, locked, on the agent's first record. In any other region, create it
   yourself with Object Lock on before the first record. Sanning's guide:
   https://docs.sanning.io/quickstart.md. Without a bucket setting, a Live key
   stops the start-up. A Sandbox agent and a Live agent are separate
   identities: use a different seed and agent ID for each.

   Done when the person has the two keys.

3. **Fill in `.env`.**

   ```bash
   cp .env.example .env
   .venv/bin/credit-demo keys   # writes underwriter.env and checker.env
   ```

   `.env` holds the settings both agents use. `underwriter.env` and
   `checker.env` hold each agent's own: its ID, its signing seed and the
   other agent's public key. `.env.example` says what each setting takes.
   Keys live in these three files and nowhere else: git ignores them. Done
   when every required value is set.

4. **Start it** with `.venv/bin/credit-demo`, which runs both agents, each as
   its own process. Before anything starts, it tests each key, the bucket
   (when one is set) and the model, and stops naming the setting to change. A
   warning about Object Lock, versioning or retention does not stop it. To run
   one agent alone, as a host does, use `credit-demo underwriter` or
   `credit-demo checker`. Done when `curl -s http://127.0.0.1:8001/health`
   answers.

5. **Run the sample case.** In a browser, click **Run the sample case** at
   http://127.0.0.1:8001. Without one:

   ```bash
   curl -s -o /dev/null -w '%{redirect_url}\n' -X POST http://127.0.0.1:8001/cases
   ```

   This prints the case's address, and its last part is the case ID. Follow
   the case in `runs/underwriter/CASE_ID/case.json`:
   - `status` goes from `running` to `concluded` in a few minutes, or to
     `stopped` with the reason in `error`.
   - Then `audit_trail_state` goes from `preparing` to `ready`, or to `failed`
     with the reason in `audit_trail_error`. It waits for the records to be
     stamped on the public record, which can take several minutes more.

   Done when `audit_trail_state` is `ready`.

6. **Verify the audit trail.**

   ```bash
   curl -s -O -J http://127.0.0.1:8001/cases/CASE_ID/audit-trail
   .venv/bin/sanning-proof case audit-trail-CASE_ID.zip
   ```

   Done when it reads **Verified**, which is exit code 0, or exit code 3 for
   a case made with a `sanning_test_` key (Verified, marked Sandbox: not evidence).
   Exit code 1 is Failed, 2 is Incomplete (the records are not stamped yet, so
   try again later) and 4 means the check could not run. The person can also drop the zip on
   https://verify.sanning.io, which checks it in the browser. `README.md`
   shows how to make it read **Failed**.

## Reading and changing the code

- `README.md` names the files to read first: the three pieces Sanning adds,
  and the credit logic that is the example's own, for you to replace.
- To add Sanning to an agent of your own, start from
  https://docs.sanning.io/adapters/langchain.md and compare it with
  `src/credit_demo/common/turn.py`.
- A real case costs model credit, so reach for one only when the tests
  cannot show what you need.
- `tests/test_public_surface.py` keeps this public repo free of issue
  numbers and private references. In your own copy, delete it if it gets in
  the way.
- Both services listen on 127.0.0.1 on a laptop, and no endpoint asks for a
  login. Keep them there, unless something that authenticates sits in front.
  In a container they listen on every interface, so the checker must have no
  public address: only the underwriter reaches it, over the host's private
  network.
- Each agent reads the Sanning Quickstart's setting names
  (`SANNING_SIGNING_SEED`, `SANNING_LOG_BUCKET` and so on), with no agent name
  in them. `src/credit_demo/common/config.py` lists them.
- The lender, the applicant and their documents are made up.
