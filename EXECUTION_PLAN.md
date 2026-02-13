# Rolodex MVP — Execution Plan

## Table of Contents

- [Context](#context)
- [Execution Overview](#execution-overview)
- [Block 0: Prerequisites](#block-0-prerequisites-you-do-once-then-walk-away)
- [Block A: Autonomous Coding](#block-a-autonomous-coding)
- [Block B: Autonomous Deployment](#block-b-autonomous-deployment)
- [Block C: Autonomous E2E Verification](#block-c-autonomous-e2e-verification)
- [Tool Strategy](#tool-strategy)
- [Risk Mitigations](#risk-mitigations)

## Context

The Rolodex MVP is fully specified across three documents (PRD, tech design, implementation plan) but has zero code. The goal is to autonomously build the entire project — 7 Python modules with full test coverage — deploy to GCP, and verify end-to-end, so you can just text a phone number to use it.

**Current state:** Only docs + `.env` with Gemini API key. No code, no infrastructure.
**System:** Python 3.14.2, pip3, Homebrew available. `gcloud` CLI **not installed**.

## Execution Overview

```
BLOCK 0: PREREQUISITES (you do once, then walk away)
  Install gcloud, authenticate, provide Twilio + GCP info

BLOCK A: AUTONOMOUS CODING
  Phase 0+1: Lead builds scaffolding + config (sequential)
  Phases 2+3+4: Agent team of 3 builds sheets/firestore/nlp (parallel)
  Phases 5+6: Agent team of 2 builds sms_handler/reminders (parallel)
  Phase 7: Lead builds Flask app (sequential)
  Result: ~55+ unit tests passing, all code built

BLOCK B: AUTONOMOUS DEPLOYMENT
  D1-D8: GCP APIs, service account, Sheets, Cloud Run, Scheduler, TTL

BLOCK C: AUTONOMOUS E2E VERIFICATION
  Health check → automated e2e tests → prompt tuning if needed
  Result: You text the number. It works.
```

**No stops.** After Block 0, I run Blocks A → B → C without pausing.

## Block 0: Prerequisites (you do once, then walk away)

Before I start, you provide:

1. **Install gcloud CLI:** `brew install google-cloud-sdk` then `gcloud auth login`
2. **GCP project ID:** Your existing project with billing enabled
3. **Twilio credentials:** Sign up at twilio.com, buy a phone number with SMS. Provide: Account SID, Auth Token, Phone Number
4. **Your phone number:** To register as the first Rolodex user

I'll add everything to `.env` and never ask you for anything again until it's done.

## Block A: Autonomous Coding

All unit tests use mocks — zero external dependencies.

### Phase 0+1: Scaffolding + Config (lead, sequential)

The lead builds the foundation that all teammates will depend on.

**Phase 0 — Project Scaffolding:**
- `.gitignore`, `requirements.txt`, `requirements-dev.txt`, `Procfile`
- `tests/conftest.py` with all shared fixtures (mock Firestore, gspread, Twilio, genai clients; sample data; env vars)
- Create venv, install dependencies, verify Python 3.14 compatibility
- Commit

**Phase 1 — config.py:**
- Write `tests/test_config.py` → run (fail) → implement `config.py` → run (pass)
- Env var loading, base64 credential decoding, constants, `send_sms()` helper
- Commit

### Phases 2+3+4: Data Layer + NLP (agent team of 3, parallel)

Three teammates, each owning their own files with zero overlap:

| Teammate | Test File | Source File | What |
|----------|-----------|-------------|------|
| **Sheets** | `tests/test_sheets_client.py` | `sheets_client.py` | Google Sheets CRUD (8 functions: get_user_by_phone, get_active_contacts, get_settings, update_contact, add_contact, add_log_entry, archive_contact, get_all_users) |
| **Firestore** | `tests/test_context.py` | `context.py` | Firestore state management — idempotency, batching, multi-turn context (3 collections, application-level TTL checks) |
| **NLP** | `tests/test_nlp.py` | `nlp.py` | Gemini integration — `parse_sms()` with structured prompt, `response_mime_type: "application/json"`, fallback parsing |

**Why this works as a team:**
- Zero file conflicts — each teammate owns exactly 2 files
- All three depend on `config.py` and `conftest.py` (read-only, built in Phase 0+1)
- No dependencies between them — sheets, firestore, and NLP are independent
- Each teammate follows TDD: write tests → run (fail) → implement → run (pass) → commit

### Phases 5+6: Handlers (agent team of 2, parallel)

After Phases 2-4 are done, two teammates build the handlers:

| Teammate | Test File | Source File | Dependencies | What |
|----------|-----------|-------------|--------------|------|
| **SMS** | `tests/test_sms_handler.py` | `sms_handler.py` | config, sheets_client, context, nlp | Full 13-step webhook orchestration — validate, idempotency, batch, Gemini, execute intent, send reply. Mocks `time.sleep(5)` in tests. |
| **Reminders** | `tests/test_reminder_handler.py` | `reminder_handler.py` | config, sheets_client | OIDC validation, per-user timezone date calc, day-of + 1-week-before reminders, SMS batching |

**Why this works:** Zero file conflicts. SMS handler is the most complex module (~20 min) while reminders is simpler (~15 min), so they finish around the same time.

### Phase 7: Flask App (lead, sequential)

After Phases 5+6, the lead builds the thin routing layer:
- Write `tests/test_app.py` → implement `app.py`
- POST /sms-webhook → delegates to sms_handler
- POST /reminder-cron → delegates to reminder_handler
- GET /health → returns 200 OK
- Commit

**Block A result:** All code written. All ~55+ unit tests passing.

## Block B: Autonomous Deployment

All credentials are in `.env` from Block 0. I run every command without stopping.

| Step | What | How |
|------|------|-----|
| D1 | Enable GCP APIs | `gcloud services enable` for Cloud Run, Firestore, Sheets, Scheduler |
| D2 | Create Firestore DB | `gcloud firestore databases create --location=us-central1` |
| D3 | Service account | Create SA, assign roles (datastore.user, run.invoker), generate key, base64-encode |
| D4 | Google Sheets | Python script using gspread: create master spreadsheet + your personal spreadsheet (Contacts/Logs/Settings tabs), share with SA email |
| D5 | Cloud Run deploy | `gcloud run deploy` with all env vars from `.env` |
| D6 | Cloud Scheduler | Create cron job `0 14 * * *` UTC with OIDC auth targeting `/reminder-cron` |
| D7 | Twilio webhook | Configure Twilio webhook URL to point to Cloud Run `/sms-webhook` via Twilio API |
| D8 | Firestore TTL | Apply TTL policies on `expire_at` for all 3 collections |

**If any step fails:** I diagnose, fix, and retry. Common issues (billing not linked, API not enabled, name collision) have known solutions.

## Block C: Autonomous E2E Verification

No manual SMS needed. E2E tests POST to the deployed webhook with computed Twilio signatures and verify via APIs.

1. **Health check:** `curl /health` → 200 OK
2. **Write `tests/test_e2e.py`:** 10 test cases from the implementation plan — log interaction, explicit timing, query, new contact, disambiguation, archive, custom reminder, day-of reminder, 1-week reminder, no-reminder-for-recent
3. **Run E2E tests:** Each test POSTs to the webhook, waits for processing, verifies Sheet state via gspread + outbound SMS via Twilio message history API
4. **Prompt tuning:** If Gemini misparses, iterate on the prompt in `nlp.py` and re-run
5. **Final commit**

**Verification complete when:** All 10 E2E tests pass. You text the Twilio number. It works.

## Tool Strategy

| Tool | When |
|------|------|
| **Agent teams** | Be sure to use [agent teams](https://code.claude.com/docs/en/agent-teams), NOT sub-agents. Phases 2+3+4 (3 teammates), Phases 5+6 (2 teammates). Each teammate owns separate files, no conflicts. Lead coordinates via shared task list. |
| **Lead (direct)** | Phases 0+1 (foundation), Phase 7 (Flask app), all of Blocks B+C (deployment + verification) |
| **Context7 MCP** | Teammates look up gspread, google-genai, Twilio SDK docs during implementation |
| **Sub-agents (Explore)** | Quick research if anyone hits an unexpected issue |
| **/commit skill** | After each passing phase |
| **Delegate mode** | Lead uses Shift+Tab during team phases to avoid doing implementation work itself |

**Why agent teams over sub-agents for Phases 2-4 and 5-6:**
- Each module is a substantial unit (~15-20 min of work) — big enough to justify a full teammate
- Teammates can read each other's committed code if needed (e.g., NLP teammate can see how sheets_client structures data)
- Teammates can message the lead if they hit issues, rather than silently failing
- Parallel implementation saves ~30 min in Block A

**Why not Ralph Loop:** The spec is detailed enough for straight-line TDD. Ralph Loop adds overhead when the path is already clear.

## Risk Mitigations

| Risk | Mitigation |
|------|-----------|
| Python 3.14 breaks dependencies | Check in Phase 0. Fall back to `pyenv install 3.12` |
| Teammate file conflict | Each teammate owns exactly 2 files — no overlap by design |
| Teammate inconsistent patterns | Lead builds conftest.py + config.py first; teammates read these as shared foundation |
| Twilio 15s timeout | `--min-instances=1` on Cloud Run (no cold start) |
| Gemini variable output | `response_mime_type: "application/json"` + fallback JSON extraction |
| Batch window multi-instance | `--max-instances=1` for MVP |
| Deployment failures | Diagnose + retry autonomously. Known failure patterns for GCP |
| E2E test flakiness from Gemini | Retry with prompt tuning. Assert on key phrases, not exact strings |

## Critical Files

| File | Role |
|------|------|
| `IMPLEMENTATION_PLAN.md` | Primary spec — test cases and function signatures |
| `TECH_DESIGN.md` | Architecture reference — webhook flow, Firestore schemas, Sheets schema |
| `PRD.md` | Behavioral spec — edge cases, matching rules, multi-turn flows |
| `tests/conftest.py` | Foundation for all testing — shared fixtures (built first, teammates read-only) |
| `nlp.py` | Gemini prompt — most likely to need post-deployment tuning |
| `sms_handler.py` | Core orchestration — most complex module |
