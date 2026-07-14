"""Core transform: NetSuite source export -> import-ready commission file.

Every rule here was verified against the June 2026 files:
  - individuals: Comm Amt / Commissionable >= 6% -> Individual Primary (4),
    else Individual General Agent (5). 0 mismatches on 1,611 rows.
  - group rows: broker name matched (case/space-insensitively) against the
    deal hierarchy -> Primary (1) / GA (2) / Managing GA (3).
    914/915 rows; the 1 exception was a manual override -> review dialog.
  - PEPM / Flat Rate / Sharx&Tech -> type 7 with the label written to Memo.
  - unclassifiable rows -> exceptions file (June: 113 rows).
"""
from __future__ import annotations
import re
import datetime as dt
from decimal import Decimal, ROUND_HALF_UP


def money2(v):
    """Round to cents the way NetSuite does: half-up (127.485 -> 127.49),
    not banker's rounding (which gives 127.48)."""
    if v is None or pd.isna(v):
        return None
    return float(Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
from dataclasses import dataclass, field

import pandas as pd

from . import config as C
from .lookups import Lookups
from .matching import NameMatcher, MatchResult


def clean_money(v) -> float | None:
    if pd.isna(v):
        return None
    s = re.sub(r"[$,\s]", "", str(v))
    try:
        return float(s)
    except ValueError:
        return None


def _norm(s) -> str:
    return " ".join(str(s).strip().lower().split()) if pd.notna(s) else ""


def format_comm_rate(src_rate, comm_amt, commissionable, is_type7: bool) -> str | None:
    """Output rate as 'X.XX%'. Type-7 (PEPM/Flat/Sharx) rows stay blank.
    Priority: numeric source rate -> percent in source text ('8% + Flat
    Rate' -> 8.00%) -> derived from Comm Amt / Commissionable."""
    if is_type7:
        return None
    if pd.notna(src_rate):
        try:
            return f"{float(src_rate) * 100:.2f}%"
        except (ValueError, TypeError):
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", str(src_rate))
            if m:
                return f"{float(m.group(1)):.2f}%"
    if comm_amt and commissionable:
        return f"{round(comm_amt / commissionable * 100, 2):.2f}%"
    return None


def format_adjustments(v):
    """Datetime adjustments -> 'M/D/YYYY H:MM' (e.g. 4/1/2026 0:00).
    Text adjustments ('Mar - May 2026') pass through unchanged."""
    if pd.isna(v):
        return None
    if isinstance(v, (pd.Timestamp, dt.datetime, dt.date)):
        t = pd.Timestamp(v)
        return f"{t.month}/{t.day}/{t.year} {t.hour}:{t.minute:02d}"
    return v


def is_group_row(row) -> bool:
    return str(row.get("ID", "")).strip().upper().startswith("G-")


@dataclass
class RunResult:
    output: pd.DataFrame            # import-ready rows
    exceptions: pd.DataFrame        # dropped rows + reason
    pending_review: list[MatchResult]  # unresolved broker names (pause here)
    stats: dict = field(default_factory=dict)
    # unresolved hierarchy mismatches (second pause point):
    # [{broker, group_id, group_name, rows, total, hierarchy}]
    pending_hierarchy: list = field(default_factory=list)


def classify_group_role(broker_name: str, hierarchy: dict) -> int | None:
    b = _norm(broker_name)
    if not b or not hierarchy:
        return None
    if b == _norm(hierarchy.get("primary", ("",))[0]) or \
       b == _norm(hierarchy.get("co_primary", ("",))[0]):
        return C.PRIMARY_BROKER
    if b == _norm(hierarchy.get("ga", ("",))[0]):
        return C.GENERAL_AGENT
    if b == _norm(hierarchy.get("mga", ("",))[0]):
        return C.MANAGING_GENERAL_AGENT
    return None


def classify_individual(comm_amt: float | None, commissionable: float | None) -> int | None:
    if not comm_amt or not commissionable:
        return None
    # round to 6 decimals: 16.26/271*100 floats to 5.999999999999999,
    # which must still count as the 6% boundary (matches Excel behavior)
    ratio = round(comm_amt / commissionable * 100, 6)
    return (C.INDIVIDUAL_PRIMARY if ratio >= C.INDIVIDUAL_PRIMARY_MIN_RATIO
            else C.INDIVIDUAL_GENERAL_AGENT)


def run_transform(source: pd.DataFrame, onhold_groups: set[str],
                  lookups: Lookups, matcher: NameMatcher,
                  period_mmyyyy: str, override_store=None,
                  hierarchy_rejects: set | None = None,
                  name_rejects: set | None = None,
                  exclude_nonpositive_vendors: bool = True) -> RunResult:
    """period_mmyyyy e.g. '062026'. If pending_review is non-empty the run
    should pause: resolve names (adding aliases), then call again."""
    df = source.copy()

    # --- 1. drop on-hold groups ------------------------------------------
    onhold_norm = {_norm(g) for g in onhold_groups if _norm(g)}
    df["_onhold"] = df["Group Name"].map(lambda g: _norm(g) in onhold_norm)

    # --- 2. sort: groups first (desc), then individuals (desc) -----------
    df["_isgroup"] = df.apply(is_group_row, axis=1)
    df = df.sort_values(["_isgroup", "ID"], ascending=[False, False],
                        kind="stable").reset_index(drop=True)

    # --- 3. resolve broker names -----------------------------------------
    name_rejects = name_rejects or set()
    unique_names = df["Brokers Name"].dropna().unique()
    resolutions: dict[str, MatchResult] = {n: matcher.match(n) for n in unique_names}
    pending = [m for m in resolutions.values()
               if not m.resolved and m.source_name not in name_rejects]
    if pending:  # pause for the review dialog before doing any more work
        return RunResult(pd.DataFrame(), pd.DataFrame(), pending,
                         {"unique_brokers": len(unique_names),
                          "unresolved": len(pending)})

    # --- 3b. detect hierarchy mismatches (second pause point) -------------
    hierarchy_rejects = hierarchy_rejects or set()
    pending_h: dict[tuple, dict] = {}
    for _, r in df.iterrows():
        src_type = str(r["Comm Type"]).strip() if pd.notna(r["Comm Type"]) else ""
        if src_type not in (C.GROUP_BROKER, C.GROUP_GA) or r["_onhold"]:
            continue
        broker = (resolutions[r["Brokers Name"]].matched_name
                  if pd.notna(r["Brokers Name"]) else None)
        gid = str(r["ID"]).strip() if pd.notna(r["ID"]) else ""
        if not broker or lookups.vendor_id_by_name.get(broker) is None:
            continue  # rejected/unknown names go to exceptions in the build
        hierarchy = lookups.hierarchy_by_group.get(gid, {})
        if classify_group_role(broker, hierarchy) is not None:
            continue
        if override_store is not None and override_store.get(broker, gid) is not None:
            continue
        if (broker, gid) in hierarchy_rejects:
            continue
        k = (broker, gid)
        amt = clean_money(r["Comm Amt"]) or 0.0
        if k not in pending_h:
            pending_h[k] = {"broker": broker, "group_id": gid,
                            "group_name": r.get("Group Name"),
                            "rows": 0, "total": 0.0,
                            "hierarchy": {p: hierarchy.get(p, ("", ""))[0]
                                          for p in ("primary", "co_primary", "ga", "mga")}}
        pending_h[k]["rows"] += 1
        pending_h[k]["total"] = round(pending_h[k]["total"] + amt, 2)
    if pending_h:
        return RunResult(pd.DataFrame(), pd.DataFrame(), [],
                         {"unresolved_hierarchy": len(pending_h)},
                         pending_hierarchy=list(pending_h.values()))

    # --- 4. row-by-row build ----------------------------------------------
    out_rows, exc_rows = [], []
    for _, r in df.iterrows():
        reason = None
        broker = resolutions[r["Brokers Name"]].matched_name if pd.notna(r["Brokers Name"]) else None
        vendor_id = lookups.vendor_id_by_name.get(broker) if broker else None
        gid = str(r["ID"]).strip() if pd.notna(r["ID"]) else ""
        hierarchy = lookups.hierarchy_by_group.get(gid, {})
        comm_amt = clean_money(r["Comm Amt"])
        commissionable = clean_money(r["Commissionable"])

        # classification
        ctype, memo = None, None
        src_type = str(r["Comm Type"]).strip() if pd.notna(r["Comm Type"]) else ""
        if r["_onhold"]:
            reason = "group on hold"
        elif vendor_id is None:
            reason = ("broker name rejected in review"
                      if pd.notna(r["Brokers Name"]) and r["Brokers Name"] in name_rejects
                      else "broker not matched to a vendor")
        elif src_type in C.TYPE7_MEMO:
            if not comm_amt:  # $0 PEPM/flat rows: nothing to pay (June rule)
                reason = "zero-amount PEPM/flat/sharx row"
            else:
                ctype, memo = C.ADDITIONAL, C.TYPE7_MEMO[src_type]
        elif src_type == C.INDIVIDUAL:
            # edge case (verified in June): rows labeled individual but
            # carrying a G- group ID belong to the group's deal hierarchy
            if gid.upper().startswith("G-"):
                ctype = classify_group_role(broker, hierarchy)
            if ctype is None:
                ctype = classify_individual(comm_amt, commissionable)
            reason = None if ctype else "individual row missing amounts"
        elif src_type in (C.GROUP_BROKER, C.GROUP_GA):
            ctype = classify_group_role(broker, hierarchy)
            if ctype is None and override_store is not None:
                ov = override_store.get(broker, gid)
                if isinstance(ov, dict):  # pay-to decision
                    role_map = {"primary": (C.PRIMARY_BROKER, "primary"),
                                "ga": (C.GENERAL_AGENT, "ga"),
                                "mga": (C.MANAGING_GENERAL_AGENT, "mga")}
                    if ov.get("pay_to") in role_map:
                        code, rkey = role_map[ov["pay_to"]]
                        target = hierarchy.get(rkey, (None, None))[0]
                        tvid = lookups.vendor_id_by_name.get(target) if target else None
                        if tvid is not None:
                            ctype, vendor_id = code, tvid
                        else:
                            reason = ("override points to deal "
                                      f"{ov['pay_to']} but none is on file")
                    elif ov.get("pay_to") == "self":
                        ctype = int(ov.get("code", 0)) or None
                elif ov is not None:  # legacy integer = keep broker, that code
                    ctype = int(ov)
            if reason is None:
                reason = None if ctype else "broker not found in deal hierarchy"
        else:
            reason = f"unknown comm type: {src_type}"

        h = {p: hierarchy.get(p, (None, None)) for p, _, _ in
             [(x[0], x[1], x[2]) for x in
              [("primary", 0, 0), ("co_primary", 0, 0), ("ga", 0, 0), ("mga", 0, 0)]]}
        vid = lookups.vendor_id_by_name  # shorthand

        row = {
            "External ID": (f"BROKER-COM-{period_mmyyyy}-{vendor_id}"
                            if vendor_id is not None else None),
            "Vendor": vendor_id,
            "Commission Type": ctype,
            "Subsidiary": C.SUBSIDIARY,
            "Account": C.ACCOUNT,
            "Expense Account": C.EXPENSE_ACCOUNT,
            "Broker Vendor Bill": C.BROKER_VENDOR_BILL,
            "Memo": memo,
            "Group ": lookups.group_internal_by_id.get(gid),
            "ID": gid,
            "Group Name": r.get("Group Name"),
            "Member First Name": r.get("Member First Name"),
            "Member Last Name": r.get("Member Last Name"),
            "Invoice Amt": clean_money(r["Invoice Amt"]),
            "Commissionable": commissionable,
            "Comm Rate": format_comm_rate(r.get("Comm Rate"), comm_amt,
                                          commissionable, ctype == C.ADDITIONAL),
            " Comm Amt ": comm_amt,
            "Product Label": r.get("Product Label"),
            "Posted Date": r.get("Posted Date"),
            "Transaction Paid Through Start": r.get("Transaction Paid Through Start"),
            "Transaction Paid Through End": r.get("Transaction Paid Through End"),
            "PEPM": r.get("PEPM"),
            "Adjustments": format_adjustments(r.get("Adjustments")),
            "Primary Broker Internal ID": vid.get(h["primary"][0]),
            "Deal Primary Broker": h["primary"][0],
            "Primary Broker Commission": h["primary"][1],
            "Co-Primary Internal ID": vid.get(h["co_primary"][0]),
            "Deal Co-Primary Broker": h["co_primary"][0],
            "Co-Primary Brokers Commission": h["co_primary"][1],
            "General Agent Internal ID": vid.get(h["ga"][0]),
            "Deal General Agent": h["ga"][0],
            "General Agent Commission": h["ga"][1],
            "Managing GA Internal ID": vid.get(h["mga"][0]),
            "Deal Managing GA": h["mga"][0],
            "Managing General Agents Commission": h["mga"][1],
        }
        if reason or ctype is None:
            # exceptions ship in the SAME import-ready layout so the file
            # can be fixed by hand and imported as a second upload
            exc_rows.append({**row, "Source Broker Name": r.get("Brokers Name"),
                             "Exception Reason": reason or "unclassified"})
        else:
            out_rows.append(row)

    output = pd.DataFrame(out_rows, columns=C.OUTPUT_COLUMNS)
    if exclude_nonpositive_vendors and len(output):
        vt = output.groupby("Vendor")[" Comm Amt "].transform("sum")
        bad = vt <= 0
        if bad.any():
            moved = output[bad]
            for _, mr in moved.iterrows():
                exc_rows.append({**mr.to_dict(),
                                 "Source Broker Name": None,
                                 "Exception Reason":
                                 "vendor total zero/negative - NetSuite "
                                 "cannot post this bill"})
            output = output[~bad].reset_index(drop=True)
    for col in ("Invoice Amt", "Commissionable", " Comm Amt ", "PEPM"):
        output[col] = pd.to_numeric(output[col], errors="coerce").map(money2)
    exceptions = pd.DataFrame(exc_rows)
    if len(exceptions):
        for col in ("Invoice Amt", "Commissionable", " Comm Amt ", "PEPM"):
            if col in exceptions.columns:
                exceptions[col] = pd.to_numeric(exceptions[col],
                                                errors="coerce").map(money2)
    # identical rounding basis to the output columns (half-up, like NetSuite)
    src_total = round(sum(money2(v) or 0.0
                          for v in (clean_money(x) for x in df["Comm Amt"])), 2)
    out_total = round(output[" Comm Amt "].sum(), 2) if len(output) else 0.0
    exc_total = (round(pd.to_numeric(exceptions[" Comm Amt "],
                  errors="coerce").fillna(0).sum(), 2) if len(exc_rows) else 0.0)
    stats = {
        "source_rows": len(df),
        "output_rows": len(output),
        "exception_rows": len(exceptions),
        "type_counts": output["Commission Type"].value_counts().to_dict() if len(output) else {},
        "source_total": src_total,
        "output_total": out_total,
        "exceptions_total": exc_total,
        "reconciliation_gap": round(src_total - out_total - exc_total, 2),
    }
    return RunResult(output, exceptions, [], stats)
