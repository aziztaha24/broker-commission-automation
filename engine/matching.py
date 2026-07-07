"""Broker name matching: exact -> alias store -> fuzzy suggestion.

The alias store is a persistent mapping {source_name -> canonical_name}.
Accepted matches from the review dialog land here, so they resolve
silently on every future run.
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process


def _norm(name: str) -> str:
    return " ".join(str(name).strip().lower().split())


@dataclass
class MatchResult:
    source_name: str
    matched_name: str | None      # canonical name if resolved
    score: float                  # 0-100
    method: str                   # 'exact' | 'alias' | 'fuzzy' | 'none'
    suggestion: str | None = None # best fuzzy candidate when unresolved

    @property
    def resolved(self) -> bool:
        return self.matched_name is not None


class AliasStore:
    """JSON-file alias store. Swap for Supabase/Postgres in production —
    only save()/load() change; the interface stays the same."""

    def __init__(self, path: str):
        self.path = path
        self._aliases: dict[str, str] = {}
        self.load()

    def load(self):
        if os.path.exists(self.path):
            with open(self.path) as f:
                self._aliases = json.load(f)

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self._aliases, f, indent=2, sort_keys=True)

    def get(self, source_name: str) -> str | None:
        return self._aliases.get(_norm(source_name))

    def add(self, source_name: str, canonical_name: str):
        self._aliases[_norm(source_name)] = canonical_name
        self.save()


class NameMatcher:
    """Matches source broker names against the canonical list
    (the vendor saved-search names)."""

    def __init__(self, canonical_names: list[str], alias_store: AliasStore,
                 suggest_min_score: float = 60):
        self.alias_store = alias_store
        self.suggest_min_score = suggest_min_score
        self._canon_by_norm = {_norm(n): n for n in canonical_names}
        self._norm_list = list(self._canon_by_norm.keys())

    def match(self, source_name: str) -> MatchResult:
        n = _norm(source_name)
        if n in self._canon_by_norm:
            return MatchResult(source_name, self._canon_by_norm[n], 100, "exact")

        alias = self.alias_store.get(source_name)
        if alias is not None:
            return MatchResult(source_name, alias, 100, "alias")

        if self._norm_list:
            best = process.extractOne(n, self._norm_list, scorer=fuzz.token_sort_ratio)
            if best and best[1] >= self.suggest_min_score:
                return MatchResult(source_name, None, best[1], "none",
                                   suggestion=self._canon_by_norm[best[0]])
        return MatchResult(source_name, None, 0, "none")

    def top_candidates(self, source_name: str, k: int = 5) -> list[tuple[str, float]]:
        """For the manual-pick dropdown in the review dialog."""
        hits = process.extract(_norm(source_name), self._norm_list,
                               scorer=fuzz.token_sort_ratio, limit=k)
        return [(self._canon_by_norm[h[0]], h[1]) for h in hits]
