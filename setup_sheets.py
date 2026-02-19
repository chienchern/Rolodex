"""Set up Rolodex spreadsheet tabs, headers, and data."""

import json
import gspread

SPREADSHEET_ID = "198fKum5ZxE-OCf_Ml8ueiXYFbU9vvsWFC8psBwC76Gs"

with open("/Users/chienchernkhor/Rolodex/sa-key.json") as f:
    creds = json.load(f)

gc = gspread.service_account_from_dict(creds)
spreadsheet = gc.open_by_key(SPREADSHEET_ID)

# Rename default sheet to Users
sheet1 = spreadsheet.sheet1
sheet1.update_title("Users")
sheet1.update("A1:C1", [["phone", "name", "sheet_id"]])
sheet1.append_row(["+19177032788", "Chien Chern", SPREADSHEET_ID])
print("Users tab done")

# Contacts tab
contacts = spreadsheet.add_worksheet(title="Contacts", rows=1000, cols=5)
contacts.update("A1:E1", [["name", "reminder_date", "last_contact_date", "last_interaction_message", "status"]])
print("Contacts tab done")

# Logs tab
logs = spreadsheet.add_worksheet(title="Logs", rows=1000, cols=4)
logs.update("A1:D1", [["date", "contact_name", "intent", "raw_message"]])
print("Logs tab done")

# Settings tab
settings = spreadsheet.add_worksheet(title="Settings", rows=10, cols=2)
settings.update("A1:B3", [
    ["key", "value"],
    ["timezone", "America/New_York"],
    ["default_reminder_days", "14"],
])
print("Settings tab done")

print(f"\nSpreadsheet ready: https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}")
