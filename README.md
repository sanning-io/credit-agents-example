# Credit agents with a verifiable audit trail

An underwriting agent assesses a loan application, and a checker agent reviews it. Each agent signs a record of every step and message, and [Sanning](https://sanning.io) stamps the records. Anyone can verify them offline, without Sanning. The lender (Bruksvik) and the applicant (Nordvik Haulage AB) are invented.

Sanning receives signed records, never your content.

## How the agents work the case

Both agents read the same documents and use the same calculator, and nothing tells either one what to decide. The annual report includes a bad debt that may or may not be a one-off, so the checker agent may challenge the assessment. The underwriting agent then revises or holds its position, and the case ends in approval with conditions or goes to a senior credit officer.

## Getting started

### Prerequisites

- Python 3.11 or later
- A [Sanning](https://console.sanning.io) account with a confirmed email address, for the API keys
- An OpenAI-compatible model service and its API key, for a model that supports tool calls
- An S3-compatible object store, for production only (on your machine, the example uses local files)

This runs in the [Sandbox](https://docs.sanning.io/sandbox), Sanning's test mode, where records are signed for real but are not evidence.

[DEPLOY.md](DEPLOY.md) covers hosts and Live.

The example runs three services:

- The underwriter, the credit desk you open in the browser, on port 8001.
- The checker, on port 8002.
- The evidence builder, which builds the audit trail, on port 8003.

### Clone the repo

```bash
git clone https://github.com/sanning-io/credit-agents-example.git
# or
gh repo clone sanning-io/credit-agents-example
cd credit-agents-example
```

### Run the following commands

1. Install the example.

   ```bash
   python3 -m venv .venv && . .venv/bin/activate
   pip install -e .
   ```

1. Make your four settings files.

   ```bash
   cp .env.example .env
   credit-demo keys
   ```

   | File | Read by | Holds | Made by |
   | --- | --- | --- | --- |
   | `.env` | both agents | the agents' API key, the model settings, the bucket settings | you |
   | `underwriter.env`, `checker.env` | that agent | its agent ID, signing seed, the other agent's public key | `credit-demo keys` |
   | `evidence-builder.env` | the evidence builder only, never `.env` | its API key, the evidence seed, the agent IDs, and in containers or on separate hosts its own bucket settings | `credit-demo keys`, and you for the API key and the bucket keys |



   The evidence seed is the secret of the organization's evidence key, which signs the audit trail. An organization has one active evidence key. 

1. Make two Sandbox API keys, one for your Agents and one for the Evidence builder:

   - In the console, open the account menu and select **Switch to sandbox**.
   - Then open **Settings**, **API keys**, and select **Create API key**.
   - Create the 1st key for your Agents, choose **Record**, and set `SANNING_API_KEY` in `.env`.
   - Create a second key for the evidence builder, choose **Build evidence**, and set the empty `SANNING_API_KEY=` line in `evidence-builder.env`.

1. Configure your OpenAI-compatible model service in `.env`:
   - Set `OPENAI_BASE_URL` to your model service URL (see below) 
   - Set `OPENAI_API_KEY` to your model service Key

   | Service | `OPENAI_BASE_URL` | `MODEL` |
   | --- | --- | --- |
   | OpenAI | leave empty | `gpt-5-mini` |
   | Vercel AI Gateway | `https://ai-gateway.vercel.sh/v1` | `openai/gpt-5-mini` |
   | OpenRouter | `https://openrouter.ai/api/v1` | `openai/gpt-5-mini` |

1. Start the three services, the command will print any missing setting.

   ```bash
   credit-demo
   ```

### Test it in the browser

Once you've completed the steps above, the app runs at http://127.0.0.1:8001.

1. Select **Run the sample case**.

1. Export the audit trail. It takes a few minutes, while the button reads **Preparing audit trail…**. When it reads **Export audit trail** (or **Try again**), select it. The download, `audit-trail-CASE_ID.zip`, holds one evidence pack for each agent.

## Check the audit trail

1. Drop the zip on the [Sanning verify page](https://verify.sanning.io). It reads **Verified** and marks the audit trail Sandbox. The page does not upload the file.

1. Check it offline. Replace `CASE_ID` with the ID in the file name:

   ```bash
   sanning-proof case audit-trail-CASE_ID.zip
   ```

   A Sandbox audit trail exits with code 3, and a Live one with 0. The [command line reference](https://docs.sanning.io/reference/cli#exit-codes) lists every code.

   The report ends with `Witness: Not checked`. That means the command did not look up the stamps on the public record, which it does only when you give it a gateway. The verdict doesn't change. A Sandbox audit trail has no stamps on the public record to find. For a Live one, add `--gateways https://arweave.net`.

   The report also says that a record's content was removed before it was signed, for most records. It does not mean content is missing. Before the SDK signs a model or tool step, it replaces the value of any field whose name looks like a credential, such as `api_key` or `password`, with `"[scrubbed]"`. The report notes every record that went through this, whether or not anything was replaced. To see whether anything was, unzip the audit trail and search it for `[scrubbed]`. In the sample case, nothing is.

1. See a failure. Unzip it. The `packs/` folder holds one folder for each agent, named after its agent ID. Change one hex digit in a `payload_hash` in the checker's `bundle.json`, and run the same command on the unzipped folder. It reads **Failed**.

## Use it with your own agents

Both agents are ordinary LangChain agents, built in `src/credit_demo/common/turn.py`. The credit logic is the example's own, for you to replace: `src/credit_demo/common/case_tools.py` and `src/credit_demo/common/calculator.py`. Sanning adds three pieces, wherever your agents run:

- **A recorder on each agent** signs a record of every step. See `src/credit_demo/common/sanning.py`.
- **Hand-off calls between the agents** record each message on both sides. See `src/credit_demo/underwriter/case.py` and `src/credit_demo/checker/web.py`.
- **An evidence builder** zips a pack of each agent's records, sealed with the organization's evidence key. See `src/credit_demo/evidence_builder/case_file.py` and `src/credit_demo/underwriter/audit_trail.py`. It holds the organization's evidence key and no agent's seed. To enroll and revoke the key, see [how to build an evidence pack](https://docs.sanning.io/evidence/build#how-to-build-an-evidence-pack).

## What is real and what is simplified

- A real model makes every decision, and each agent signs with its own key.
- There is one sample case. It ends at **Sent for sign-off** or **Referred to a senior credit officer**, with no human approval step.
- The credit desk and documents are the example's own. In production, they are your systems.

## Explore it with an AI agent

`AGENTS.md` tells a coding agent how to run the example. Try:

- "Get this example running. Ask me for anything you need."
- "Change one value in an audit trail and show me that verification fails."
- "Show me how to add Sanning's recorder to my own LangChain agent."
