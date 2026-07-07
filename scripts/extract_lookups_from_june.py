"""Derive provisional lookup CSVs from the June working copy + final file.
These stand in for the NetSuite saved searches (vendors, opportunities,
customers) until the real exports are provided. Same schema either way.
"""
import pandas as pd
import re, sys, os

SRC_DIR = sys.argv[1] if len(sys.argv) > 1 else "/mnt/project"
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "lookups")
os.makedirs(OUT, exist_ok=True)

wc = pd.read_csv(f"{SRC_DIR}/Final_Working_Copy.csv")
fin = pd.read_csv(f"{SRC_DIR}/FINAL_JUNE_BROKER_COMMISSION.csv")

# ---- vendors: name -> internal id -----------------------------------------
vendors = {}
for _, r in wc.dropna(subset=["Broker Name", "Vendor"]).iterrows():
    vendors[str(r["Broker Name"]).strip()] = int(r["Vendor"])
# hierarchy names also carry ids in the final file
pairs = [("Deal Primary Broker", "Primary Broker Internal ID"),
         ("Deal Co-Primary Broker", "Co-Primary Internal ID"),
         ("Deal General Agent", "General Agent Internal ID"),
         ("Deal Managing GA", "Managing GA Internal ID")]
for name_c, id_c in pairs:
    for _, r in fin.dropna(subset=[name_c, id_c]).iterrows():
        vendors.setdefault(str(r[name_c]).strip(), int(r[id_c]))
pd.DataFrame([{"name": k, "internal_id": v} for k, v in vendors.items()]) \
  .to_csv(f"{OUT}/vendors.csv", index=False)

# ---- opportunities: group id -> hierarchy ----------------------------------
rows = []
grp = wc[wc["ID"].astype(str).str.startswith("G-", na=False)]
for gid, g in grp.groupby("ID"):
    r = g.iloc[0]
    rows.append({
        "group_id": gid,
        "primary_name": r.get("Deal Primary Broker"),
        "primary_rate": r.get("Primary Broker Commission"),
        "co_primary_name": r.get("Deal Co-Primary Broker"),
        "co_primary_rate": r.get("Co-Primary Broker's Commission"),
        "ga_name": r.get("Deal General Agent"),
        "ga_rate": r.get("General Agent Commission"),
        "mga_name": r.get("Deal Managing GA"),
        "mga_rate": r.get("Managing General Agent's Commission"),
    })
pd.DataFrame(rows).to_csv(f"{OUT}/opportunities.csv", index=False)

# ---- customers: group id -> internal group number ---------------------------
cust = fin.dropna(subset=["ID", "Group "]).drop_duplicates("ID")
cust[["ID", "Group "]].rename(columns={"ID": "group_id", "Group ": "internal_group"}) \
    .to_csv(f"{OUT}/customers.csv", index=False)

print(f"vendors: {len(vendors)} | opportunities: {len(rows)} | customers: {len(cust)}")
