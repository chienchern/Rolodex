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
- [MVP Scope](#mvp-scope-whats-included)
- [Out of Scope for MVP](#out-of-scope-for-mvp)
- [Success Metrics](#success-metrics)
- [Known Limitations](#known-limitations)
- [Open Questions / Future Considerations](#open-questions--future-considerations)

## Overview
A relationship management system that helps track contacts, conversation history, and proactively reminds the user when to reach out to people they want to build relationships with.

## User Problem
- Need to maintain relationships with important contacts
- Hard to remember when last spoke with someone and what was discussed
- Need prompts to reach out at appropriate intervals
- Want low-friction way to log interactions and view contact history

## Solution
SMS-based interface for on-the-go updates + Google Sheets for data viewing and editing.

---

## Core Features

### 1. Contact Data Model (Google Sheets)

The Google Sheet uses a three-tab structure:

**Contacts Tab** (one row per contact):
- **Name**: Contact's full name (primary lookup key for SMS matching)
- **Last Contact Date**: When you last interacted with them
- **Reminder Date**: When the system will remind you to reach out. Set explicitly ("follow up in 3 weeks") or auto-computed as last contact date + default reminder interval. Empty means no pending reminder.
- **Last Contact Notes**: Most recent interaction notes (overwrite field — replaced only when a new interaction is logged via SMS Command A. Acts as a "quick glance" snapshot. Full history lives in the Logs tab.)
- **Status**: Active or Archived (default: Active). Only Active contacts appear in search results and receive reminders.

**Logs Tab** (one row per interaction):
- Stores date, contact name, and notes for each interaction
- Full schema defined in tech design

**Settings Tab** (key-value configuration):
- **Timezone**: User's timezone in IANA format (e.g., `America/New_York`)
- **Default Reminder Days**: Default follow-up interval in days (default: 14)

Each user has their own spreadsheet. A master spreadsheet maps user phone numbers to their personal spreadsheets, keeping the backend stateless.

### 2. SMS Commands (Natural Language Processing)

Users can text the system using natural language for:

**A. Log Interaction**
- Example: "Met with John, discussed his startup funding"
- Example (multiple people): "Met with John and Sarah, discussed the project timeline"
- System behavior:
  - Parses contact name(s) from message
  - If no name specified, asks: "Who did you meet with?"
  - If multiple people mentioned, updates all known contacts first, then enters the confirmation flow for any unknown names (does not hold the entire command)
  - Updates last contact date to today
  - Appends a new row to the Logs tab with timestamp and note
  - Overwrites Last Contact Notes on the Contacts tab
  - If timing specified ("follow up in 3 weeks"), sets reminder date accordingly and confirms with the precise day and date: "Updated John. Next reminder set for Monday, Mar 2, 2026."
  - If timing NOT specified, sets reminder date using the default interval (configurable per user in Settings tab, default: 2 weeks) and confirms with the precise day and date: "Updated John. I'll remind you to reach out on Monday, Feb 23, 2026."
  - If the system cannot parse a timing expression, it responds: "I couldn't understand that timing. Could you try again? (e.g., 'in 2 weeks', 'in 3 days')"

**B. Custom Reminder**
- Example: "Remind me about Sarah in 2 weeks"
- System behavior:
  - Updates reminder date to specified time
  - Confirms via SMS with the precise day and date: "Reminder set for Sarah on Monday, Feb 23, 2026."

**C. Query Last Contact**
- Example: "When did I last talk to John?"
- System behavior:
  - Returns: "Last spoke with John on Jan 15, 2026. Discussed: his new job at TechCorp"

**D. Archive Contact**
- Example: "Delete John" or "Remove Sarah from rolodex"
- System behavior:
  - Asks for confirmation: "Archive John from your rolodex? Reply YES to confirm"
  - If confirmed, sets the contact's Status to "Archived" in the Contacts tab
  - Archived contacts are excluded from name matching and reminders, but all data (contact row + logs) is preserved
  - If the user later mentions the same name (e.g., logs a new interaction with "John Smith"), the system treats them as a new contact (the archived row is not reactivated)
  - To reactivate an archived contact, the user must manually change the Status back to "Active" in Google Sheets

### 3. Contact Matching & Management

**Matching Logic:**
- When user texts about a contact, the system extracts the name from the message and matches it against Active contacts in the Name column
- The user's phone number identifies the user (authentication), not the contact
- If multiple Active contacts share the same first name, system asks: "Which John? (John Smith, John Doe)"
- User can disambiguate using:
  - Full name: "John Smith"
  - Nickname/shorthand: "John S"

**Multi-turn Context Retention:**
- When the system asks a clarifying question (e.g., "Which John?" or "Who did you meet with?"), it must cache the original message context
- Cached context expires after 10 minutes. If the user responds after expiration, the system treats the reply as a new message.
- Once the user responds with clarification (within the 10-minute window), the system executes the original intent immediately without requiring the user to retype the full message
- Example: User: "Met John for coffee" -> System: "Which John?" -> User: "Doe" -> System processes the full "Met John Doe for coffee" interaction

**New Contact Handling:**
- If SMS mentions an unknown person:
  1. Check for similar existing names (fuzzy match) and ask: "I don't see 'Jon' in your Rolodex. Did you mean 'John'? Reply YES to match, or NEW to create 'Jon'."
  2. If no similar names exist, ask for confirmation: "I don't have 'Alex' in your Rolodex. Want me to add them? Reply YES to confirm."
  3. If confirmed, create new row in Contacts tab with Status = Active
  4. Populate name from SMS
  5. Set last contact date to today
  6. Set reminder date using default interval
  8. Confirm to user: "Added [Name] to your rolodex. I'll remind you to follow up on [Day], [Date]." (e.g., "Added Alex to your rolodex. I'll remind you to follow up on Monday, Feb 23, 2026.")

### 4. Automated Reminders (SMS)

**Reminder Cadence:**
- **If reminder date is > 7 days away:** Send two reminders:
  - **1 week before**: "Reminder: Reach out to John in 1 week (last spoke about startup funding)"
  - **Day of**: "Today: Reach out to John (last spoke on Jan 15, 2026 about startup funding)"
- **If reminder date is ≤ 7 days away:** Send one reminder:
  - **Day of** only: "Today: Reach out to John (last spoke on Jan 15 about startup funding)"
- Reminders are only sent for Active contacts.

**Reminder Timing:**
- Reminders sent daily at 9am EST

**Reminder Content:**
- Contact name
- When you last spoke (date)
- What you discussed (latest notes)
- Call to action

### 5. Onboarding / Setup

Users are pre-provisioned: an admin creates each user's personal spreadsheet (with Contacts, Logs, and Settings tabs) and adds their phone number and spreadsheet ID to the master Users tab. No self-service onboarding for MVP.

---

## User Workflows

### Workflow 1: Log a New Interaction
1. User has coffee with John
2. User texts: "Had coffee with John today, talked about his new startup idea"
3. System parses message, matches "John" against Active contacts in the Name column
4. System updates Contacts tab (last contact date, overwrites last contact notes) and adds a row to Logs tab
5. System responds: "Updated John. I'll remind you to reach out on Monday, Feb 23, 2026."

### Workflow 2: Query Contact History
1. User texts: "When did I last talk to Sarah?"
2. System looks up Sarah by name in the Contacts tab
3. System responds: "Last spoke with Sarah on Jan 20, 2026. Discussed: her job search"

### Workflow 3: Custom Follow-up Timing
1. User texts: "Met with Alex, discussed partnership, follow up in 1 month"
2. System parses timing instruction
3. System updates Contacts tab (last contact date, last contact notes) and Logs tab; sets reminder date to 1 month from today
4. System responds: "Updated Alex. Next reminder set for Monday, Mar 9, 2026."

### Workflow 4: Receive & Act on Reminder
1. System sends SMS: "Today: Reach out to John (last spoke on Jan 15 about startup funding)"
2. User reaches out to John
3. User texts system with update (returns to Workflow 1)

### Workflow 5: View/Edit Data Directly
1. User opens Google Sheet on laptop
2. Reviews Contacts tab — can sort by reminder date, filter by name, etc.
3. Reviews Logs tab for full interaction history
4. Manually edits notes or adjusts timing for specific contacts
5. Changes take effect on the next system read (inbound SMS or next daily cron run at 9am EST)

### Workflow 6: Archive
1. User texts: "Remove John from my rolodex"
2. System confirms: "Archive John from your rolodex? Reply YES to confirm"
3. User replies: "YES"
4. System sets John's Status to Archived. John no longer appears in search or reminders.

---

## MVP Scope (What's Included)

- ✅ SMS logging of interactions (natural language)
- ✅ Google Sheets as data store (three-tab structure: Contacts + Logs + Settings)
- ✅ Automated reminders (1 week before + day of, with smart short-interval handling)
- ✅ Query last contact via SMS
- ✅ New contact creation with confirmation (fuzzy match to prevent typo duplicates)
- ✅ Configurable default follow-up interval (default: 2 weeks, per-user setting)
- ✅ Custom follow-up timing via SMS (LLM-powered date parsing with confirmation)
- ✅ Full interaction history in Logs tab
- ✅ Group meeting logging (multiple people in one message)
- ✅ Contact archiving via SMS or manual sheet edit (soft delete, no automatic reactivation)
- ✅ Missing name handling (system asks for clarification)
- ✅ Name disambiguation for duplicate first names (full name or nickname)
- ✅ Multi-turn context retention for clarification flows
- ✅ Multiple independent users (2-3), each with their own spreadsheet
- ✅ Scheduled reminders daily at 9am EST
- ✅ Pre-provisioned user setup (admin creates spreadsheet, adds to master Users tab)

## Out of Scope for MVP

- ❌ Multiple phone numbers per contact
- ❌ Rich formatting in notes
- ❌ Attachment/photo storage
- ❌ Integration with calendar
- ❌ Contact categories/tags
- ❌ Analytics/relationship insights
- ❌ Group chat / threading (system does not facilitate conversations between multiple parties)
- ❌ Email interface (SMS only)
- ❌ Shared contacts between users (each user has independent data)
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

- **Weekend/holiday scheduling:** If a follow-up date falls on a weekend or holiday, the system does not automatically adjust to the next business day. Reminders are sent on the exact calculated date.
- **Name-based matching only:** Contacts are matched by name, not by any unique ID. Unusual or very common names may occasionally require disambiguation.
- **Manual sheet edits and reminders:** The reminder cron job runs once daily at 9am EST. If a user manually changes a Reminder Date in the sheet to "today" after the cron has already run, that reminder will not fire until the next day's cron run. Edits should be made before 9am EST to take effect same-day.
- **No undo command:** If the NLP misparses a message (e.g., updates "Robert" instead of "Robbie"), the user must correct it manually in the Google Sheet. There is no SMS-based undo for MVP.
- **No SMS-based reactivation of archived contacts:** Once a contact is archived, mentioning their name again creates a new contact rather than reactivating the archived one. To reactivate, the user must manually change the Status back to "Active" in Google Sheets.

---

## Open Questions / Future Considerations

1. What's the SMS message length limit to consider? (Technical constraint)
2. How to handle SMS delivery failures? (Technical implementation detail)
3. Should contacts have multiple phone numbers? (Currently: one per contact)
4. If a user manually deletes a row from the Contacts tab (rather than archiving via SMS) and later texts about that person again, should the system treat them as a brand new contact? (For MVP: yes, treat as new. The orphaned Logs rows remain but are not linked.)

