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

2. **Ask the person for three things,** all at once, saying what each is for.
   The person creates these accounts; you cannot.
   - **A Sanning API key** with Anchor access, from console.sanning.io
     (Settings, API keys). It records each step.
   - **An OpenRouter API key.** It pays for the model, a small amount per
     case.
   - **An S3-compatible bucket**, created with Object Lock on, and keys that
     can write to it: Amazon S3 or Cloudflare R2. It holds the records'
     content, which never goes to Sanning. Sanning's guide:
     https://docs.sanning.io/guides/bucket.md.

   Done when the person has all three.

3. **Fill in `.env`.**

   ```bash
   cp .env.example .env
   .venv/bin/credit-demo keys >> .env   # a signing key for each agent
   ```

   `.env.example` says what each setting takes. Keys live in `.env` and
   nowhere else: git ignores it. Done when every required value is set.

4. **Start it** with `.venv/bin/credit-demo`. Before anything starts, it
   tests each key, the bucket and the model, and stops naming the setting to
   change. A warning about Object Lock, versioning or retention does not stop
   it. Done when `curl -s http://127.0.0.1:8001/health` answers.

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
   a case made with a `sanning_test_` key (Verified with sandbox evidence).
   Exit code 1 is Failed, 2 is Incomplete (the records are not stamped yet, so
   try again later) and 4 means the check could not run. The person can also drop the zip on
   https://verify.sanning.io, which checks it in the browser. `README.md`
   shows how to make it read **Failed**.

## Reading and changing the code

- `README.md` names the files to read first: the three pieces Sanning adds,
  and the credit logic that is the example's own, for you to replace.
- To add Sanning to an agent of your own, start from
  https://docs.sanning.io/guides/langchain.md and compare it with
  `src/credit_demo/common/turn.py`.
- A real case costs model credit, so reach for one only when the tests
  cannot show what you need.
- `tests/test_public_surface.py` keeps this public repo free of issue
  numbers and private references. In your own copy, delete it if it gets in
  the way.
- Both services listen on 127.0.0.1, and no endpoint asks for a login. Keep
  them there, unless something that authenticates sits in front.
- The lender, the applicant and their documents are made up.
