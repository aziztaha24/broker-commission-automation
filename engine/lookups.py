"""Lookup tables used by the transform.

Three lookups, mirroring the NetSuite saved searches:
  1. vendors:       broker/vendor name -> vendor internal ID
  2. opportunities: Group ID -> deal hierarchy (primary / co-primary / GA /
                    managing GA, each with name + commission rate)
  3. customers:     Group ID (G-RDA...) -> internal group number

Column headers are auto-detected: NetSuite exports use headers like
'Internal ID' / 'Name' / 'Company Name', so each logical field accepts a
list of synonyms (case/space-insensitive). A missing field raises
LookupSchemaError with a message that lists what was found vs expected —
shown verbatim in the app so the user can fix the export.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import pandas as pd


class LookupSchemaError(Exception):
    pass


def _canon(h: str) -> str:
    return "".join(str(h).lower().split()).replace("_", "").replace("-", "")


def _find_col(df: pd.DataFrame, synonyms: list[str], file_label: str,
              field_label: str, required: bool = True) -> str | None:
    canon_map = {_canon(c): c for c in df.columns}
    for s in synonyms:
        if _canon(s) in canon_map:
            return canon_map[_canon(s)]
    if required:
        raise LookupSchemaError(
            f"{file_label}: couldn't find a '{field_label}' column. "
            f"Accepted names: {synonyms}. "
            f"Columns in your file: {list(df.columns)}")
    return None


VENDOR_NAME_SYNS = ["name", "vendor name", "vendor", "company name",
                    "broker name", "entity", "entity id", "legal name"]
VENDOR_ID_SYNS = ["internal_id", "internal id", "id", "vendor internal id"]

GROUP_ID_SYNS = ["group_id", "group id", "group #", "group number", "id",
                 "group", "customer id", "company id", "opportunity group id"]
INTERNAL_GROUP_SYNS = ["internal_group", "internal id", "internal group",
                       "internal group id", "group internal id", "customer internal id"]

HIER_SYNS = {
    "primary_name": ["primary_name", "deal primary broker", "primary broker",
                     "primary broker name"],
    "primary_rate": ["primary_rate", "primary broker commission",
                     "primary broker rate", "primary commission"],
    "co_primary_name": ["co_primary_name", "deal co-primary broker",
                        "co-primary broker", "co primary broker"],
    "co_primary_rate": ["co_primary_rate", "co-primary broker's commission",
                        "co-primary brokers commission", "co-primary commission",
                        "co primary broker commission"],
    "ga_name": ["ga_name", "deal general agent", "general agent",
                "general agent name"],
    "ga_rate": ["ga_rate", "general agent commission", "general agent rate",
                "ga commission"],
    "mga_name": ["mga_name", "deal managing ga", "managing general agent",
                 "managing ga", "deal managing general agent"],
    "mga_rate": ["mga_rate", "managing general agent's commission",
                 "managing general agents commission", "managing ga commission",
                 "mga commission"],
}

HIERARCHY_ROLES = [
    ("primary",    "Deal Primary Broker",    "Primary Broker Commission"),
    ("co_primary", "Deal Co-Primary Broker", "Co-Primary Brokers Commission"),
    ("ga",         "Deal General Agent",     "General Agent Commission"),
    ("mga",        "Deal Managing GA",       "Managing General Agents Commission"),
]


@dataclass
class Lookups:
    vendor_id_by_name: dict[str, int] = field(default_factory=dict)
    hierarchy_by_group: dict[str, dict] = field(default_factory=dict)
    group_internal_by_id: dict[str, float] = field(default_factory=dict)

    @property
    def canonical_names(self) -> list[str]:
        return list(self.vendor_id_by_name.keys())


def load_lookups(vendors_csv, opportunities_csv, customers_csv) -> Lookups:
    """Args may be file paths or uploaded file objects (Streamlit)."""
    lk = Lookups()

    v = pd.read_csv(vendors_csv)
    ncol = _find_col(v, VENDOR_NAME_SYNS, "Vendors file", "vendor name")
    icol = _find_col(v, VENDOR_ID_SYNS, "Vendors file", "internal id")
    for _, r in v.iterrows():
        if pd.notna(r[ncol]) and pd.notna(r[icol]):
            try:
                lk.vendor_id_by_name[str(r[ncol]).strip()] = int(float(r[icol]))
            except (ValueError, TypeError):
                continue

    c = pd.read_csv(customers_csv)
    ggcol = _find_col(c, GROUP_ID_SYNS, "Customers file", "group id")
    igcol = _find_col(c, INTERNAL_GROUP_SYNS, "Customers file", "internal group number")
    for _, r in c.iterrows():
        if pd.notna(r[ggcol]) and pd.notna(r[igcol]):
            lk.group_internal_by_id[str(r[ggcol]).strip()] = r[igcol]

    def _numkey(v) -> str:
        """normalize 31587 / 31587.0 / '31587' to '31587'"""
        try:
            return str(int(float(str(v).strip())))
        except (ValueError, TypeError):
            return str(v).strip()

    # reverse map: internal group number -> G-RDA id (used if the
    # opportunities export references groups by internal number)
    rda_by_internal = {_numkey(v): k for k, v in lk.group_internal_by_id.items()}

    o = pd.read_csv(opportunities_csv)
    gcol = _find_col(o, GROUP_ID_SYNS, "Opportunities file", "group id")
    cols = {k: _find_col(o, syns, "Opportunities file", k, required=("name" in k))
            for k, syns in HIER_SYNS.items()}
    for _, r in o.iterrows():
        gid = str(r[gcol]).strip()
        if not gid or gid.lower() == "nan":
            continue
        if not gid.upper().startswith("G-"):
            gid = rda_by_internal.get(_numkey(gid), gid)
        roles = {}
        for prefix, _, _ in HIERARCHY_ROLES:
            nc, rc = cols[f"{prefix}_name"], cols.get(f"{prefix}_rate")
            nm = r[nc] if nc else None
            rt = r[rc] if rc else None
            if pd.notna(nm) and str(nm).strip():
                roles[prefix] = (str(nm).strip(),
                                 str(rt).strip() if pd.notna(rt) else "")
        lk.hierarchy_by_group[gid] = roles

    return lk
