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
- **Phone Number**: Contact's phone number (informational; not used for matching)
- **Last Contact Date**: When you last interacted with them
- **Next Contact Date**: When you should reach out next
- **Latest Notes**: Most recent interaction notes (overwrite field — replaced only when a new interaction is logged via SMS Command A. Stores the raw SMS text; if over 280 characters, the system summarizes via LLM before storing. Acts as a "quick glance" snapshot. Full history lives in the Logs tab.)
- **Status**: Active or Archived (default: Active). Only Active contacts appear in search results and receive reminders.

**Logs Tab** (one row per interaction):
- **Date**: When the interaction occurred
- **Name**: Contact name
- **Note**: What was discussed

**Settings Tab** (key-value configuration):
- **Timezone**: User's timezone in IANA format (e.g., `America/New_York`)
- **User Phone**: User's phone number (e.g., `+15550000000`)

The Settings Tab keeps the Google Sheet self-contained as both database and configuration, making the backend stateless. The backend only needs the Sheet ID (configured as an environment variable) to operate.

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
  - Overwrites Latest Notes on the Contacts tab (raw SMS text, or LLM summary if over 280 characters)
  - If timing specified ("follow up in 3 weeks"), sets next contact date accordingly and confirms with the precise day and date: "Updated John. Next reminder set for Monday, Mar 2, 2026."
  - If timing NOT specified, defaults to 2 weeks and confirms with the precise day and date: "Updated John. I'll remind you to reach out on Monday, Feb 23, 2026."

**B. Custom Reminder**
- Example: "Remind me about Sarah in 2 weeks"
- System behavior:
  - Updates next contact date to specified time
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
  5. Phone number field left blank (user can add manually in sheet)
  6. Set last contact date to today
  7. Set default next contact date to 2 weeks
  8. Confirm to user: "Added [Name] to your rolodex. I'll remind you to follow up on [Day], [Date]." (e.g., "Added Alex to your rolodex. I'll remind you to follow up on Monday, Feb 23, 2026.")

### 4. Automated Reminders (SMS)

**Reminder Cadence:**
- **If next contact date is > 7 days away:** Send two reminders:
  - **1 week before**: "Reminder: Reach out to John in 1 week (last spoke about startup funding)"
  - **Day of**: "Today: Reach out to John (last spoke on Jan 15, 2026 about startup funding)"
- **If next contact date is ≤ 7 days away:** Send one reminder:
  - **Day of** only: "Today: Reach out to John (last spoke on Jan 15 about startup funding)"
- Reminders are only sent for Active contacts.

**Reminder Timing:**
- All reminders sent at 9am in user's local timezone (read from Settings tab)

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
3. System parses message, matches "John" against Active contacts in the Name column
4. System updates Contacts tab (last contact date, overwrites latest notes) and adds a row to Logs tab
5. System responds: "Updated John. I'll remind you to reach out on Monday, Feb 23, 2026."

### Workflow 2: Query Contact History
1. User texts: "When did I last talk to Sarah?"
2. System looks up Sarah by name in the Contacts tab
3. System responds: "Last spoke with Sarah on Jan 20, 2026. Discussed: her job search"

### Workflow 3: Custom Follow-up Timing
1. User texts: "Met with Alex, discussed partnership, follow up in 1 month"
2. System parses timing instruction
3. System updates Contacts tab (last contact date, latest notes) and Logs tab; sets next contact date to 1 month from today
4. System responds: "Updated Alex. Next reminder set for Monday, Mar 9, 2026."

### Workflow 4: Receive & Act on Reminder
1. System sends SMS: "Today: Reach out to John (last spoke on Jan 15 about startup funding)"
2. User reaches out to John
3. User texts system with update (returns to Workflow 1)

### Workflow 5: View/Edit Data Directly
1. User opens Google Sheet on laptop
2. Reviews Contacts tab — can sort by next contact date, filter by name, etc.
3. Reviews Logs tab for full interaction history
4. Manually edits notes or adjusts timing for specific contacts
5. Changes take effect on the next system read (inbound SMS or next daily cron run)

### Workflow 6: Archive
1. User texts: "Remove John from my rolodex"
2. System confirms: "Archive John from your rolodex? Reply YES to confirm"
3. User replies: "YES"
4. System sets John's Status to Archived. John no longer appears in search or reminders.

---

## Technical Requirements

### SMS Service
- Receive inbound SMS from user
- Send outbound SMS for reminders and confirmations
- Handle natural language parsing of user messages
- Handle standard SMS compliance keywords: STOP (unsubscribe from all messages), START/UNSTOP (resubscribe), HELP (return support info). Required by TCPA/CTIA guidelines — SMS providers like Twilio enforce this and may suspend numbers that don't comply.

