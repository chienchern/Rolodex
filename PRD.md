# Rolodex MVP - Product Requirements Document

## Table of Contents

- [Overview](#overview)
- [User Problem](#user-problem)
- [Solution](#solution)
- [Core Features](#core-features)
  - [Contact Data Model](#1-contact-data-model-google-sheets)
  - [SMS Commands](#2-sms-commands-natural-language-processing)
  - [Contact Matching & Management](#3-contact-matching--management)
  - [Automated Reminders](#4-automated-reminders-sms)
  - [Onboarding / Setup](#5-onboarding--setup)
- [User Workflows](#user-workflows)
- [Technical Requirements](#technical-requirements)
- [MVP Scope](#mvp-scope-whats-included)
- [Out of Scope for MVP](#out-of-scope-for-mvp)
- [Success Metrics](#success-metrics)
- [Known Limitations](#known-limitations)
- [Open Questions / Future Considerations](#open-questions--future-considerations)
- [Next Steps](#next-steps)

## Overview
A relationship management system that helps track contacts, conversation history, and proactively reminds the user when to reach out to people they want to build relationships with. Supports 2-3 users, each with their own spreadsheet.

## User Problem
- Need to maintain relationships with important contacts
- Hard to remember when last spoke with someone and what was discussed
- Need prompts to reach out at appropriate intervals
- Want low-friction way to log interactions and view contact history

## Solution
SMS-based interface for on-the-go updates + Google Sheets for data viewing and editing. Each user gets their own Google Sheet; a master spreadsheet maps users to their sheets.

---

## Core Features

### 1. Contact Data Model (Google Sheets)

A master spreadsheet contains a Users tab that maps each user's phone number to their personal spreadsheet. Each user's spreadsheet has a three-tab structure:

**Users Tab** (in master spreadsheet, one row per user):
- **phone**: User's phone number (e.g., `+15551234567`)
- **name**: User's name
- **sheet_id**: ID of the user's personal Google Sheet

**Contacts Tab** (per-user spreadsheet, one row per contact):
- **name**: Contact's full name (primary lookup key for SMS matching)
- **reminder_date**: The date the user receives a reminder SMS. Set explicitly by the user (e.g., "follow up next Friday") or auto-computed as `last_contact_date` + `default_reminder_days` when logging an interaction. Empty means no pending reminder.
- **last_contact_date**: When you last interacted with them
- **last_contact_notes**: Most recent interaction notes (overwrite field — replaced only when a new interaction is logged via SMS Command A. Acts as a "quick glance" snapshot. Full history lives in the Logs tab.)
- **status**: active or archived (default: active). Only active contacts appear in search results and receive reminders.

**Logs Tab** (per-user spreadsheet, one row per interaction):
- **date**: When the interaction occurred
- **contact_name**: Contact name
- **intent**: The classified intent (e.g., `log_interaction`, `set_reminder`)
- **notes**: What was discussed or reason for reminder
- **raw_message**: The original SMS text, stored for debugging when the LLM misparses

**Settings Tab** (per-user spreadsheet, key-value configuration):
- **timezone**: User's timezone in IANA format (e.g., `America/New_York`)
- **default_reminder_days**: Default follow-up interval in days (e.g., `14`)

The master spreadsheet ID is configured as an environment variable (`MASTER_SHEET_ID`). The backend looks up the user's phone number in the Users tab to find their personal spreadsheet.

### 2. SMS Commands (Natural Language Processing)

Users can text the system using natural language for:

**A. Log Interaction**
- Example: "Met with John, discussed his startup funding"
- Example (multiple people): "Met with John and Sarah, discussed the project timeline"
- System behavior:
  - Parses contact name(s) from message
  - If no name specified, asks: "Who did you meet with?"
  - If multiple people mentioned, updates all known contacts first, then enters the confirmation flow for any unknown names (does not hold the entire command)
  - Updates last_contact_date to today
  - Appends a new row to the Logs tab with date, contact_name, intent, notes, and the raw_message (original SMS text for debugging)
  - Overwrites last_contact_notes on the Contacts tab
  - If timing specified ("follow up in 3 weeks"), sets reminder_date accordingly and confirms with the precise day and date: "Updated John. Next reminder set for Monday, Mar 2, 2026."
  - If timing NOT specified, sets reminder_date to today + default_reminder_days (from Settings tab) and confirms with the precise day and date: "Updated John. I'll remind you to reach out on Monday, Feb 23, 2026."

**B. Custom Reminder**
- Example: "Remind me about Sarah in 2 weeks"
- System behavior:
  - Updates reminder_date to specified time
  - Confirms via SMS with the precise day and date: "Reminder set for Sarah on Monday, Feb 23, 2026."

**C. Query Last Contact**
- Example: "When did I last talk to John?"
- System behavior:
  - Returns: "Last spoke with John on Jan 15, 2026. Discussed: his new job at TechCorp"

**D. Archive Contact**
- Example: "Delete John" or "Remove Sarah from rolodex"
- System behavior:
  - Asks for confirmation: "Archive John from your rolodex? Reply YES to confirm"
  - If confirmed, sets the contact's status to "archived" in the Contacts tab
  - Archived contacts are excluded from name matching and reminders, but all data (contact row + logs) is preserved
  - If the user later mentions the same name (e.g., logs a new interaction with "John Smith"), the system treats them as a new contact (the archived row is not reactivated)
  - To reactivate an archived contact, the user must manually change the status back to "active" in Google Sheets

### 3. Contact Matching & Management

**Matching Logic:**
- When user texts about a contact, the system extracts the name from the message and matches it against active contacts in the name column
- The user's phone number identifies the user (authentication), not the contact
- If multiple active contacts share the same first name, system asks: "Which John? (John Smith, John Doe)"
- User can disambiguate using:
  - Full name: "John Smith"
  - Nickname/shorthand: "John S"

**Multi-turn Context Retention:**
- When the system asks a clarifying question (e.g., "Which John?" or "Who did you meet with?"), it caches the original message context in Firestore with a 10-minute TTL
- Cached context expires after 10 minutes. If the user responds after expiration, the system treats the reply as a new message.
- If the next message from the user is a new intent rather than a clarification response (e.g., user ignores "Which John?" and texts something unrelated), the system discards the pending context and processes the new message fresh
- Once the user responds with clarification (within the 10-minute window), the system executes the original intent immediately without requiring the user to retype the full message
- Example: User: "Met John for coffee" -> System: "Which John?" -> User: "Doe" -> System processes the full "Met John Doe for coffee" interaction

**New Contact Handling:**
- If SMS mentions an unknown person:
  1. Check for similar existing names (fuzzy match) and ask: "I don't see 'Jon' in your Rolodex. Did you mean 'John'? Reply YES to match, or NEW to create 'Jon'."
  2. If no similar names exist, ask for confirmation: "I don't have 'Alex' in your Rolodex. Want me to add them? Reply YES to confirm."
  3. If confirmed, create new row in Contacts tab with status = active
  4. Populate name from SMS
  5. Set last_contact_date to today
  6. Set reminder_date to today + default_reminder_days (from Settings tab)
  7. Confirm to user: "Added [Name] to your rolodex. I'll remind you to follow up on [Day], [Date]." (e.g., "Added Alex to your rolodex. I'll remind you to follow up on Monday, Feb 23, 2026.")

### 4. Automated Reminders (SMS)

**Reminder Cadence:**
- Reminders are only sent for active contacts with a non-empty reminder_date.
- **If reminder_date is > 7 days from last_contact_date:** Send two reminders:
  - **1 week before reminder_date**: "Reminder: Reach out to John in 1 week (last spoke about startup funding)"
  - **Day of reminder_date**: "Today: Reach out to John (last spoke on Jan 15, 2026 about startup funding)"
- **If reminder_date is ≤ 7 days from last_contact_date:** Send one reminder:
  - **Day of** only: "Today: Reach out to John (last spoke on Jan 15 about startup funding)"

**Reminder Timing:**
- A daily cron job fetches all user spreadsheets, identifies reminders due, and sends them at 9am in each user's local timezone (read from Settings tab)

**Reminder Content:**
- Contact name
- When you last spoke (date)
- What you discussed (latest notes)
- Call to action

### 5. Onboarding / Setup

When a new user first texts the system:
1. System infers timezone from the user's phone number area code
2. System sends a confirmation: "Welcome to Rolodex! Based on your number, it looks like you're in New York (EST). Is that right?"
3. User can confirm or correct (e.g., "No, I'm in LA")
4. System writes the confirmed timezone and user phone number to the Settings tab
5. System confirms: "Got it, set to Pacific Time. You're all set! Text me after your next meeting to get started."

---

## User Workflows

### Workflow 1: Log a New Interaction
1. User has coffee with John
2. User texts: "Had coffee with John today, talked about his new startup idea"
3. System parses message via Gemini, matches "John" against active contacts in the name column
4. System updates Contacts tab (last_contact_date, overwrites last_contact_notes) and adds a row to Logs tab (with intent, notes, and raw_message)
5. System responds: "Updated John. I'll remind you to reach out on Monday, Feb 23, 2026."

### Workflow 2: Query Contact History
1. User texts: "When did I last talk to Sarah?"
2. System looks up Sarah by name in the Contacts tab
3. System responds: "Last spoke with Sarah on Jan 20, 2026. Discussed: her job search"

### Workflow 3: Custom Follow-up Timing
1. User texts: "Met with Alex, discussed partnership, follow up in 1 month"
2. System parses timing instruction via Gemini
3. System updates Contacts tab (last_contact_date, last_contact_notes) and Logs tab; sets reminder_date to 1 month from today
4. System responds: "Updated Alex. Next reminder set for Monday, Mar 9, 2026."

### Workflow 4: Receive & Act on Reminder
1. System sends SMS: "Today: Reach out to John (last spoke on Jan 15 about startup funding)"
2. User reaches out to John
3. User texts system with update (returns to Workflow 1)

### Workflow 5: View/Edit Data Directly
1. User opens their personal Google Sheet on laptop
2. Reviews Contacts tab — can sort by reminder_date, filter by name, etc.
3. Reviews Logs tab for full interaction history
4. Manually edits notes or adjusts timing for specific contacts
5. Changes take effect on the next system read (inbound SMS or next daily cron run)

### Workflow 6: Archive
1. User texts: "Remove John from my rolodex"
2. System confirms: "Archive John from your rolodex? Reply YES to confirm"
3. User replies: "YES"
4. System sets John's status to archived. John no longer appears in search or reminders.

---

## Technical Requirements

### SMS Service (Twilio)
- Receive inbound SMS from user via Twilio webhook
- Send outbound SMS for reminders and confirmations via Twilio API
- Validate inbound requests using Twilio's `X-Twilio-Signature` header
- Handle natural language parsing of user messages (via Gemini)
- Handle standard SMS compliance keywords: STOP (unsubscribe from all messages), START/UNSTOP (resubscribe), HELP (return support info). Required by TCPA/CTIA guidelines — Twilio enforces this and may suspend numbers that don't comply.

### Google Sheets Integration
- Master spreadsheet (ID configured as `MASTER_SHEET_ID` environment variable) with Users tab mapping phone numbers to per-user spreadsheets
- Each user's spreadsheet has a three-tab structure: Contacts tab + Logs tab + Settings tab
- Google Sheets is the source of truth — no separate database
- **Read strategy:**
  - **Inbound SMS:** Look up user's spreadsheet via master Users tab, then read their sheet on-demand (low volume, real-time read is fine)
  - **Outbound reminders:** Daily cron job iterates over all users, fetches each user's sheet, identifies reminders due, and sends them at 9am in each user's timezone
- **Concurrency:** Google Sheets API has rate limits and no native row-level locking. If multiple SMS arrive in rapid succession, the backend uses sleep-based message batching (5-second window) to collect rapid-fire texts and process them as a single batch, avoiding race conditions.

### Natural Language Processing (Gemini)
- Uses Google's Gemini API to parse user SMS into structured JSON
- Input to Gemini: user's SMS text, list of active contact names, pending multi-turn context (if any), current date with day-of-week in user's timezone
- Extracts:
  - Intent (`log_interaction`, `query`, `set_reminder`, `archive`, `onboarding`, `clarify`, `unknown`)
  - Contact name(s) with match type (`exact`, `fuzzy`, `new`, `ambiguous`)
  - Interaction notes/content
  - Follow-up date (if specified)
  - Response message to send back to user
- **Date parsing:** Gemini should attempt to interpret all timing expressions, including ambiguous ones like "next Tuesday" or "in a couple weeks." If Gemini can resolve the expression to a specific date, confirm it to the user: "Reminder set for March 3 (Monday)." If Gemini cannot confidently parse the expression, respond: "I couldn't understand that timing. Could you try again? (e.g., 'in 2 weeks', 'in 3 days')"

### Scheduling/Reminder System
- Track reminder_date for all active contacts (empty means no pending reminder)
- Send SMS reminders at appropriate intervals (1 week before + day of, or day of only for short intervals)
- Daily cron job fetches all user spreadsheets and queues reminders
- Handle time zones appropriately (per-user timezone from Settings tab)

### Contact Matching
- Contact names are passed directly in the Gemini prompt; the LLM resolves matches (works for <100 contacts per user)
- Match mentioned names to name column in Contacts tab (active contacts only)
- Handle ambiguity (multiple contacts with same first name)
- Fuzzy matching for typo detection on new contact creation

---

## MVP Scope (What's Included)

- ✅ SMS logging of interactions (natural language)
- ✅ Google Sheets as data store (master spreadsheet with Users tab + per-user spreadsheets with Contacts, Logs, Settings tabs)
- ✅ Automated reminders (1 week before + day of, with smart short-interval handling)
- ✅ Query last contact via SMS
- ✅ New contact creation with confirmation (fuzzy match to prevent typo duplicates)
- ✅ Configurable default follow-up interval (default_reminder_days in Settings tab)
- ✅ Custom follow-up timing via SMS (Gemini-powered date parsing with confirmation)
- ✅ Full interaction history in Logs tab
- ✅ Group meeting logging (multiple people in one message)
- ✅ Contact archiving via SMS or manual sheet edit (soft delete to "archived" status, no automatic reactivation)
- ✅ Missing name handling (system asks for clarification)
- ✅ Name disambiguation for duplicate first names (full name or nickname)
- ✅ Multi-turn context retention for clarification flows (Firestore with 10-min TTL)
- ✅ Sleep-based message batching (5s window for rapid multi-text sequences)
- ✅ Idempotency via Twilio MessageSid dedup (Firestore)
- ✅ Scheduled reminders at 9am local time
- ✅ Onboarding flow with infer-and-confirm timezone setup
- ✅ SMS compliance (STOP/START/HELP keywords)

## Out of Scope for MVP

- ❌ Multiple phone numbers per contact
- ❌ Rich formatting in notes
- ❌ Attachment/photo storage
- ❌ Integration with calendar
- ❌ Contact categories/tags
- ❌ Analytics/relationship insights
- ❌ Group chat / threading (system does not facilitate conversations between multiple parties)
- ❌ Email interface (SMS only)
- ❌ Multi-user/sharing
- ❌ Mobile app (SMS is the interface)
- ❌ Undo command (NLP misparses are corrected manually in the Google Sheet)

---

## Success Metrics

- User logs at least 3 interactions per week via SMS
- User responds to/acts on at least 50% of reminders
- System correctly parses 90%+ of natural language SMS commands
- Zero data loss (Google Sheets provides backup/history)

---

## Known Limitations

- **Weekend/holiday scheduling:** If a reminder_date falls on a weekend or holiday, the system does not automatically adjust to the next business day. Reminders are sent on the exact calculated date.
- **Name-based matching only:** Contacts are matched by name via LLM prompt (not by any unique ID). This works for <100 contacts per user. Unusual or very common names may occasionally require disambiguation.
- **Manual sheet edits and reminders:** The reminder cron job runs once daily. If a user manually changes a reminder_date in the sheet to "today" after the cron has already run, that reminder will not fire until the next day's cron run. Edits should be made before 9am local time to take effect same-day.
- **No undo command:** If Gemini misparses a message (e.g., updates "Robert" instead of "Robbie"), the user must correct it manually in the Google Sheet. The raw_message column in the Logs tab is preserved for debugging. There is no SMS-based undo for MVP.
- **No SMS-based reactivation of archived contacts:** Once a contact is archived, mentioning their name again creates a new contact rather than reactivating the archived one. To reactivate, the user must manually change the status back to "active" in Google Sheets.
- **5-second batching delay:** Every interaction has a minimum 5-second delay due to the sleep-based message batching window, even for single messages.

---

## Open Questions / Future Considerations

1. What's the SMS message length limit to consider? (Technical constraint)
2. How to handle SMS delivery failures? (Technical implementation detail)
3. Should contacts have multiple phone numbers? (Currently: one per contact)
4. If a user manually deletes a row from the Contacts tab (rather than archiving via SMS) and later texts about that person again, the system treats them as a brand new contact. The orphaned Logs rows remain but are not linked.

---

## Next Steps

1. ~~Review and finalize this PRD~~ ✅
2. ~~Technical design~~ ✅ (see TECH_DESIGN.md — Twilio for SMS, Cloud Run/Flask backend, Gemini for NLP, Firestore for context/idempotency)
3. Set up Google Sheets: master spreadsheet with Users tab + per-user spreadsheets with Contacts, Logs, Settings tabs. Share all with service account.
4. Build MVP implementation
5. Test with real contacts and iterate
