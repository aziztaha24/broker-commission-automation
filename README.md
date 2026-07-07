# Broker commission automation

Monthly NetSuite broker-commission pipeline: source export -> classified,
import-ready CSV + exceptions file, with an in-app review dialog for
broker-name mismatches (accepted matches are remembered).

## Verified against June 2026
Reproduces the June final file: 2,580 rows, identical commission-type
distribution. Residual differences are the 3 manual-override rows, which
correctly land in the exceptions file for human review.

## Rules implemented (engine/transform.py)
- Individuals: Comm Amt / Commissionable >= 6% -> type 4, else 5
  (6% boundary handled with rounding; verified on 1,611 rows).
- Rows with a G- group ID (even if labeled individual) classify by deal
  hierarchy: primary/co-primary -> 1, GA -> 2, managing GA -> 3.
- Group PEPM / Flat Rate / Sharx&Tech -> type 7, label written to Memo;
  $0-amount rows dropped.
- OnHold groups excluded. Unclassifiable rows -> exceptions CSV.
- External ID = BROKER-COM-{MMYYYY}-{vendor internal id}.
- Constants: Subsidiary 4, Account 975, Expense Account 796,
  Broker Vendor Bill = Yes.

## Run locally
    pip install -r requirements.txt
    streamlit run app.py

## Deploy free (Streamlit Community Cloud)
1. Push this folder to a GitHub repo.
2. share.streamlit.io -> New app -> pick the repo, main file app.py.
3. App settings -> Secrets: add  APP_PASSWORD = "yourpassword"

## Swapping in real NetSuite data
The bundled lookups in data/lookups/ were derived from the June working
copy for testing. Replace with saved-search exports using these columns:
- vendors.csv:        name, internal_id
- opportunities.csv:  group_id, primary_name, primary_rate,
                      co_primary_name, co_primary_rate, ga_name, ga_rate,
                      mga_name, mga_rate
- customers.csv:      group_id, internal_group
Direct SuiteQL pull + CsvImportTask upload go in engine/netsuite.py once
the OAuth integration record exists (secrets in Streamlit's manager).

## Alias store
data/aliases.json maps source broker names -> canonical vendor names.
NOTE: Streamlit Cloud's disk is ephemeral — for production, move the alias
store to Supabase free Postgres (only AliasStore.load/save change).