### Google Sheets Integration
- Read/write access to designated Google Sheet (Sheet ID configured as environment variable)
- Three-tab structure: Contacts tab + Logs tab + Settings tab
- Google Sheets is the source of truth — no separate database
- **Read strategy:**
  - **Inbound SMS:** Read sheet on-demand when a user message is received (low volume, real-time read is fine)
  - **Outbound reminders:** Daily cron job fetches the sheet once per day, identifies all reminders due in the next 24 hours, and queues them for delivery at 9am local time
- **Concurrency:** Google Sheets API has rate limits and no native row-level locking. If multiple SMS arrive in rapid succession, a naive implementation may cause race conditions or data overwrites. The backend must process sheet writes sequentially (e.g., via a simple message queue, or by using Google Apps Script as the webhook receiver, which handles sheet locking natively).

### Natural Language Processing
- Parse user SMS to extract:
  - Contact name
  - Interaction notes/content
  - Timing instructions (if specified)
  - Intent (log interaction, query, set reminder, archive)
- **Date parsing:** The LLM should attempt to interpret all timing expressions, including ambiguous ones like "next Tuesday" or "in a couple weeks." If the LLM can resolve the expression to a specific date, confirm it to the user: "Reminder set for March 3 (Monday)." If the LLM cannot confidently parse the expression, respond: "I couldn't understand that timing. Could you try again? (e.g., 'in 2 weeks', 'in 3 days')"

### Scheduling/Reminder System
- Track next contact dates for all Active contacts
- Send SMS reminders at appropriate intervals (1 week before + day of, or day of only for short intervals)
- Daily cron job to fetch and queue reminders
- Handle time zones appropriately (per-user timezone from Settings tab)

### Contact Matching
- Match mentioned names to Name column in Contacts tab (Active contacts only)
- Handle ambiguity (multiple contacts with same first name)
- Fuzzy matching for typo detection on new contact creation

---

## MVP Scope (What's Included)

- ✅ SMS logging of interactions (natural language)
- ✅ Google Sheets as data store (three-tab structure: Contacts + Logs + Settings)
- ✅ Automated reminders (1 week before + day of, with smart short-interval handling)
- ✅ Query last contact via SMS
- ✅ New contact creation with confirmation (fuzzy match to prevent typo duplicates)
- ✅ Default 2-week follow-up interval
- ✅ Custom follow-up timing via SMS (LLM-powered date parsing with confirmation)
- ✅ Full interaction history in Logs tab
- ✅ Group meeting logging (multiple people in one message)
- ✅ Contact archiving via SMS or manual sheet edit (soft delete, no automatic reactivation)
- ✅ Missing name handling (system asks for clarification)
- ✅ Name disambiguation for duplicate first names (full name or nickname)
- ✅ Multi-turn context retention for clarification flows
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

- **Weekend/holiday scheduling:** If a follow-up date falls on a weekend or holiday, the system does not automatically adjust to the next business day. Reminders are sent on the exact calculated date.
- **Name-based matching only:** Contacts are matched by name, not by any unique ID. Unusual or very common names may occasionally require disambiguation.
- **Manual sheet edits and reminders:** The reminder cron job runs once daily. If a user manually changes a Next Contact Date in the sheet to "today" after the cron has already run, that reminder will not fire until the next day's cron run. Edits should be made before 9am local time to take effect same-day.
- **No undo command:** If the NLP misparses a message (e.g., updates "Robert" instead of "Robbie"), the user must correct it manually in the Google Sheet. There is no SMS-based undo for MVP.
- **No SMS-based reactivation of archived contacts:** Once a contact is archived, mentioning their name again creates a new contact rather than reactivating the archived one. To reactivate, the user must manually change the Status back to "Active" in Google Sheets.

---

## Open Questions / Future Considerations

1. What's the SMS message length limit to consider? (Technical constraint)
2. How to handle SMS delivery failures? (Technical implementation detail)
3. Should contacts have multiple phone numbers? (Currently: one per contact)
4. If a user manually deletes a row from the Contacts tab (rather than archiving via SMS) and later texts about that person again, should the system treat them as a brand new contact? (For MVP: yes, treat as new. The orphaned Logs rows remain but are not linked.)

---

## Next Steps

1. Review and finalize this PRD
2. Technical design: Choose SMS provider (Twilio?), backend architecture, NLP approach
3. Set up Google Sheet template structure (Contacts tab + Logs tab + Settings tab)
4. Build MVP implementation
5. Test with real contacts and iterate
