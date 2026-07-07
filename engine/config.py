"""Constants and configuration for the broker commission pipeline."""

# Hardcoded values applied to every output row
SUBSIDIARY = 4
ACCOUNT = 975
EXPENSE_ACCOUNT = 796
BROKER_VENDOR_BILL = "Yes"

# Commission Type internal IDs (from the NetSuite list)
PRIMARY_BROKER = 1
GENERAL_AGENT = 2
MANAGING_GENERAL_AGENT = 3
INDIVIDUAL_PRIMARY = 4
INDIVIDUAL_GENERAL_AGENT = 5
ADDITIONAL = 7  # PEPM / Flat Rate / Sharx&Tech

# Source "Comm Type" text -> handling
GROUP_BROKER = "Group Invoices for Broker Commissions"
GROUP_GA = "Group Invoices for General Agent Commissions"
INDIVIDUAL = "Individual Invoices for Broker Commissions"
TYPE7_MEMO = {  # source comm type -> memo text written on the row
    "Group PEPM Commissions": "PEPM",
    "Group Flat Rate Commission": "Flat Rate",
    "Group Sharx&Tech Commissions": "Sharx&Tech",
}

# Individuals: Comm Amt / Commissionable >= this (in %) -> Individual Primary
INDIVIDUAL_PRIMARY_MIN_RATIO = 6.0

# Fuzzy matching thresholds (0-100)
AUTO_ACCEPT_SCORE = 100      # exact/alias only auto-accepts
SUGGEST_MIN_SCORE = 60       # below this we show "no close match"

# Exact column order of the NetSuite import file
# (note: ' Comm Amt ' has surrounding spaces in the historical file — kept for
#  compatibility with the existing saved import map)
OUTPUT_COLUMNS = [
    "External ID", "Vendor", "Commission Type", "Subsidiary", "Account",
    "Expense Account", "Broker Vendor Bill", "Memo", "Group ", "ID",
    "Group Name", "Member First Name", "Member Last Name", "Invoice Amt",
    "Commissionable", "Comm Rate", " Comm Amt ", "Product Label",
    "Posted Date", "Transaction Paid Through Start",
    "Transaction Paid Through End", "PEPM", "Adjustments",
    "Primary Broker Internal ID", "Deal Primary Broker",
    "Primary Broker Commission", "Co-Primary Internal ID",
    "Deal Co-Primary Broker", "Co-Primary Brokers Commission",
    "General Agent Internal ID", "Deal General Agent",
    "General Agent Commission", "Managing GA Internal ID",
    "Deal Managing GA", "Managing General Agents Commission",
]
