# Rolodex MVP Implementation Plan

## Table of Contents

- [Context](#context)
- [Leveraging Claude Code for This Build](#leveraging-claude-code-for-this-build)
- [Implementation Phases](#implementation-phases)
  - [Phase 0: Project Scaffolding](#phase-0-project-scaffolding)
  - [Phase 1: config.py](#phase-1-configpy--configuration)
  - [Phase 2: sheets_client.py](#phase-2-sheets_clientpy--google-sheets-data-layer)
  - [Phase 3: context.py](#phase-3-contextpy--firestore-state-management)
  - [Phase 4: nlp.py](#phase-4-nlppy--gemini-nlp-integration)
  - [Phase 5: sms_handler.py](#phase-5-sms_handlerpy--sms-webhook-logic)
  - [Phase 6: reminder_handler.py](#phase-6-reminder_handlerpy--daily-reminder-cron)
  - [Phase 7: app.py](#phase-7-apppy--flask-app-entry-point)
- [Testing Strategy](#testing-strategy)
- [Prerequisites (Manual Steps)](#prerequisites-manual-steps--you-do-these)
- [Deployment Steps](#deployment-steps-ill-run-these-via-cli)
- [Key Risks and Mitigations](#key-risks-and-mitigations)

## Context

The Rolodex MVP is an SMS-based personal CRM: users text a Twilio number, a Flask app on Cloud Run parses intent via Gemini, and Google Sheets stores contact data. Firestore handles ephemeral state (message batching, multi-turn context, idempotency). Cloud Scheduler triggers daily reminders. The PRD and tech design are complete and well-aligned — no code exists yet.

---

## Leveraging Claude Code for This Build

The user wants to understand how to use Claude Code's capabilities effectively. Here's what's relevant for this project:

**Sub-agents (Task tool)** — Claude can spawn specialized child agents that work independently and return results. Types available:
- **Explore** — searches/reads code to answer questions about the codebase
- **Plan** — designs implementation approaches
- **Bash** — runs shell commands
- **general-purpose** — handles complex multi-step tasks with all tools

For this build, sub-agents are useful for: parallelizing independent module implementation, researching API documentation (gspread, Twilio, google-genai), and running tests.

**Agent teams / parallel agents** — Multiple sub-agents can be launched simultaneously in one message. For example, `sheets_client.py`, `context.py`, and `nlp.py` have no dependencies on each other, so all three could be built in parallel by separate agents.

**Skills** — Slash commands like `/commit` that trigger specialized workflows. The `/commit` skill is useful after each phase to checkpoint progress.

**MCP (Model Context Protocol) servers** — External tool servers that extend Claude's capabilities. For example, a Firestore MCP server could let Claude directly read/write Firestore during testing. MCPs are configured in `.claude/settings.json`. Not strictly needed for this build but can accelerate debugging.

**Plugins** — Not a distinct Claude Code concept; this term usually refers to MCPs or skills.

### Recommended workflow for this build:
1. I build each module sequentially (they're small enough that parallelization adds overhead without much time savings)
2. After each module, we test it before moving on
3. Use `/commit` after each working phase
4. If we hit API/integration questions, I can spawn Explore agents to research docs

---

## Implementation Phases

### Phase 0: Project Scaffolding
**Files:** `.gitignore`, `requirements.txt`, `Procfile`

- `.gitignore`: `.env`, `__pycache__/`, `*.pyc`, `.DS_Store`, `venv/`, `*.json` (credential files), `.claude/`
- `requirements.txt`: flask, gunicorn, twilio, google-cloud-firestore, gspread, google-auth, google-genai, pytz
- `Procfile`: `web: gunicorn --bind :$PORT --workers 1 --threads 8 app:app`

### Phase 1: `config.py` — Configuration
**File:** `config.py`

Load all env vars with fail-fast (`os.environ["KEY"]`), decode base64 service account credentials, define constants (BATCH_WINDOW_SECONDS=5, CONTEXT_TTL_MINUTES=10, etc.). Also include a shared `send_sms()` helper since both handlers need it.

### Phase 2: `sheets_client.py` — Google Sheets Data Layer
**File:** `sheets_client.py` | **Depends on:** `config.py`

Key functions:
- `get_user_by_phone(phone)` — look up user in master Users tab
- `get_active_contacts(sheet_id)` — read active contacts from user's Contacts tab
- `get_settings(sheet_id)` — read Settings tab as key-value dict
- `update_contact(sheet_id, contact_name, updates)` — update specific fields on a contact row
- `add_contact(sheet_id, contact_data)` — add new contact row
- `add_log_entry(sheet_id, log_data)` — append to Logs tab
- `archive_contact(sheet_id, contact_name)` — set status to "archived"
- `get_all_users()` — read all users (for reminder cron)

Uses `gspread.service_account_from_dict()` with lazy-cached client.

### Phase 3: `context.py` — Firestore State Management
**File:** `context.py` | **Depends on:** `config.py`

Three collections with application-level TTL checks (`expire_at > now`):
- **processed_messages** — idempotency (1-hour TTL). Functions: `is_message_processed()`, `mark_message_processed()`
- **pending_messages** — batch window (10-min TTL). Functions: `store_pending_message()`, `get_pending_messages()`, `has_newer_message()`, `clear_pending_messages()`
- **context** — multi-turn cache (10-min TTL). Functions: `get_context()`, `store_context()`, `clear_context()`

### Phase 4: `nlp.py` — Gemini NLP Integration
**File:** `nlp.py` | **Depends on:** `config.py`

Main function: `parse_sms(sms_text, contact_names, pending_context, current_date_str) -> dict`

The Gemini prompt instructs the model to:
- Classify intent: `log_interaction | query | set_reminder | archive | clarify | unknown`
- Match contact names against provided list (exact/fuzzy/new/ambiguous)
- Parse relative dates into YYYY-MM-DD format
- Generate a natural SMS reply with day-of-week and date
- Handle clarification flows (missing names, ambiguous matches, archive confirmation)

Uses `google-genai` SDK with `response_mime_type: "application/json"` for reliable structured output. Robust fallback parsing if JSON extraction fails.

### Phase 5: `sms_handler.py` — SMS Webhook Logic
**File:** `sms_handler.py` | **Depends on:** `config`, `sheets_client`, `context`, `nlp`

Main function: `handle_inbound_sms(form_data, request_url, twilio_signature) -> str`

Full 18-step orchestration:
1. Validate Twilio signature (handle `X-Forwarded-Proto` for HTTPS behind proxy)
2. Idempotency check via MessageSid
3. User lookup by phone number
4. Store pending message, sleep 5s (batch window)
5. Check for newer messages (if yes, defer)
6. Combine batched messages
7. Retrieve multi-turn context
8. Read contacts + settings from Sheets
9. Call Gemini NLP
10. Handle multi-turn resolution (discard stale context if new intent detected)
11. Execute intent (update Sheets, set reminders, create contacts, archive)
12. Clear pending messages + resolved context
13. Send reply SMS

Intent execution details:
- **log_interaction**: Update contact's `last_contact_date` + `last_contact_notes`, add log entry, compute `reminder_date` (explicit if provided, else `today + default_reminder_days`)
- **query**: No sheet updates, just return info from `response_message`
- **set_reminder**: Update `reminder_date` on contact
- **archive**: Two-turn flow — first request stores context asking confirmation, confirmation executes archive
- **clarify**: Resolve pending context and re-execute original intent
- **unknown**: Send `response_message` as-is

Error handling: try/catch wrapping entire handler, sends "Something went wrong" SMS on failure.

### Phase 6: `reminder_handler.py` — Daily Reminder Cron
**File:** `reminder_handler.py` | **Depends on:** `config`, `sheets_client`

Main function: `handle_reminder_cron(authorization_header) -> (body, status_code)`

Flow:
1. Validate OIDC token (with skip flag for local testing)
2. Read all users from master sheet
3. For each user: compute today in their timezone, find contacts due for reminders
4. Reminder logic:
   - `reminder_date == today` → send day-of reminder
   - `reminder_date == today + 7 days` AND `reminder_date > last_contact_date + 7 days` → send 1-week-before reminder
5. Combine reminders per user into single SMS, send via Twilio

### Phase 7: `app.py` — Flask App Entry Point
**File:** `app.py` | **Depends on:** all above

Thin routing layer:
- `POST /sms-webhook` → delegates to `sms_handler.handle_inbound_sms()`
- `POST /reminder-cron` → delegates to `reminder_handler.handle_reminder_cron()`
- `GET /health` → returns "OK"

---

## Testing Strategy

### Per-module verification (during implementation):
| Module | How to test |
|--------|------------|
| `config.py` | Import with env vars set, verify constants load |
| `sheets_client.py` | Run against real test spreadsheet — CRUD operations |
| `context.py` | Run against Firestore emulator — store/read/clear operations |
| `nlp.py` | Call with sample SMS texts, verify JSON responses |
| `sms_handler.py` | Sub-functions testable in isolation; full flow via Flask |
| `reminder_handler.py` | Date logic testable standalone; full flow via curl |
| `app.py` | Flask dev server + ngrok for end-to-end SMS |

### End-to-end test scenarios (post-integration):
1. Log interaction: "Had coffee with Sarah" → Sheets updated, reply received
2. Log with timing: "Lunch with Dad, follow up in 3 weeks" → custom reminder date set
3. Query: "When did I last talk to Mike?" → returns last contact info
4. New contact: "Dinner with Alex" → fuzzy-match check, confirmation flow
5. Ambiguous match: "Met with John" (2 Johns exist) → disambiguation prompt
6. Multi-turn: Reply to disambiguation → resolves correctly
7. Message batching: Send 3 messages quickly → all combined and processed once
8. Archive: "Remove Sarah" → confirmation → archived
9. Set reminder: "Remind me about Dad in 1 month" → reminder date updated
10. Reminder cron: Set contact's reminder_date to today, trigger cron → SMS received

---

## Prerequisites (Manual Steps — You Do These)

**You have:** GCP account with billing. **You need:**

1. **Twilio account** — sign up at twilio.com (free trial works), buy a phone number with SMS capability. You'll need: Account SID, Auth Token, and the phone number.
2. **`gcloud` CLI installed** — if not already, install from cloud.google.com/sdk. I'll verify this when we start.
3. **`gcloud` authenticated** — run `gcloud auth login` so I can execute commands on your behalf

Once you have Twilio credentials and gcloud ready, tell me and I'll handle everything else via CLI:
- GCP project creation and API enablement
- Service account creation and key generation
- Firestore database setup + TTL policies
- Google Sheets creation (master + per-user) via API
- Cloud Run deployment
- Cloud Scheduler job creation
- Twilio webhook configuration
- Populating `.env` with real credentials

---

## Deployment Steps (I'll Run These Via CLI)

### Phase D1: GCP Infrastructure
```
gcloud projects create rolodex-mvp
gcloud config set project rolodex-mvp
gcloud services enable run.googleapis.com firestore.googleapis.com \
  sheets.googleapis.com cloudscheduler.googleapis.com
gcloud firestore databases create --location=us-central1
```

### Phase D2: Service Account
```
gcloud iam service-accounts create rolodex-sa
gcloud projects add-iam-policy-binding rolodex-mvp \
  --member="serviceAccount:rolodex-sa@rolodex-mvp.iam.gserviceaccount.com" \
  --role="roles/datastore.user"
gcloud iam service-accounts keys create sa-key.json \
  --iam-account=rolodex-sa@rolodex-mvp.iam.gserviceaccount.com
# Base64 encode for GSPREAD_CREDENTIALS_B64 env var
```

### Phase D3: Google Sheets Setup
- Create master spreadsheet with Users tab (phone, name, sheet_id) via Sheets API
- Create per-user spreadsheets with Contacts, Logs, Settings tabs
- Share all sheets with service account email

### Phase D4: Deploy to Cloud Run
```
gcloud run deploy rolodex-mvp --source . --region us-central1 \
  --allow-unauthenticated --min-instances=1 --max-instances=1 \
  --set-env-vars "TWILIO_ACCOUNT_SID=...,TWILIO_AUTH_TOKEN=...,..."
```

### Phase D5: Cloud Scheduler + Twilio Webhook
```
gcloud scheduler jobs create http rolodex-reminder-cron \
  --schedule="0 14 * * *" \
  --uri="https://<cloud-run-url>/reminder-cron" \
  --http-method=POST --oidc-service-account-email=...
```
- Configure Twilio webhook URL to Cloud Run `/sms-webhook`

### Phase D6: Firestore TTL Policies
```
gcloud firestore fields ttls update expire_at \
  --collection-group=processed_messages --enable-ttl
gcloud firestore fields ttls update expire_at \
  --collection-group=pending_messages --enable-ttl
gcloud firestore fields ttls update expire_at \
  --collection-group=context --enable-ttl
```

### Phase D7: End-to-end Verification
- Send real SMS, verify full round-trip
- Manually trigger cron: `gcloud scheduler jobs run rolodex-reminder-cron`
- Check logs: `gcloud run services logs read rolodex-mvp`

---

## Key Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Gemini returns malformed JSON | Use `response_mime_type: "application/json"` + fallback parsing |
| Cold start + batch window approaches 15s Twilio limit | `--min-instances=1` keeps instance warm |
| Batching breaks across multiple Cloud Run instances | `--max-instances=1` for MVP (2-3 users) |
| Twilio signature mismatch behind proxy | Check `X-Forwarded-Proto`, reconstruct URL with https |
| Google Sheets API rate limits | Small user count (2-3) stays well under 60 req/min quota |
