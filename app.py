"""Broker commission automation — Streamlit app.

Run locally:   streamlit run app.py
Deploy free:   push to GitHub -> share.streamlit.io -> New app
Secrets:       set APP_PASSWORD in .streamlit/secrets.toml (or the
               Streamlit Cloud secrets manager). NetSuite OAuth secrets
               go there too once the integration record exists.
"""
import io
import json
import datetime as dt

import pandas as pd
import streamlit as st

from engine.lookups import load_lookups
from engine.matching import AliasStore, NameMatcher
from engine.transform import run_transform

st.set_page_config(page_title="Broker commissions", page_icon="📄", layout="wide")

# ---------------------------------------------------------------- auth gate
def check_password() -> bool:
    if st.session_state.get("authed"):
        return True
    pw = st.text_input("Password", type="password")
    if pw:
        if pw == st.secrets.get("APP_PASSWORD", "changeme"):
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Wrong password")
    return False

if not check_password():
    st.stop()

# ------------------------------------------------------------------- state
ALIAS_PATH = "data/aliases.json"
HISTORY_PATH = "data/run_history.jsonl"
ss = st.session_state
ss.setdefault("decisions", {})     # source_name -> canonical | "__REJECT__"
ss.setdefault("h_rejects", set())  # (broker, group_id) rejected this session
ss.setdefault("n_rejects", set())  # source names rejected this session
ss.setdefault("result", None)

st.title("Broker commission run")

# ------------------------------------------------------------------ inputs
c1, c2 = st.columns([1, 2])
with c1:
    today = dt.date.today()
    period = st.text_input("Period (MMYYYY)", value=today.strftime("%m%Y"))
with c2:
    st.caption("Data source: file upload now; NetSuite saved-search pull "
               "activates once OAuth credentials are configured in secrets.")

ns_ready = all(k in st.secrets for k in
                ("NS_ACCOUNT_ID", "NS_CLIENT_ID", "NS_CERT_ID", "NS_PRIVATE_KEY"))
pull_ns = st.toggle("Pull lookups directly from NetSuite", value=False,
                    disabled=not ns_ready,
                    help=("Requires NetSuite OAuth secrets — see "
                          "SETUP_NETSUITE.md" if not ns_ready else
                          "Vendors, opportunities and customers are pulled "
                          "live via SuiteQL; no lookup uploads needed."))

src_file = st.file_uploader("NetSuite source export (.xlsx)", type=["xlsx"])
lc1, lc2, lc3 = st.columns(3)
vend_file = lc1.file_uploader("Vendors lookup (.csv)", type=["csv"])
opp_file = lc2.file_uploader("Opportunities lookup (.csv)", type=["csv"])
cust_file = lc3.file_uploader("Customers lookup (.csv)", type=["csv"])

drop_negatives = st.checkbox(
    "Move vendors with zero/negative totals to the exceptions file "
    "(NetSuite cannot post these bills)", value=True)
use_bundled = st.checkbox("Use bundled June lookups (testing)", value=True,
                          disabled=bool(vend_file and opp_file and cust_file))


def get_lookups():
    if pull_ns:
        from engine.netsuite import NetSuiteClient, pull_lookup_frames
        import io as _io
        client = NetSuiteClient(st.secrets["NS_ACCOUNT_ID"],
                                st.secrets["NS_CLIENT_ID"],
                                st.secrets["NS_CERT_ID"],
                                st.secrets["NS_PRIVATE_KEY"])
        v, o, c = pull_lookup_frames(client)
        def _buf(df):
            b = _io.StringIO(); df.to_csv(b, index=False); b.seek(0); return b
        return load_lookups(_buf(v), _buf(o), _buf(c))
    if vend_file and opp_file and cust_file:
        return load_lookups(vend_file, opp_file, cust_file)
    if use_bundled:
        return load_lookups("data/lookups/vendors.csv",
                            "data/lookups/opportunities.csv",
                            "data/lookups/customers.csv")
    return None


