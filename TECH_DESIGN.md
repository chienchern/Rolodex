# Rolodex MVP - Technical Design

## Context
Rolodex is an SMS-based personal CRM. Users text the system to log interactions, query contact history, set custom reminders, and receive automated follow-up reminders. Google Sheets is the data store. Supports 2-3 users, each with their own spreadsheet.

## Architecture

```
User SMS ──► Twilio ──► Cloud Run /sms-webhook ──► Gemini (NLP) ──► Google Sheets
                              │                                           ▲
                              ▼                                           │
                         Firestore                                        │
                    (context cache +                                      │
                     idempotency)                                         │
                                                                          │
Cloud Scheduler ──► Cloud Run /reminder-cron ─────────────────► Twilio ──► User SMS
                                                (reminders)
```

## Tech Stack

| Component | Choice | What it is |
|-----------|--------|------------|
| Cloud provider | **Google Cloud** | Google's cloud platform (like AWS or Azure) |
| Compute | **Cloud Run** (single Flask app) | Google's serverless container platform — you give it your code, it runs a web server that spins up on demand and scales to zero when idle (no traffic = no cost) |
| SMS | **Twilio** | Cloud telephony service — provides a real phone number, forwards incoming SMS to your server as HTTP requests, and exposes an API to send outbound texts |
| NLP/LLM | **Gemini API** | Google's LLM API — parses natural language into structured JSON (intent, entities, dates) |
| Data store | **Google Sheets** (via gspread + service account) | Used as a lightweight database. `gspread` is a Python library that reads/writes Sheets via Google's API. A service account is a bot identity that authenticates without user login |
| Context cache + idempotency | **Firestore** | Google's serverless NoSQL document database — stores JSON-like documents in collections, with built-in TTL (time-to-live) to auto-delete expired documents |
| Scheduler | **Cloud Scheduler** | Google's managed cron service — fires HTTP requests on a schedule (like cron jobs, but hosted) |
| Deployment | **`gcloud run deploy --source .`** | Single CLI command that auto-detects the language, builds a container image using Buildpacks (no Dockerfile needed), and deploys to Cloud Run |

## Key Design Decisions (with rationale)

1. **Google Cloud over AWS/Azure** — Google Sheets is the data store, and Google Cloud has the simplest authentication story for accessing Google services (a single service account covers Sheets, Firestore, and Cloud Run). On AWS, you'd need to manage separate Google API credentials alongside AWS IAM.

