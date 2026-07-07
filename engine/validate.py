"""Backtest validator: compare engine output against a historical final file.

Produces a verdict + detailed diff so past months can be verified in
seconds instead of eyeballing thousands of rows.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import pandas as pd


def _key(df: pd.DataFrame, amt_col: str) -> pd.Series:
    return (df["Vendor"].astype("Int64").astype(str) + "|"
            + df["ID"].astype(str) + "|"
            + df["Member First Name"].fillna("").astype(str) + "|"
            + df["Member Last Name"].fillna("").astype(str) + "|"
            + pd.to_numeric(df[amt_col], errors="coerce").round(2).astype(str))


@dataclass
class ValidationReport:
    engine_rows: int = 0
    final_rows: int = 0
    matched: int = 0
    class_diffs: pd.DataFrame = field(default_factory=pd.DataFrame)
    only_engine: pd.DataFrame = field(default_factory=pd.DataFrame)
    only_final: pd.DataFrame = field(default_factory=pd.DataFrame)
    type_counts_engine: dict = field(default_factory=dict)
    type_counts_final: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return (len(self.class_diffs) == 0
                and self.type_counts_engine == self.type_counts_final)

    @property
    def summary(self) -> str:
        lines = [
            f"engine rows: {self.engine_rows} | final rows: {self.final_rows}",
            f"classification differences: {len(self.class_diffs)}",
            f"rows only in engine output: {len(self.only_engine)}",
            f"rows only in historical final: {len(self.only_final)}",
            f"type counts engine: {self.type_counts_engine}",
            f"type counts final:  {self.type_counts_final}",
        ]
        return "\n".join(lines)


def validate(engine_out: pd.DataFrame, historical_final: pd.DataFrame) -> ValidationReport:
    e, f = engine_out.copy(), historical_final.copy()
    # tolerate the historical header quirk (' Comm Amt ' with spaces)
    f_amt = " Comm Amt " if " Comm Amt " in f.columns else "Comm Amt"
    e["_k"], f["_k"] = _key(e, " Comm Amt "), _key(f, f_amt)

    m = e.merge(f[["_k", "Commission Type"]], on="_k", suffixes=("", "_final"))
    diffs = m[m["Commission Type"] != m["Commission Type_final"]]

    keep = ["Vendor", "ID", "Group Name", "Commission Type", " Comm Amt "]
    rep = ValidationReport(
        engine_rows=len(e), final_rows=len(f), matched=len(m),
        class_diffs=diffs[keep + ["Commission Type_final"]] if len(diffs) else pd.DataFrame(),
        only_engine=e[~e["_k"].isin(f["_k"])][keep],
        only_final=f[~f["_k"].isin(e["_k"])][
            ["Vendor", "ID", "Group Name", "Commission Type", f_amt]],
        type_counts_engine={int(k): int(v) for k, v in
                            e["Commission Type"].value_counts().sort_index().items()},
        type_counts_final={int(k): int(v) for k, v in
                           f["Commission Type"].value_counts().sort_index().items()},
    )
    return rep
