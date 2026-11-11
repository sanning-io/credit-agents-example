# Run the credit example in your cloud, and in Live

This page covers what [README.md](README.md) leaves out: running the three services as containers or on another host, keeping the agents' content in a bucket, and recording in Live mode. Work through the README first, so that you know the example runs on your machine.

## Run it in containers

Put the agents' bucket settings in `.env` and the evidence builder's own bucket keys in `evidence-builder.env`. Then run `docker compose up --build`, and open http://127.0.0.1:8001.

## Run it on another host

Make one service for each column of the table, all from the one image, with a volume at `/app/runs` for each agent. Each service gets every setting from the host, under the names in the table, and the settings files are not used.

| Setting | Underwriter | Checker | Evidence builder |
| --- | --- | --- | --- |
| `SANNING_API_KEY` | the agents' key | the agents' key | its own key (**Build evidence**) |
| `OPENAI_API_KEY`, `MODEL` | yes | yes | not used |
| `OPENAI_BASE_URL` | if not OpenAI | if not OpenAI | not used |
| `SANNING_LOG_BUCKET`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | keys that can write | keys that can write | its own keys |
| `SANNING_AGENT_ID` | its own | its own | not used |
| `SANNING_SIGNING_SEED` | its own | its own | the evidence seed |
| `SANNING_SIGNING_KEY_KIND` | not set | not set | `organization` |
| `UNDERWRITER_AGENT_ID`, `CHECKER_AGENT_ID` | not used | not used | the agents' IDs |
| `COUNTERPARTY_PUBLIC_KEY` | the checker's public key | the underwriter's public key | not used |
| `CHECKER_URL`, `EVIDENCE_BUILDER_URL` | their addresses | not used | not used |
| `DESK_URL` | not used | the underwriter's address | not used |
| Start command | `credit-demo underwriter` | `credit-demo checker` | `credit-demo evidence-builder` |

Both agents use one bucket, each in a folder named after its agent ID, and the evidence builder has its own keys. The Quickstart lists the [AWS permissions](https://docs.sanning.io/quickstart#the-exact-aws-permissions) for an agent and for an evidence builder.

No page has a sign-in. Keep the underwriter's address private, and give the checker and the evidence builder no address outside the host's private network.

## Record in Live

Live records are evidence, and their stamps cannot be withdrawn. You need:

- A Live API key (`sanning_prod_`), made by an owner or an admin. See the [Quickstart](https://docs.sanning.io/quickstart#make-a-live-api-key).
- A bucket of its own.
- Fresh seeds: delete `underwriter.env`, `checker.env` and `evidence-builder.env`, then run `credit-demo keys` again.
- Live agent IDs, set in each agent's file and in `evidence-builder.env`.
- A Live **Build evidence** API key for the evidence builder.

## Wait for the stamps in Live

Confirmation of a stamp on the public record has taken from a few minutes to over an hour in production. The credit desk asks for the audit trail every 20 seconds and stops after 45 tries, about 15 minutes. When it stops first, the button reads **Try again**: select it to prepare the audit trail again once the stamps are confirmed.

To also check each stamp on the public record, add `--gateways https://arweave.net` to the offline command. See [Verify an evidence pack](https://docs.sanning.io/evidence/verify).
