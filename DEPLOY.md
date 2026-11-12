# Run the credit example on a server

Run the README on your own machine first. This page moves that copy to a server with Docker.

## What you need

- **A server**: 4 GB of memory, and Docker Engine with the Compose and Buildx plugins
- **A bucket**: S3-compatible, with Object Lock.
- **Your settings files**: the four files you made in the README

## Run it on a server with Docker

1. Clone the repo on the server, as in the README.

1. From your own computer, copy your four settings files into the repo folder on the server:

   ```bash
   scp .env underwriter.env checker.env evidence-builder.env user_name@server_address:credit-agents-example/
   ```

   Replace `user_name` with your user name on the server, and `server_address` with the server's address.

1. Fill in the bucket settings in `.env`. Fill in the same settings in `evidence-builder.env`, with the evidence builder's own keys.

1. Start the three services, then read their start-up checks:

   ```bash
   docker compose up --build -d
   docker compose logs
   ```

1. On your own computer, open an SSH tunnel to the server, then open http://127.0.0.1:8001. Stop any copy that runs on your own computer first, or the tunnel can't use port 8001.

   ```bash
   ssh -L 8001:127.0.0.1:8001 user_name@server_address
   ```

## Run it on another platform

Run three services from the one image. Set each one's settings on the platform, with the same names and values as in its files:

| Service | Settings | Start command |
| --- | --- | --- |
| Underwriter | `.env` and `underwriter.env`, plus `CHECKER_URL` and `EVIDENCE_BUILDER_URL` | `credit-demo underwriter` |
| Checker | `.env` and `checker.env`, plus `DESK_URL` | `credit-demo checker` |
| Evidence builder | `evidence-builder.env` only, plus `CREDIT_DEMO_REQUIRE_BUCKET=true` | `credit-demo evidence-builder` |

Give each agent a volume at `/app/runs`. `compose.yaml` shows the same layout. No page has a sign-in, so keep all three services on a private network.

## Record in Live

Live records are evidence, and their stamps can't be withdrawn. You need:

- **Two Live API keys**: one with **Record** for the agents, and one with **Build evidence** for the evidence builder. An owner or an admin makes them. See [Make a Live API key](https://docs.sanning.io/quickstart#make-a-live-api-key).
- **A bucket of its own**: with Object Lock and a default retention rule. Without the rule, the start-up check warns that records written to it can be deleted.
- **Fresh seeds and agent IDs**: delete `underwriter.env`, `checker.env` and `evidence-builder.env`, run `credit-demo keys` again, then set Live agent IDs in all three files.

A Live stamp takes from a few minutes to over an hour to confirm. If the credit desk stops waiting first, the export button reads **Try again**. Select it once the stamps are confirmed.
