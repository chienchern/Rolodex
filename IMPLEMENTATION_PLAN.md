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
  - [Approach: TDD](#approach-test-driven-development-tdd)
  - [Test Infrastructure](#test-infrastructure)
  - [Project Structure](#project-structure)
  - [End-to-end Tests](#automated-end-to-end-tests-teststest_e2epy)
- [Prerequisites (Manual Steps)](#prerequisites-manual-steps--you-do-these)
- [Deployment Steps](#deployment-steps-ill-run-these-via-cli)
- [Key Risks and Mitigations](#key-risks-and-mitigations)

## Context

The Rolodex MVP is an SMS-based personal CRM: users text a Twilio number, a Flask app on Cloud Run parses intent via Gemini, and Google Sheets stores contact data. Firestore handles ephemeral state (message batching, multi-turn context, idempotency). Cloud Scheduler triggers daily reminders. The PRD and tech design are complete and well-aligned — no code exists yet.

## Implementation Phases

### Phase 0: Project Scaffolding
**Files:** `.gitignore`, `requirements.txt`, `requirements-dev.txt`, `Procfile`, `tests/conftest.py`

- `.gitignore`: `.env`, `__pycache__/`, `*.pyc`, `.DS_Store`, `venv/`, `*.json` (credential files), `.claude/`
- `requirements.txt`: flask, gunicorn, twilio, google-cloud-firestore, gspread, google-auth, google-genai, pytz
- `requirements-dev.txt`: `-r requirements.txt`, pytest, pytest-mock
- `Procfile`: `web: gunicorn --bind :$PORT --workers 1 --threads 8 app:app`
- `tests/conftest.py`: Shared pytest fixtures — mock Firestore client, mock gspread client/spreadsheet/worksheet, mock Twilio client, mock google.genai client, sample env vars, sample contact data, sample Gemini responses

### Phase 1: `config.py` — Configuration
**File:** `config.py`

**1a. Write tests** (`tests/test_config.py`):
- Env vars load correctly when all are set
- Missing required env var raises `KeyError`
- Base64-decoded credentials produce valid dict
- Constants have expected values (BATCH_WINDOW_SECONDS=5, CONTEXT_TTL_MINUTES=10, etc.)
- `send_sms()` calls `twilio_client.messages.create()` with correct args

**1b. Implement** `config.py`:
Load all env vars with fail-fast (`os.environ["KEY"]`), decode base64 service account credentials, define constants. Also include a shared `send_sms()` helper since both handlers need it.

### Phase 2: `sheets_client.py` — Google Sheets Data Layer
**File:** `sheets_client.py` | **Depends on:** `config.py`

**2a. Write tests** (`tests/test_sheets_client.py`):
Mock gspread client. Tests define the interface:
- `get_user_by_phone(phone)` — returns user dict when found, returns `None` when not found
- `get_active_contacts(sheet_id)` — returns list of contact dicts with status="active" only
- `get_settings(sheet_id)` — returns key-value dict from Settings tab
- `update_contact(sheet_id, contact_name, updates)` — updates correct row, raises if contact not found
- `add_contact(sheet_id, contact_data)` — appends row with correct fields
- `add_log_entry(sheet_id, log_data)` — appends to Logs tab
- `archive_contact(sheet_id, contact_name)` — sets status to "archived"
- `get_all_users()` — returns all rows from master Users tab

**2b. Implement** `sheets_client.py`:
Uses `gspread.service_account_from_dict()` with lazy-cached client. Implements all functions to pass tests.

### Phase 3: `context.py` — Firestore State Management
**File:** `context.py` | **Depends on:** `config.py`

**3a. Write tests** (`tests/test_context.py`):
Mock Firestore client. Tests define the interface for three collections:

Idempotency (processed_messages):
- `is_message_processed(message_sid)` — returns `False` for new, `True` for existing
- `mark_message_processed(message_sid)` — stores doc with `expire_at` (1-hour TTL)

Batch window (pending_messages):
- `store_pending_message(user_phone, message_text, message_sid)` — stores doc with `expire_at`
- `get_pending_messages(user_phone)` — returns messages ordered by `received_at`
- `has_newer_message(user_phone, received_at)` — returns `True`/`False`
- `clear_pending_messages(user_phone)` — deletes all pending for user

Multi-turn context:
- `get_context(user_phone)` — returns context dict if `expire_at > now`, else `None`
- `store_context(user_phone, context_data)` — stores with 10-min TTL
- `clear_context(user_phone)` — deletes context doc

Key edge case tests:
- Expired documents (`expire_at` in past) are treated as non-existent
- Empty collections return empty results

**3b. Implement** `context.py`:
Firestore client with application-level TTL checks. Three collections: `processed_messages`, `pending_messages`, `context`.

### Phase 4: `nlp.py` — Gemini NLP Integration
**File:** `nlp.py` | **Depends on:** `config.py`

**4a. Write tests** (`tests/test_nlp.py`):
Mock `google.genai` client. Tests define the interface:

Prompt construction:
- `parse_sms()` includes contact names, context, and date in the prompt
- All provided contact names appear in the prompt

Response parsing (happy path):
- Well-formed JSON response is parsed into expected dict structure
- All intent types handled: `log_interaction`, `query`, `set_reminder`, `archive`, `clarify`, `unknown`
- Multi-contact responses parsed correctly

Fallback parsing:
- JSON wrapped in markdown backticks is extracted
- JSON with trailing/leading text is extracted
- Missing optional fields default to `None`

Error handling:
- API call raises exception — function raises or returns sensible error
- Empty response body — handled gracefully
- Non-JSON response — handled gracefully

Field validation:
- Unknown intent value is handled
- Null contact name is handled
- Malformed date string is handled

**4b. Implement** `nlp.py`:
Main function: `parse_sms(sms_text, contact_names, pending_context, current_date_str) -> dict`. The Gemini prompt classifies intent, matches contacts, parses dates, and generates replies. Uses `google-genai` SDK with `response_mime_type: "application/json"` for reliable structured output. Robust fallback parsing if JSON extraction fails.

**4c. Manual prompt tuning:**
After tests pass, iterate on the Gemini prompt with real SMS examples. The prompt wording is validated by sending real messages, not by unit tests.

### Phase 5: `sms_handler.py` — SMS Webhook Logic
**File:** `sms_handler.py` | **Depends on:** `config`, `sheets_client`, `context`, `nlp`

**5a. Write tests** (`tests/test_sms_handler.py`):
Mock all dependencies (config, sheets_client, context, nlp). Tests define:

Orchestration:
- Valid Twilio signature proceeds; invalid signature returns error
- Duplicate MessageSid (idempotency) returns 200 without processing
- Unknown phone number returns error SMS
- Batch window: defers if newer message exists

Intent routing (one test per intent):
- `log_interaction` — updates contact, adds log, sets reminder_date, sends reply
- `query` — no sheet updates, sends response_message
- `set_reminder` — updates reminder_date, sends confirmation
- `archive` — first call stores context asking confirmation; confirmation executes archive
- `clarify` — resolves pending context and re-executes original intent
- `unknown` — sends response_message as-is

Multi-turn:
- Stale context (new intent detected) is discarded
- Valid context is used for resolution

Error handling:
- Exception during processing sends "Something went wrong" SMS

**5b. Implement** `sms_handler.py`:
Main function: `handle_inbound_sms(form_data, request_url, twilio_signature) -> str`

Full orchestration:
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

**6a. Write tests** (`tests/test_reminder_handler.py`):
Mock sheets_client, config. Tests define:

Auth:
- Valid OIDC token proceeds; missing/invalid token returns 401

Date logic:
- Contact with `reminder_date == today` gets day-of reminder
- Contact with `reminder_date == today + 7` AND `reminder_date > last_contact_date + 7` gets 1-week-before reminder
- Contact with `reminder_date == today + 7` but `reminder_date <= last_contact_date + 7` gets NO 1-week-before reminder (recent interaction)
- Archived contacts are excluded
- Contacts with no reminder_date are excluded

SMS batching:
- Multiple reminders for one user are combined into a single SMS

Timezone handling:
- "Today" is computed per-user using their timezone setting

**6b. Implement** `reminder_handler.py`:
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

**7a. Write tests** (`tests/test_app.py`):
Use Flask test client. Mock sms_handler and reminder_handler. Tests define:
- `POST /sms-webhook` delegates to `handle_inbound_sms()` with correct args
- `POST /reminder-cron` delegates to `handle_reminder_cron()` with correct args
- `GET /health` returns 200 "OK"
- Unknown routes return 404

**7b. Implement** `app.py`:
Thin routing layer:
- `POST /sms-webhook` → delegates to `sms_handler.handle_inbound_sms()`
- `POST /reminder-cron` → delegates to `reminder_handler.handle_reminder_cron()`
- `GET /health` → returns "OK"

---

## Testing Strategy

### Approach: Test-Driven Development (TDD)
Every module follows the same workflow: write tests first to define the interface and expected behavior, then implement to make the tests pass. Tests use mocked external dependencies (gspread, Firestore, Twilio, Gemini) so they run fast with no infrastructure required.

### Test infrastructure

**Dependencies** (`requirements-dev.txt`): pytest, pytest-mock

**Shared fixtures** (`tests/conftest.py`):
- `mock_firestore_client` — patched Firestore client with configurable document returns
- `mock_gspread_client` — patched gspread client with sample worksheet data
- `mock_twilio_client` — patched Twilio client capturing `messages.create()` calls
- `mock_genai_client` — patched google.genai client with configurable responses
- `sample_contacts` — list of sample contact dicts
- `sample_settings` — sample settings dict
- `env_vars` — monkeypatch fixture that sets all required env vars

**Run tests:** `pytest tests/ -v`

### Project structure
```
tests/
├── conftest.py
├── test_config.py
├── test_sheets_client.py
├── test_context.py
├── test_nlp.py
├── test_sms_handler.py
├── test_reminder_handler.py
├── test_app.py
└── test_e2e.py              # Runs against deployed app with real APIs
```

### Automated end-to-end tests (`tests/test_e2e.py`)

E2e tests run against the deployed app using real APIs (Twilio, Sheets, Firestore). No browser automation needed — Twilio API sends inbound SMS, gspread verifies Sheet state, Twilio message history verifies replies.

**How it works:**
- Each test POSTs to the deployed `/sms-webhook` endpoint with form data matching Twilio's webhook format (Body, From, MessageSid, etc.)
- A valid Twilio signature is computed using `twilio.request_validator.RequestValidator` so signature validation passes
- After posting, the test waits for processing (~10s for batch window + Gemini + Sheets), then checks results
- Outbound reply SMS is verified via `twilio_client.messages.list(to=TEST_USER_PHONE)`
- Sheet state is verified via gspread reading the test user's spreadsheet
- Each test resets the test spreadsheet to a known seed state before running

**Run e2e tests:** `pytest tests/test_e2e.py -v` (requires deployed app + real credentials in env)

**Seed data** (reset before each test):
- Test user in master Users tab: phone=`+15550001111`, name="Test User", sheet_id=`<test_sheet_id>`
- Test user's Contacts tab:

| name | reminder_date | last_contact_date | last_contact_notes | status |
|------|--------------|-------------------|-------------------|--------|
| Sarah Chen | 2026-02-20 | 2026-01-15 | discussed her startup | active |
| Dad | 2026-03-01 | 2026-01-20 | called him about retirement | active |
| Mike Torres | | 2026-02-03 | lunch, new job at Google | active |
| John Smith | 2026-02-25 | 2026-02-01 | coffee, discussed travel plans | active |
| John Doe | 2026-03-10 | 2026-01-30 | drinks, he's moving to Austin | active |

- Test user's Settings tab: timezone=`America/New_York`, default_reminder_days=`14`

---

**Test 1: Log interaction with existing contact**

| | |
|---|---|
| **Input SMS** | "Had coffee with Sarah" |
| **Expected reply contains** | "Sarah Chen", a date 14 days from today (day-of-week + date) |
| **Expected Contacts tab** | Sarah Chen: `last_contact_date` = today, `last_contact_notes` contains "coffee", `reminder_date` = today + 14 days |
| **Expected Logs tab** | New row: `date` = today, `contact_name` = "Sarah Chen", `intent` = "log_interaction", `notes` contains "coffee", `raw_message` = "Had coffee with Sarah" |

**Test 2: Log interaction with explicit follow-up timing**

| | |
|---|---|
| **Input SMS** | "Lunch with Dad, follow up in 3 weeks" |
| **Expected reply contains** | "Dad", a date 21 days from today (day-of-week + date) |
| **Expected Contacts tab** | Dad: `last_contact_date` = today, `last_contact_notes` contains "lunch", `reminder_date` = today + 21 days |
| **Expected Logs tab** | New row with `contact_name` = "Dad", `notes` contains "lunch" |

**Test 3: Query last contact**

| | |
|---|---|
| **Input SMS** | "When did I last talk to Mike?" |
| **Expected reply contains** | "Mike Torres", "Feb 3" or "February 3", "Google" or "new job" |
| **Expected Contacts tab** | No changes (all fields unchanged from seed) |
| **Expected Logs tab** | No new rows |

**Test 4: New contact — no similar names**

Two-turn flow:

| | |
|---|---|
| **Input SMS 1** | "Dinner with Priya" |
| **Expected reply 1 contains** | "Priya", "add" or "confirm" or "new" |
| **Input SMS 2** | "YES" |
| **Expected reply 2 contains** | "Added Priya", a reminder date |
| **Expected Contacts tab** | New row: `name` = "Priya", `status` = "active", `last_contact_date` = today, `reminder_date` = today + 14 days |
| **Expected Logs tab** | New row with `contact_name` = "Priya" |

**Test 5: Ambiguous match — disambiguation**

Two-turn flow:

| | |
|---|---|
| **Input SMS 1** | "Met with John for drinks" |
| **Expected reply 1 contains** | "Which John", "John Smith", "John Doe" |
| **Input SMS 2** | "Doe" |
| **Expected reply 2 contains** | "John Doe" |
| **Expected Contacts tab** | John Doe: `last_contact_date` = today, `last_contact_notes` contains "drinks". John Smith: unchanged |
| **Expected Logs tab** | New row with `contact_name` = "John Doe" |

**Test 6: Archive contact**

Two-turn flow:

| | |
|---|---|
| **Input SMS 1** | "Remove Sarah from my rolodex" |
| **Expected reply 1 contains** | "Archive", "Sarah Chen", "confirm" or "YES" |
| **Input SMS 2** | "YES" |
| **Expected reply 2 contains** | "archived" or "removed" |
| **Expected Contacts tab** | Sarah Chen: `status` = "archived". All other fields preserved |

**Test 7: Set custom reminder**

| | |
|---|---|
| **Input SMS** | "Remind me about Dad in 1 month" |
| **Expected reply contains** | "Dad", a date ~30 days from today (day-of-week + date) |
| **Expected Contacts tab** | Dad: `reminder_date` = today + ~30 days. `last_contact_date` and `last_contact_notes` unchanged |
| **Expected Logs tab** | No new rows (or row with `intent` = "set_reminder") |

**Test 8: Reminder cron — day-of reminder**

| | |
|---|---|
| **Setup** | Via gspread, set Sarah Chen's `reminder_date` = today |
| **Input** | POST to `/reminder-cron` with valid OIDC token |
| **Expected outbound SMS contains** | "Sarah Chen", "startup" (from last_contact_notes) |
| **Expected Contacts tab** | No changes |

**Test 9: Reminder cron — 1-week-before reminder**

| | |
|---|---|
| **Setup** | Via gspread, set Sarah Chen's `reminder_date` = today + 7, `last_contact_date` = 30 days ago (so reminder_date > last_contact_date + 7) |
| **Input** | POST to `/reminder-cron` with valid OIDC token |
| **Expected outbound SMS contains** | "Sarah Chen", "1 week" or "7 days" |

**Test 10: Reminder cron — no 1-week reminder for recent contact**

| | |
|---|---|
| **Setup** | Via gspread, set Mike's `reminder_date` = today + 7, `last_contact_date` = today - 3 (recent interaction, so reminder_date <= last_contact_date + 7) |
| **Input** | POST to `/reminder-cron` with valid OIDC token |
| **Expected** | No outbound SMS to test user about Mike |

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