2. **Cloud Run over Cloud Functions** — Cloud Run runs a full web server (a Flask app), while Cloud Functions (Google's equivalent of AWS Lambda) runs individual functions triggered by events. Cloud Run lets us serve multiple endpoints (`/sms-webhook`, `/reminder-cron`) from one codebase with shared logic. Cloud Functions would require separate function deployments for each endpoint with duplicated dependencies. Both scale to zero and cost the same.

3. **Synchronous processing over a message queue** — When an SMS arrives, the app processes it in the same HTTP request: call Gemini (~2s), write to Sheets (~1s), send reply. The alternative is to return 200 immediately, put the message on a queue (e.g., Pub/Sub, RabbitMQ), and process it asynchronously. A queue adds infrastructure, retry logic, and makes debugging harder. Since total processing time (~8s with batching) is well within Twilio's 15s webhook timeout, synchronous is simpler and sufficient for 2-3 users.

4. **Contact names passed directly in LLM prompt over fuzzy-match library** — When the user texts "Had coffee with Sarah", the app needs to match "Sarah" to a contact. The alternative is a fuzzy-matching library (e.g., `fuzzywuzzy`) that compares the name against the contact list using string similarity scores. Instead, we pass the full contact list to Gemini and let it resolve the match. This is simpler (no matching logic to write/tune) and handles nicknames and misspellings naturally. The tradeoff: it only works because the contact list is small (<100 names). For larger lists, the prompt would get too long and a dedicated matching step would be needed.

5. **Firestore for idempotency** — Twilio may retry delivering an SMS webhook if it doesn't get a 200 response in time. Without protection, the same message could be processed twice (duplicate log entry, duplicate reply). Each Twilio message has a unique `MessageSid`. We store it in Firestore on first processing and check for it on subsequent requests. Firestore is already in the stack for multi-turn conversation state (storing pending clarification questions — see "Multi-turn Context" section below), so this adds no new infrastructure.

6. **OIDC auth for `/reminder-cron`** — The app must be publicly accessible (`--allow-unauthenticated` on Cloud Run) so Twilio can reach `/sms-webhook`. But this means `/reminder-cron` is also public — anyone who discovers the URL could trigger reminders. To prevent this, Cloud Scheduler sends an OIDC token (a signed JWT from Google) with each request. The `/reminder-cron` handler verifies this token, ensuring only Cloud Scheduler can invoke it. No auth is needed on `/sms-webhook` because Twilio's `X-Twilio-Signature` header serves the same purpose.

7. **Application-level context expiry check** — Firestore's TTL feature auto-deletes expired documents, but deletion can be delayed up to 24 hours. This means an expired document can still appear in query results. For example: the app asked "Which Sarah?" 2 hours ago, the 10-minute context should be long gone, but Firestore hasn't cleaned it up yet. Without a check, the app would mistakenly treat the user's next message as a reply to that stale question. So every context query filters by `expire_at > now` in code — if the document's expiry timestamp is in the past, the app ignores it. TTL eventually cleans up the data, but the app doesn't rely on it for correctness.

8. **Sleep-based message batching over Cloud Tasks** — Users often send multiple texts in quick succession for a single interaction. To batch them, each request handler stores its message then sleeps 5 seconds. After waking, it checks if any newer messages arrived. If yes, it does nothing and returns — the newer message's handler will pick up the batch. If no, it's the last message, so it processes the entire batch. Crucially, each message arrives as a separate HTTP request on a separate thread, so the sleeps run in parallel, not sequentially. A 4-message burst looks like:
    - t=0s: Msg 1 → store → sleep 5s
    - t=1s: Msg 2 → store → sleep 5s
    - t=2s: Msg 3 → store → sleep 5s
    - t=3s: Msg 4 → store → sleep 5s
    - t=5s: Msg 1 wakes → sees Msg 4 is newer → return 200 (5s total)
    - t=6s: Msg 2 wakes → sees Msg 4 is newer → return 200 (5s total)
    - t=7s: Msg 3 wakes → sees Msg 4 is newer → return 200 (5s total)
    - t=8s: Msg 4 wakes → no newer messages → process all 4 → Gemini (~2s) → Sheets (~1s) → return 200 (~8s total)

    No single request exceeds ~8s, well within Twilio's 15s limit. The alternative is Cloud Tasks (Google's managed task queue) — each message schedules a delayed task that processes the batch later. Cloud Tasks would decouple the wait from the Twilio request (no timeout pressure), but adds a new GCP service, a new endpoint, and more configuration. The sleep approach is simpler. Tradeoff: every interaction has a minimum 5s delay, even single messages.

9. **Separate spreadsheet per user over shared spreadsheet** — Each user gets their own Google Sheet. The alternative is a single shared spreadsheet with a `user` column on every tab, filtering on every read/write. Separate sheets keep data cleanly isolated — each user can open their spreadsheet and see only their contacts. It also avoids filtering logic and means one user's data can't accidentally leak to another.

## Flask App Routes

### `POST /sms-webhook` — Inbound SMS handler

Messages are batched to handle multi-message sequences (e.g., user sends 3 texts in quick succession about the same interaction). The handler sleeps briefly to collect messages before processing them as a group.

1. Validate Twilio request signature (`X-Twilio-Signature`)
2. **Idempotency check:** Look up `MessageSid` in Firestore. If exists, return 200 and stop
3. Store `MessageSid` in Firestore (idempotency)
4. Look up sender's phone number in Users tab to resolve their spreadsheet
5. Store the SMS text in Firestore `pending_messages` collection (keyed by user phone + timestamp)
6. **Sleep 5 seconds** (batch window — allows additional messages to arrive)
7. Query Firestore for all pending messages from this user
8. If a newer message exists → another request will handle the batch → return 200
9. **This is the last message in the batch.** Combine all pending message texts into one string
10. Check Firestore for multi-turn context (filtered by `expire_at > now`)
11. Read user's Google Sheet (active contacts + settings)
12. Call Gemini with: combined SMS text, contact names, multi-turn context, current date+day-of-week in user timezone
13. Gemini returns structured JSON (intent, contacts, notes, follow-up date, response message)
14. **Multi-turn context resolution:** If pending context exists and Gemini returns a new intent (not a clarification response), discard the pending context and process as a fresh message
15. Execute intent: update Sheets (Contacts tab, Logs tab), set reminder_date. Store the combined raw SMS text in the Logs tab `raw_message` column for debugging
16. Clear pending messages from Firestore
17. Send reply SMS via `twilio_client.messages.create()`
18. Return 200 OK

