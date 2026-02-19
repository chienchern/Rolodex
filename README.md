# Rolodex

A personal CRM you text. Log interactions, query contact history, and get automatic follow-up reminders — all via Telegram, with Google Sheets as the data layer.

## Table of Contents

- [How it works](#how-it-works)
- [Example conversations](#example-conversations)
- [Architecture](#architecture)
- [Self-hosting setup](#self-hosting-setup)

---

## How it works

You text a Telegram bot in plain English. The app uses Gemini to parse your intent, updates your Google Sheet, and replies with a confirmation. A daily cron job at 9am sends reminder messages for contacts whose follow-up date has arrived.

Your data lives entirely in a Google Sheet you own — one tab for contacts, one for interaction logs, one for settings.

---

## Example conversations

```
— Tuesday, Feb 24 —

You:      Had coffee with Sarah, she's thinking of leaving Google
Rolodex:  Updated Sarah Chen. I'll remind you to reach out on Monday, Mar 9.

You:      Dinner with Marcus tonight, he's starting at OpenAI next month
Rolodex:  I don't have Marcus in your Rolodex. Want me to add him? Reply YES to confirm.

You:      YES
Rolodex:  Added Marcus. I'll remind you to follow up on Sunday, Mar 10.

You:      Actually follow up with him in 3 weeks
Rolodex:  Updated Marcus. Next reminder set for Monday, Mar 17.

You:      Rename Dave to David
Rolodex:  Updated: Dave → David.

You:      Remove Mike from my rolodex
Rolodex:  Archive Mike Torres from your Rolodex? Reply YES to confirm.

You:      YES
Rolodex:  Mike Torres has been archived.

— Sunday, Mar 1 —

You:      What did Sarah and I last talk about?
Rolodex:  Last spoke with Sarah Chen on Feb 24. She's thinking of leaving Google.

— Monday, Mar 9 —

Rolodex:  Time to reach out to Sarah Chen today.

          Last you told me: "Had coffee with Sarah, she's thinking of leaving Google."
```

---

## Architecture

```
User ──► Telegram ──► Cloud Run /telegram-webhook ──► Gemini ──► Google Sheets
                             │
                             ▼
                        Firestore
                   (context + idempotency)

Cloud Scheduler ──► Cloud Run /reminder-cron ──► Telegram ──► User
```

| Component | Choice |
|-----------|--------|
| Compute | Cloud Run (Flask, single instance) |
| NLP | Gemini API |
| Data store | Google Sheets (via gspread) |
| Ephemeral state | Firestore (multi-turn context, idempotency) |
| Messaging | Telegram Bot API |
| Scheduler | Cloud Scheduler (daily at 9am per user timezone) |

---

## Self-hosting setup

**~30 minutes total** — ~15 min of manual setup (Steps 1–2), ~15 min of Claude Code running autonomously (Step 3).

**Step 1 — Install Claude Code** ([claude.ai/code](https://claude.ai/code))

**Step 2 — Gather prerequisites (one-time, done by you):**

- [Install Telegram](https://telegram.org/) and create an account
- Authenticate gcloud: `gcloud auth login` ([install gcloud](https://cloud.google.com/sdk/docs/install) if needed)
- GCP project with billing enabled ([create one](https://console.cloud.google.com/))
- Gemini API key ([get one free](https://aistudio.google.com/app/apikey))
- Telegram bot token — message [@BotFather](https://t.me/BotFather) → `/newbot`
- Telegram chat ID — message [@userinfobot](https://t.me/userinfobot) on Telegram; it replies with your ID
- Two Google Sheets (create in [Google Sheets](https://sheets.google.com), note the ID from each URL):

  **Master sheet** — one tab named `Users` with columns: `phone`, `telegram_chat_id`, `name`, `sheet_id`

  **Personal sheet** — three tabs:
  - `Contacts` — columns: `name`, `reminder_date`, `last_contact_date`, `last_interaction_message`, `status`
  - `Logs` — columns: `date`, `contact_name`, `intent`, `raw_message`
  - `Settings` — columns: `key`, `value`. Add two rows: `timezone` | `America/New_York` (or your [IANA timezone](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)) and `default_reminder_days` | `14`

  Share both sheets with `rolodex-sa@<your-project-id>.iam.gserviceaccount.com` (Editor access). This is the service account Claude Code will create — you can share after it runs step 1 of the setup.

**Step 3 — Run Claude Code with this prompt** (Claude handles cloning + full setup):

```
Clone https://github.com/chienchern/Rolodex.git and set up my Rolodex on GCP.

Config:
- GCP project ID: [X]
- Gemini API key: [Y]
- Telegram bot token: [Z], secret token: [any random string]
- My name: [A], Telegram chat ID: [B], timezone: [e.g. America/New_York]
- Master sheet ID: [M], personal sheet ID: [P]

Handle everything: enable GCP APIs, create service account, Firestore with TTL
policies, deploy to Cloud Run, Cloud Scheduler for 9am daily reminders, register
Telegram webhook, add me as a user in the master sheet.
```

Claude Code will run all the commands autonomously. ~10 minutes.
