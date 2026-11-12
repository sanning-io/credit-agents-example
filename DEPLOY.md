# Run the credit example in your cloud, and in Live

This page covers what [README.md](README.md) leaves out: running the three services as containers or on another host, keeping the agents' content in a bucket, and recording in Live mode. Work through the README first, so that you know the example runs on your machine.

## What it needs

- **Size**: a server with 4 GB of memory ran all 3 services.
- **Software**: Python 3.11 or later, or Docker Engine with the Compose and Buildx plugins. Without Buildx, Compose warns "Docker Compose is configured to build using Bake, but buildx isn't installed".
- **Podman**: on one Ubuntu server, a Podman package set `DOCKER_HOST` to a Podman socket at login, and every `docker` command failed with "Cannot connect to the Docker daemon". Run `unset DOCKER_HOST`.
- **Network**: the example only makes outbound calls: package downloads at install or build time, the Sanning API, the model service and the bucket. Nothing calls in.
- **Access**: the credit desk has no sign-in, so Compose publishes it only on `127.0.0.1:8001`. To open it from another computer, use an SSH tunnel or your own access gateway. Stop any copy that runs on your own computer first, or the tunnel can't use port 8001.
- **Bucket**: Object Lock with a default retention rule, and a set of keys of its own for the evidence builder. Uploads must be fast enough for the case: in testing, each record took 4 uploads, one after another. On a test store where an upload took about 0.5 s, the checker did not finish within the underwriter's 10-minute wait.

For the SSH tunnel, run this on your own computer and open http://127.0.0.1:8001 in its browser:

```bash
ssh -L 8001:127.0.0.1:8001 USER@SERVER
```

Replace the following:

- `USER`: your user name on the server
- `SERVER`: the server's address

## Run it in containers

Put the agents' bucket settings in `.env` and the evidence builder's own bucket settings, with keys of its own, in `evidence-builder.env`. `credit-demo keys` writes the names as commented-out lines. The evidence builder's container can't see the agents' volumes, so Compose stops it at start-up when `evidence-builder.env` sets no bucket. Then run `docker compose up --build`, and open http://127.0.0.1:8001.

## Run it on another host

Make one service for each column of the table, all from the one image, with a volume at `/app/runs` for each agent. Each service gets every setting from the host, under the names in the table, and the settings files are not used.

| Setting | Underwriter | Checker | Evidence builder |
| --- | --- | --- | --- |
| `SANNING_API_KEY` | the agents' key | the agents' key | its own key (**Build evidence**) |
| `OPENAI_API_KEY`, `MODEL` | yes | yes | not used |
| `OPENAI_BASE_URL` | if not OpenAI | if not OpenAI | not used |
| `SANNING_LOG_BUCKET`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | keys that can write | keys that can write | its own keys |
| `AWS_ENDPOINT_URL_S3` | if the bucket is not on Amazon S3 | if the bucket is not on Amazon S3 | if the bucket is not on Amazon S3 |
| `SANNING_S3_PATH_STYLE` | `true` for path-style addresses | `true` for path-style addresses | `true` for path-style addresses |
| `CREDIT_DEMO_REQUIRE_BUCKET` | not used | not used | optional: `true` stops start-up when it has no bucket |
| `SANNING_AGENT_ID` | its own | its own | not used |
| `SANNING_SIGNING_SEED` | its own | its own | the evidence seed |
| `SANNING_SIGNING_KEY_KIND` | not set | not set | `organization` |
| `UNDERWRITER_AGENT_ID`, `CHECKER_AGENT_ID` | not used | not used | the agents' IDs |
| `COUNTERPARTY_PUBLIC_KEY` | the checker's public key | the underwriter's public key | not used |
| `CHECKER_URL`, `EVIDENCE_BUILDER_URL` | their addresses | not used | not used |
| `DESK_URL` | not used | the underwriter's address | not used |
| Start command | `credit-demo underwriter` | `credit-demo checker` | `credit-demo evidence-builder` |

An evidence builder on its own host can't read the agents' local disk. Set `CREDIT_DEMO_REQUIRE_BUCKET` to `true` there, and start-up stops when it has no bucket.

Both agents use one bucket, each in a folder named after its agent ID, and the evidence builder has its own keys. The Quickstart lists the [AWS permissions](https://docs.sanning.io/quickstart#the-exact-aws-permissions) for an agent and for an evidence builder.

No page has a sign-in. Keep the underwriter's address private, and give the checker and the evidence builder no address outside the host's private network.

## Record in Live

Live records are evidence, and their stamps cannot be withdrawn. You need:

- A Live API key (`sanning_prod_`), made by an owner or an admin. See the [Quickstart](https://docs.sanning.io/quickstart#make-a-live-api-key).
- A bucket of its own, with Object Lock and a default retention rule. Without the rule, the start-up check warns that records written to it can be deleted.
- Fresh seeds: delete `underwriter.env`, `checker.env` and `evidence-builder.env`, then run `credit-demo keys` again.
- Live agent IDs, set in each agent's file and in `evidence-builder.env`.
- A Live **Build evidence** API key for the evidence builder.

## Wait for the stamps in Live

Confirmation of a stamp on the public record has taken from a few minutes to over an hour in production. The credit desk asks for the audit trail every 20 seconds and stops after 45 tries, about 15 minutes. When it stops first, the button reads **Try again**: select it to prepare the audit trail again once the stamps are confirmed.

To also check each stamp on the public record, add `--gateways https://arweave.net` to the offline command. See [Verify an evidence pack](https://docs.sanning.io/evidence/verify).