**Error handling:** Wrap in try/except. On failure, send generic error SMS: "Something went wrong. Please try again." Log the full error.

**Timing budget:** 5s batch window + ~2s Gemini + ~1s Sheets = **~8s total**. Within Twilio's 15s max timeout (which includes connection overhead). Earlier messages in a batch return 200 after their 5s sleep (~5s total).

### `POST /reminder-cron` — Daily reminder job
1. Validate OIDC token from `Authorization: Bearer` header (ensures only Cloud Scheduler can trigger this endpoint — use `google-auth` to verify the token and check the expected service account email)
2. Read all users from Users tab
3. For each user:
   a. Read their timezone from their Settings tab
   b. Compute today's date in their timezone
   c. Read all active contacts from their Contacts tab
   d. Find contacts where:
      - `reminder_date` == today → send "day of" reminder
      - `reminder_date` == today + 7 days → send "1 week before" reminder (only if reminder_date > 7 days from last_contact_date)
   e. Send reminder SMS to the user's phone number via Twilio
4. Return 200 OK

**Cloud Scheduler config:** `0 9 * * *` in UTC. The job iterates over users and computes "today" per-user using their timezone setting.

## Google Sheets Schema

### Users tab (in a shared master spreadsheet)

| phone | name | sheet_id |
|---|---|---|
| +15551234567 | Alice | 1AbC2dEf3GhI4jKlMnOpQrStUvWxYz |
| +15559876543 | Bob | 9ZyX8wVu7TsR6qPoNmLkJiHgFeDcBa |

- Maps each user's phone number to their personal spreadsheet.
- The master spreadsheet ID is stored as an env var (`MASTER_SHEET_ID`).

### Contacts tab (per-user spreadsheet)

| name | reminder_date | last_contact_date | last_contact_notes | status |
|---|---|---|---|---|
| Sarah Chen | 2026-02-24 | 2026-02-10 | had coffee, she's launching her startup next month | active |
| Dad | 2026-03-05 | 2026-01-20 | called him, discussed retirement party planning | active |
| Mike Torres | | 2026-02-03 | grabbed lunch, he started his new job at Google | active |

- `reminder_date`: the date the user receives a reminder SMS. Set explicitly by the user ("follow up next Friday") or auto-computed as `last_contact_date + DEFAULT_REMINDER_DAYS` when logging an interaction. Empty means no pending reminder (e.g., Mike).
- Default reminder interval is stored in the Settings tab (`default_reminder_days`), so it can be changed without redeploying.

### Logs tab (per-user spreadsheet)

| date | contact_name | intent | notes | raw_message |
|---|---|---|---|---|
| 2026-02-10 | Sarah Chen | log_interaction | had coffee, she's launching her startup next month | Had coffee with Sarah Chen, she's launching her startup next month. Follow up in 2 weeks |
| 2026-02-03 | Mike Torres | log_interaction | grabbed lunch, he started his new job at Google | Lunch with Mike today, he just started at Google |
| 2026-01-20 | Dad | set_reminder | birthday | Remind me to call Dad on his birthday March 5 |

- `raw_message`: the original SMS text, stored for debugging when Gemini misparses.
- Every inbound SMS that results in an action gets a row here.

### Settings tab (per-user spreadsheet)

| key | value |
|---|---|
| timezone | America/New_York |
| default_reminder_days | 14 |

## NLP Design (Gemini)

Single structured prompt. Input:
- User's SMS text
- List of active contact names (for matching — fine for <100 contacts)
- Pending multi-turn context (if any)
- Current date with day-of-week in user's timezone (e.g., "Monday, February 10, 2026")

Output (structured JSON):
```json
{
  "intent": "log_interaction | query | set_reminder | archive | onboarding | clarify | unknown",
  "contacts": [{"name": "John Smith", "match_type": "exact | fuzzy | new | ambiguous"}],
  "notes": "discussed his startup funding (or reason for reminder, e.g. 'birthday')",
  "follow_up_date": "2026-02-24",
  "needs_clarification": false,
  "clarification_question": null,
  "response_message": "Updated John Smith. I'll remind you to reach out on Monday, Feb 24, 2026."
}
```

