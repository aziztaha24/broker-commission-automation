"""Lookup tables used by the transform.

Three lookups, mirroring the NetSuite saved searches:
  1. vendors:       broker/vendor name -> vendor internal ID
  2. opportunities: Group ID -> deal hierarchy (primary / co-primary / GA /
                    managing GA, each with name + commission rate)
  3. customers:     Group ID (G-RDA...) -> internal group number

For now these load from CSV files; `netsuite.py` will populate the same
dataclass from SuiteQL once credentials are configured.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import pandas as pd

HIERARCHY_ROLES = [
    # (column prefix in opportunities file, output name column, output rate column)
    ("primary",    "Deal Primary Broker",    "Primary Broker Commission"),
    ("co_primary", "Deal Co-Primary Broker", "Co-Primary Brokers Commission"),
    ("ga",         "Deal General Agent",     "General Agent Commission"),
    ("mga",        "Deal Managing GA",       "Managing General Agents Commission"),
]


@dataclass
class Lookups:
    # name -> vendor internal id  (canonical vendor names)
    vendor_id_by_name: dict[str, int] = field(default_factory=dict)
    # group id (G-RDA...) -> {role: (name, rate_str)}
    hierarchy_by_group: dict[str, dict] = field(default_factory=dict)
    # group id -> internal group number
    group_internal_by_id: dict[str, float] = field(default_factory=dict)

    @property
    def canonical_names(self) -> list[str]:
        return list(self.vendor_id_by_name.keys())


def load_lookups(vendors_csv: str, opportunities_csv: str,
                 customers_csv: str) -> Lookups:
    lk = Lookups()

    v = pd.read_csv(vendors_csv)
    for _, r in v.iterrows():
        if pd.notna(r["name"]) and pd.notna(r["internal_id"]):
            lk.vendor_id_by_name[str(r["name"]).strip()] = int(r["internal_id"])

    o = pd.read_csv(opportunities_csv)
    for _, r in o.iterrows():
        gid = str(r["group_id"]).strip()
        roles = {}
        for prefix, name_col, rate_col in HIERARCHY_ROLES:
            nm, rt = r.get(f"{prefix}_name"), r.get(f"{prefix}_rate")
            if pd.notna(nm) and str(nm).strip():
                roles[prefix] = (str(nm).strip(),
                                 str(rt).strip() if pd.notna(rt) else "")
        lk.hierarchy_by_group[gid] = roles

    c = pd.read_csv(customers_csv)
    for _, r in c.iterrows():
        if pd.notna(r["group_id"]) and pd.notna(r["internal_group"]):
            lk.group_internal_by_id[str(r["group_id"]).strip()] = r["internal_group"]

    return lk