# ------------------------------------------------- hierarchy review dialog
@st.dialog("Brokers not in their group's deal hierarchy", width="large")
def hierarchy_dialog(pending):
    from engine.matching import OverrideStore
    st.caption("The named broker isn't on this group's deal. Choose who "
               "should actually be paid — your choice is remembered for "
               "future runs.")
    store = OverrideStore("data/overrides.json")
    choices = {}
    for p in pending:
        st.divider()
        st.markdown(f"Row says **{p['broker']}** — {p['group_name']} "
                    f"({p['group_id']}), {p['rows']} row(s), "
                    f"${p['total']:,.2f}")
        opts, meta = [], []
        role_labels = {"primary": "Primary Broker", "ga": "General Agent",
                       "mga": "Managing General Agent"}
        for rkey, label in role_labels.items():
            nm = p["hierarchy"].get(rkey)
            if nm:
                opts.append(f"Pay the deal's {label} — {nm}")
                meta.append({"pay_to": rkey})
        opts.append(f"Pay {p['broker']} themselves")
        meta.append({"pay_to": "self"})
        opts.append("Send to exceptions file")
        meta.append(None)
        idx = st.radio("Pay to:", range(len(opts)),
                       format_func=lambda i, o=opts: o[i],
                       key=f"h_{p['broker']}_{p['group_id']}",
                       label_visibility="collapsed")
        decision = meta[idx]
        if decision and decision["pay_to"] == "self":
            code = st.selectbox("Bill them as:",
                                ["Primary Broker (1)", "General Agent (2)",
                                 "Managing General Agent (3)"],
                                key=f"hc_{p['broker']}_{p['group_id']}")
            decision = {"pay_to": "self", "code": int(code[-2])}
        choices[(p['broker'], p['group_id'])] = decision
    st.divider()
    if st.button("Save decisions and continue run", type="primary"):
        for (broker, gid), decision in choices.items():
            if decision is None:
                ss.h_rejects.add((broker, gid))
            else:
                store.add(broker, gid, decision)
        ss["resume"] = True
        st.rerun()


# ----------------------------------------------------------- review dialog
@st.dialog("Broker names need review", width="large")
def review_dialog(pending, matcher):
    st.caption("Accepted matches are remembered for future runs. "
               "Rejected brokers' rows go to the exceptions file.")
    store = AliasStore(ALIAS_PATH)
    all_vendors = sorted(matcher._canon_by_norm.values())
    for m in pending:
        st.divider()
        st.markdown(f"**Source file says:** {m.source_name}")
        candidates = matcher.top_candidates(m.source_name, k=5)
        options = [f"{name}  ({score:.0f}% similar)" for name, score in candidates]
        options.append("Search the full vendor list…")
        options.append("Reject — send rows to exceptions file")
        default = 0 if m.suggestion else len(options) - 1
        choice = st.radio("Match to:", options, index=default,
                          key=f"pick_{m.source_name}", label_visibility="collapsed")
        if choice.startswith("Reject"):
            ss.decisions[m.source_name] = "__REJECT__"
        elif choice.startswith("Search the full"):
            picked = st.selectbox(
                f"Type to search all {len(all_vendors)} vendors:",
                all_vendors, index=None,
                placeholder="Start typing a broker name…",
                key=f"full_{m.source_name}")
            if picked:
                ss.decisions[m.source_name] = picked
            else:
                ss.decisions.pop(m.source_name, None)  # nothing chosen yet
        else:
            ss.decisions[m.source_name] = candidates[options.index(choice)][0]
    st.divider()
    undecided = [m.source_name for m in pending
                 if m.source_name not in ss.decisions]
    if undecided:
        st.warning("Pick a vendor from the search box (or another option) "
                   f"for: {', '.join(undecided)}")
    if st.button("Save decisions and continue run", type="primary",
                 disabled=bool(undecided)):
        for src_name, canon in ss.decisions.items():
            if canon == "__REJECT__":
                ss.n_rejects.add(src_name)
            else:
                store.add(src_name, canon)
        ss["resume"] = True
        st.rerun()


# --------------------------------------------------------------------- run
def execute_run():
    from engine.lookups import LookupSchemaError
    try:
        lk = get_lookups()
    except LookupSchemaError as e:
        st.error(f"Lookup file problem — {e}")
        return
    except Exception as e:
        st.error(f"Couldn't read a lookup file: {e}")
        return
    if lk is None:
        st.error("Provide the three lookup files (or tick the bundled option).")
        return
    xls = pd.ExcelFile(src_file)
    source = xls.parse("Source File")
    onhold = set()
    if "OnHold" in xls.sheet_names:
        onhold = set(xls.parse("OnHold")["Group Name"].dropna().astype(str))

    from engine.matching import OverrideStore
    overrides = OverrideStore("data/overrides.json")
    matcher = NameMatcher(lk.canonical_names, AliasStore(ALIAS_PATH))
    with st.spinner("Processing…"):
        res = run_transform(source, onhold, lk, matcher, period,
                            override_store=overrides,
                            hierarchy_rejects=ss.h_rejects,
                            name_rejects=ss.n_rejects,
                            exclude_nonpositive_vendors=drop_negatives)

    # rows for rejected brokers: force into exceptions by leaving them
    # unmatched (matcher won't resolve them; transform routes them out)
    if res.pending_review:
        unresolved = [m for m in res.pending_review
                      if ss.decisions.get(m.source_name) != "__REJECT__"
                      and m.source_name not in ss.n_rejects]
        if unresolved:
            review_dialog(unresolved, matcher)
            return
        with st.spinner("Processing…"):
            res = run_transform(source, onhold, lk, matcher, period,
                                override_store=overrides,
                                hierarchy_rejects=ss.h_rejects,
                                name_rejects=ss.n_rejects)
    if res.pending_hierarchy:
        hierarchy_dialog(res.pending_hierarchy)
        return
    ss["result"] = res

    with open(HISTORY_PATH, "a") as f:
        f.write(json.dumps({"ts": dt.datetime.now().isoformat(),
                            "period": period, **res.stats}, default=str) + "\n")