The `notes` field should always be populated with relevant context — for interactions this is what was discussed, for reminders this is the reason (e.g., "birthday", "check in on job search").

When `needs_clarification` is true, store context in Firestore with 10-min TTL and send `clarification_question` to user. If the next message from the user is a new intent rather than a clarification response (e.g., user ignores "Which Sarah?" and texts something unrelated), Gemini should classify it as the new intent — the app discards the pending context and processes the new message fresh.

## Pending Messages (Firestore)

- **Collection:** `pending_messages`
- **Document ID:** auto-generated
- **Fields:** `user_phone`, `message_text`, `message_sid`, `received_at`, `expire_at`
- **TTL:** 10 minutes (cleanup — messages should be cleared after processing, TTL is a safety net)
- Used by the batch window in `/sms-webhook`. Each inbound SMS is stored here immediately. After the 5s sleep, the handler queries for all pending messages for the user, ordered by `received_at`.

## Multi-turn Context (Firestore)

- **Collection:** `context`
- **Document ID:** user phone number (e.g., `+15550000000`)
- **Fields:** `original_message`, `pending_intent`, `candidates`, `created_at`, `expire_at`
- **TTL:** 10 minutes (Firestore TTL policy on `expire_at` field). **Note:** TTL deletion can be delayed up to 24 hours — always filter by `expire_at > now` in application code to enforce expiry

## Idempotency (Firestore)

- **Collection:** `processed_messages`
- **Document ID:** Twilio `MessageSid`
- **Fields:** `processed_at`, `expire_at`
- **TTL:** 1 hour (Firestore TTL policy on `expire_at` field, auto-cleanup)

## Project Structure

```
Rolodex/
├── PRD.md
├── .env                     # Local dev env vars (not committed)
├── .gitignore
├── requirements.txt
├── Procfile                 # web: gunicorn --bind :$PORT --workers 1 --threads 8 app:app
├── app.py                   # Flask app, route definitions, entry point
├── sms_handler.py           # Inbound SMS processing logic
├── reminder_handler.py      # Reminder cron logic
├── sheets_client.py         # Google Sheets read/write via gspread
├── nlp.py                   # Gemini API integration, prompt, response parsing
├── context.py               # Firestore context cache + idempotency
└── config.py                # Configuration, env vars, constants
```

## Key Dependencies (requirements.txt)

```
flask
gunicorn
twilio
google-cloud-firestore
gspread
google-auth
google-genai
```

## Environment Variables (Cloud Run)

| Variable | Description |
|----------|-------------|
| `TWILIO_ACCOUNT_SID` | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | Twilio auth token |
| `TWILIO_PHONE_NUMBER` | Twilio phone number (e.g., +1234567890) |
| `GEMINI_API_KEY` | Gemini API key |
| `MASTER_SHEET_ID` | Google Sheet ID for the master spreadsheet (contains Users tab) |
| `GSPREAD_CREDENTIALS_B64` | Base64-encoded service account JSON |

## Setup Steps

1. **GCP Project:** Create project, enable Cloud Run, Firestore, Sheets API, Cloud Scheduler APIs
2. **Service Account:** Create with Sheets editor + Firestore access. Download JSON key, base64-encode for deployment
3. **Google Sheets:** Create a master spreadsheet with a Users tab. For each user, create a personal spreadsheet with Contacts, Logs, Settings tabs. Share all sheets with the service account email
4. **Twilio:** Create account, buy phone number, configure webhook URL to Cloud Run `/sms-webhook`. Configure a **Fallback URL** for resilience (Twilio has minimal retry behavior on inbound SMS webhooks)
5. **Deploy:** `gcloud run deploy rolodex-mvp --source . --region us-central1 --allow-unauthenticated --set-env-vars "..."`
6. **Cloud Scheduler:** Create job: `0 9 * * *` in UTC, targeting Cloud Run `/reminder-cron` endpoint with OIDC auth (create a scheduler service account with `roles/run.invoker`). The app handles per-user timezone logic internally
7. **Test:** Send real SMS end-to-end

## Verification / Testing Plan

1. **Local dev:** Run `flask run`, use ngrok to expose webhook, point Twilio to ngrok URL
2. **Unit tests:** Test NLP prompt parsing, Sheets read/write, context cache logic
3. **Integration test:** Send SMS → verify Sheets updated correctly → verify reply received
4. **Reminder test:** Manually set a contact's reminder_date to today, trigger `/reminder-cron`, verify SMS received
