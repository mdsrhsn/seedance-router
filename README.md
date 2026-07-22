# Seedance Router

Central management for multiple paid BytePlus ModelArk accounts. Add each account's
API key once, then queue generations without deciding which account runs them.
The router picks a key, and moves to the next one when a key hits its rate limit,
runs out of balance, or stops responding.

## Files

| File | Does what |
|---|---|
| `app.py` | Dashboard, credential CRUD, model catalogue, job queueing |
| `worker.py` | Background loop: dispatches queued jobs, polls running ones |
| `router.py` | Picks the key, handles failures, manages cooldowns |
| `modelark.py` | The only file that knows the BytePlus wire format |
| `models.py` | Database tables |
| `crypto_util.py` | Encrypts API keys at rest |
| `config.py` | Reads environment variables |

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
```

Generate the two secrets and paste them into `.env`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"                      # SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # FERNET_KEY
```

**Back up `FERNET_KEY` somewhere safe.** Every stored API key is encrypted with it.
Lose it and you re-enter every credential.

Run the two processes in separate terminals:

```bash
python app.py      # dashboard on http://localhost:5000
python worker.py   # the part that actually talks to BytePlus
```

## Railway

Two services off the same repo, sharing one Postgres and the same variables:

- Web — `gunicorn app:app --bind 0.0.0.0:$PORT`
- Worker — `python worker.py`

`DATABASE_URL` comes from the Postgres plugin. Set `SECRET_KEY`, `FERNET_KEY`,
`ADMIN_USER`, `ADMIN_PASSWORD` yourself.

## First run

1. **Models** → add a model. Paste the ID exactly as the ModelArk console shows it,
   and set the cost of one output.
2. **Credentials** → add each account's API key with its current balance and a
   priority number. Lower priority numbers are used first, so `10 / 20 / 30`
   drains one account before touching the next.
3. **Generate** → queue a job. Watch it move through **Jobs**.

## How routing decides

A key is eligible when all of these hold:

- routing is switched on and status is `active`
- it is not in cooldown
- it has fewer jobs in flight than its concurrency limit
- **its own balance covers the whole job**

That last one matters more than it sounds. Balances do not combine. Three keys
holding 15 each cannot run a job costing 17 — the job runs on one key or not at
all. When nothing qualifies the job is marked `blocked` with the reason, instead
of being fired off to fail.

## What happens when a call fails

| Response | Key becomes | Job |
|---|---|---|
| 429 rate limit | cooling, 60s | retried on the next key |
| 402 / insufficient balance | exhausted | retried on the next key |
| 401 / 403 | disabled, routing off | retried on the next key |
| 5xx | short cooldown | retried |
| other 4xx | untouched | failed, no retry |

The last row is deliberate. A rejected prompt or a wrong model ID is not the key's
fault, and retrying it across the pool would burn balance on every account.

## Polling is sticky

The task ID that ModelArk returns only exists inside the account that created it.
Each job stores which credential submitted it, and polls with that same key.
Rotating during polling would return "task not found".

## Quota tracking

BytePlus does not expose a live balance over the API, so this tool tracks it
locally: the estimated cost is deducted at submission and refunded if the
generation fails. Drift is expected. Check the real balance in the ModelArk
console now and then and correct it with **Sync quota to** on the Credentials page.

## Not built yet

- Team logins — right now there is one admin account from the environment
- Low balance alerts to WhatsApp or email
- Automatic download of finished videos to storage