if st.button("▶ Run", type="primary", disabled=src_file is None):
    ss["result"] = None
    execute_run()

if ss.pop("resume", False) and src_file is not None:
    execute_run()

# ------------------------------------------------------------------ output
res = ss.get("result")
if res is not None and not res.pending_review:
    s = res.stats
    exc_reasons = (res.exceptions["Exception Reason"].value_counts().to_dict()
                   if len(res.exceptions) else {})
    n_hier_miss = exc_reasons.get("broker not found in deal hierarchy", 0)
    if s["output_rows"] and n_hier_miss > s["output_rows"] * 0.10:
        st.warning(f"{n_hier_miss} rows couldn't find their group's deal "
                   "hierarchy — the opportunities file may not be joining "
                   "on the right Group ID. Check that its Group # values "
                   "correspond to the source file's G-RDA IDs (directly or "
                   "via the customers file).")
    a, b, c, d = st.columns(4)
    a.metric("Source rows", s["source_rows"])
    b.metric("Import rows", s["output_rows"])
    c.metric("Exceptions", s["exception_rows"])
    d.metric("Commission types", len(s["type_counts"]))

    # ------------------------------------------------ money reconciliation
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Source total $", f"{s['source_total']:,.2f}")
    r2.metric("Import file $", f"{s['output_total']:,.2f}")
    r3.metric("Exceptions $", f"{s['exceptions_total']:,.2f}")
    r4.metric("Gap", f"{s['reconciliation_gap']:,.2f}")
    if abs(s["reconciliation_gap"]) < 0.01:
        st.success("Reconciled — source total = import file + exceptions, "
                   "to the cent. Every source dollar is accounted for.")
    else:
        st.error(f"RECONCILIATION GAP of {s['reconciliation_gap']:,.2f} — "
                 "some source amounts are in neither output file. "
                 "Do not import until this is resolved.")
    st.dataframe(res.output.head(50), use_container_width=True)

    buf = io.StringIO(); res.output.to_csv(buf, index=False)
    st.download_button(f"⬇ Import file — BROKER_COMMISSION_{period}.csv",
                       buf.getvalue(), f"BROKER_COMMISSION_{period}.csv", "text/csv")
    if len(res.exceptions):
        buf2 = io.StringIO(); res.exceptions.to_csv(buf2, index=False)
        st.download_button(f"⬇ Exceptions file ({len(res.exceptions)} rows)",
                           buf2.getvalue(), f"EXCEPTIONS_{period}.csv", "text/csv")

    # -------------------------------------------------- backtest validator
    st.divider()
    st.subheader("Backtest against a previous month")
    st.caption("Upload the final file you produced manually for this same "
               "period. The engine output is compared row by row.")
    hist = st.file_uploader("Historical final file (.csv)", type=["csv"],
                            key="hist_final")
    if hist is not None:
        from engine.validate import validate
        report = validate(res.output, pd.read_csv(hist))
        if report.passed:
            st.success("PASS — classifications and type counts match the "
                       "historical final file.")
        else:
            st.warning("Differences found — review below. Manual-override "
                       "rows are expected to appear as differences.")
        st.text(report.summary)
        if len(report.class_diffs):
            st.markdown("**Rows classified differently:**")
            st.dataframe(report.class_diffs, use_container_width=True)
        cA, cB = st.columns(2)
        if len(report.only_engine):
            cA.markdown("**Only in engine output:**")
            cA.dataframe(report.only_engine, use_container_width=True)
        if len(report.only_final):
            cB.markdown("**Only in historical final:**")
            cB.dataframe(report.only_final, use_container_width=True)
