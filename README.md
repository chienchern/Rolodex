# Rolodex

A personal CRM you text. Log interactions, query contact history, and get automatic follow-up reminders — all via Telegram or SMS, with Google Sheets as the data layer.

## Table of Contents

- [How it works](#how-it-works)
- [Example conversations](#example-conversations)
  - [Logging an interaction](#logging-an-interaction)
  - [Custom follow-up timing](#custom-follow-up-timing)
  - [Querying a contact](#querying-a-contact)
  - [Automated reminders](#automated-reminders)
  - [Adding a new contact](#adding-a-new-contact)
  - [Renaming a contact](#renaming-a-contact)
  - [Archiving a contact](#archiving-a-contact)
- [Architecture](#architecture)
- [Self-hosting setup](#self-hosting-setup)
  - [Prerequisites](#prerequisites)
  - [1. GCP project and APIs](#1-gcp-project-and-apis)
  - [2. Service account](#2-service-account)
  - [3. Firestore](#3-firestore)
  - [4. Google Sheets](#4-google-sheets)
  - [5. Messaging channel](#5-messaging-channel)
  - [6. Deploy to Cloud Run](#6-deploy-to-cloud-run)
  - [7. Cloud Scheduler](#7-cloud-scheduler)
  - [8. Connect your messaging channel](#8-connect-your-messaging-channel)
  - [9. Add yourself as a user](#9-add-yourself-as-a-user)

---

## How it works

You text a bot (Telegram or SMS) in plain English. The app uses Gemini to parse your intent, updates your Google Sheet, and replies with a confirmation. A daily cron job at 9am sends reminder messages for contacts whose follow-up date has arrived.

Your data lives entirely in a Google Sheet you own — one tab for contacts, one for interaction logs, one for settings.

---

## Example conversations

### Logging an interaction

> **You:** Had coffee with Sarah today, she's thinking about leaving her job

> **Rolodex:** Updated Sarah Chen. I'll remind you to reach out on Monday, Mar 9, 2026.

---

> **You:** Dinner with James last Friday, talked about his move to Austin

> **Rolodex:** Updated James. Existing reminder on Tuesday, Mar 3, 2026 unchanged.

*(If the contact already has a future reminder set, Rolodex leaves it alone.)*

---

### Custom follow-up timing

> **You:** Lunch with Dad, follow up in 3 weeks

> **Rolodex:** Updated Dad. Next reminder set for Monday, Mar 16, 2026.

---

> **You:** Caught up with Maria, she's applying for jobs — check in with her in 4 days

> **Rolodex:** Updated Maria. Next reminder set for Friday, Feb 28, 2026.

---

### Querying a contact

> **You:** When did I last talk to Mike?

> **Rolodex:** Last spoke with Mike Torres on Feb 3, 2026. Message: Grabbed lunch with Mike, he started his new job at Google.

---

### Automated reminders

On the morning your follow-up date arrives:

> **Rolodex:** Today: Reach out to Sarah Chen (last spoke on Feb 10, 2026 — Had coffee with Sarah, she's thinking about leaving her job)

If the reminder is more than 7 days out, you get an advance heads-up one week before:

> **Rolodex:** Reminder: Reach out to John Smith in 1 week (last spoke — Coffee with John, discussed his travel plans)

---

### Adding a new contact

> **You:** Dinner with Priya, she works in VC

> **Rolodex:** I don't have Priya in your Rolodex. Want me to add them? Reply YES to confirm.

> **You:** YES

> **Rolodex:** Added Priya to your Rolodex. I'll remind you to follow up on Monday, Mar 9, 2026.

If there's a similar name already in your sheet, Rolodex checks first:

> **You:** Met with Jon for coffee

> **Rolodex:** I don't see "Jon" in your Rolodex. Did you mean "John Smith"? Reply YES to match, or NEW to create "Jon".

---

### Renaming a contact

> **You:** Rename Robert to Rob

> **Rolodex:** Updated: Robert → Rob.

---

### Archiving a contact

> **You:** Remove Sarah from my rolodex

> **Rolodex:** Archive Sarah Chen from your Rolodex? Reply YES to confirm.

> **You:** YES

> **Rolodex:** Sarah Chen has been archived.

Archived contacts are hidden from search results and reminders, but all data is preserved in the sheet.

---

## Architecture

```
User Telegram ──► Telegram  ──► Cloud Run /telegram-webhook ──► Gemini ──► Google Sheets
User SMS      ──► Twilio    ──► Cloud Run /sms-webhook      ──►    │            ▲
                                        │                          └────────────┘
                                        ▼
                                   Firestore
                              (context + idempotency)

Cloud Scheduler ──► Cloud Run /reminder-cron ──► Telegram / Twilio ──► User
```

| Component | Choice |
|-----------|--------|
| Compute | Cloud Run (Flask, single instance) |
| NLP | Gemini API |
| Data store | Google Sheets (via gspread) |
| Ephemeral state | Firestore (multi-turn context, idempotency) |
| Messaging | Telegram Bot API or Twilio SMS |
| Scheduler | Cloud Scheduler (daily at 9am per user timezone) |

---

## Self-hosting setup

**Time to set up: ~45 minutes.** You'll need a GCP account with billing enabled.

### Prerequisites

- [`gcloud` CLI](https://cloud.google.com/sdk/docs/install) installed and authenticated (`gcloud auth login`)
- A [Google Cloud project](https://console.cloud.google.com/) with billing enabled
- A [Gemini API key](https://aistudio.google.com/app/apikey)
- Either a [Telegram bot](https://t.me/BotFather) (free) or a [Twilio account](https://www.twilio.com/) with a phone number (~$1.15/month)

---

### 1. GCP project and APIs

```bash
export PROJECT_ID=your-project-id
gcloud config set project $PROJECT_ID

gcloud services enable \
  run.googleapis.com \
  firestore.googleapis.com \
  sheets.googleapis.com \
  cloudscheduler.googleapis.com
```

---

### 2. Service account

```bash
gcloud iam service-accounts create rolodex-sa \
  --display-name="Rolodex Service Account"

# Grant Firestore and Sheets access
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:rolodex-sa@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/datastore.user"

# Download the key and base64-encode it for the env var
gcloud iam service-accounts keys create sa-key.json \
  --iam-account=rolodex-sa@$PROJECT_ID.iam.gserviceaccount.com

export GSPREAD_CREDENTIALS_B64=$(base64 -i sa-key.json)
```

Note the service account email — you'll need it to share your Google Sheets.

---

### 3. Firestore

```bash
gcloud firestore databases create --location=us-central1

# Set TTL policies on the expire_at field for each collection
gcloud firestore fields ttls update expire_at \
  --collection-group=processed_messages --enable-ttl
gcloud firestore fields ttls update expire_at \
  --collection-group=context --enable-ttl
```

---

### 4. Google Sheets

Create two spreadsheets manually in [Google Sheets](https://sheets.google.com):

**Master spreadsheet** — one sheet, tab named `Users`:

| phone | telegram_chat_id | name | sheet_id |
|-------|-----------------|------|----------|
| | | | |

Note the spreadsheet ID from the URL (`/spreadsheets/d/<ID>/`).

**Your personal spreadsheet** — four tabs:

- **Contacts** — columns: `name`, `reminder_date`, `last_contact_date`, `last_interaction_message`, `status`
- **Logs** — columns: `date`, `contact_name`, `intent`, `raw_message`
- **Settings** — columns: `key`, `value`. Add two rows:
  - `timezone` | `America/New_York` (or your [IANA timezone](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones))
  - `default_reminder_days` | `14`

Share **both** spreadsheets with the service account email (`Editor` access).

---

### 5. Messaging channel

**Option A — Telegram (recommended, free):**

1. Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` → follow prompts
2. Save the bot token as `TELEGRAM_BOT_TOKEN`
3. Choose a secret token string (any random string) as `TELEGRAM_SECRET_TOKEN`

**Option B — SMS via Twilio:**

1. Sign up at [twilio.com](https://www.twilio.com), buy a phone number with SMS
2. Save Account SID, Auth Token, and phone number

---

### 6. Deploy to Cloud Run

```bash
gcloud run deploy rolodex \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --min-instances=1 \
  --max-instances=1 \
  --set-env-vars "GEMINI_API_KEY=<your-key>,\
MASTER_SHEET_ID=<master-sheet-id>,\
GSPREAD_CREDENTIALS_B64=<base64-creds>,\
MESSAGING_CHANNEL=telegram,\
TELEGRAM_BOT_TOKEN=<bot-token>,\
TELEGRAM_SECRET_TOKEN=<secret-token>"
```

For SMS instead, replace the last three lines with:
```bash
  --set-env-vars "...,MESSAGING_CHANNEL=sms,\
TWILIO_ACCOUNT_SID=<sid>,\
TWILIO_AUTH_TOKEN=<token>,\
TWILIO_PHONE_NUMBER=<+1...>"
```

Note the Cloud Run URL from the deploy output.

---

### 7. Cloud Scheduler

This sends daily reminders at 9am UTC (adjust the cron schedule to match your timezone):

```bash
# Create a service account for the scheduler to invoke Cloud Run
gcloud iam service-accounts create rolodex-scheduler \
  --display-name="Rolodex Scheduler"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:rolodex-scheduler@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/run.invoker"

gcloud scheduler jobs create http rolodex-reminder-cron \
  --schedule="0 14 * * *" \
  --uri="https://<your-cloud-run-url>/reminder-cron" \
  --http-method=POST \
  --oidc-service-account-email="rolodex-scheduler@$PROJECT_ID.iam.gserviceaccount.com" \
  --location=us-central1
```

`0 14 * * *` fires at 14:00 UTC = 9am EST. Adjust for your timezone.

---

### 8. Connect your messaging channel

**Telegram:**

Register your Cloud Run URL as the webhook:

```bash
curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  -d "url=https://<your-cloud-run-url>/telegram-webhook" \
  -d "secret_token=<TELEGRAM_SECRET_TOKEN>"
```

**Twilio SMS:**

In the [Twilio Console](https://console.twilio.com), go to your phone number's configuration and set the webhook URL for incoming messages to:
```
https://<your-cloud-run-url>/sms-webhook
```

---

### 9. Add yourself as a user

**Telegram:** Find your chat ID by messaging your bot, then calling:
```
https://api.telegram.org/bot<TOKEN>/getUpdates
```
Look for `message.chat.id` in the response.

**SMS:** Your phone number in E.164 format (e.g., `+15551234567`).

Add a row to the `Users` tab of your master spreadsheet:

| phone | telegram_chat_id | name | sheet_id |
|-------|-----------------|------|----------|
| | 123456789 | Your Name | `<your-personal-sheet-id>` |

You're live. Send your bot a message.
