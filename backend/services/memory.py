"""Memory across decks.

* **Similar ventures**: each finished profile is embedded; a new run is
  compared with past ones ("this looks like 3 decks you analysed before").
* **Deck versions**: when a company sends an updated deck, the run is matched
  to that company's previous run and the report shows what changed: score,
  verdict, criterion ratings, headline claims and market size.
"""

from __future__ import annotations

import json
import re
import time

import numpy as np

from core.embeddings import get_embedder
from services.db import get_db

SIMILARITY_MIN = 0.55


def company_key(name: str) -> str:
    name = re.sub(r"\b(inc|ltd|llc|gmbh|labs?|ai|technologies|tech|co)\b", "", (name or "").lower())
    return re.sub(r"[^a-z0-9]", "", name)


def _profile_text(p: dict) -> str:
    return " ".join(str(p.get(k, "")) for k in ("problem", "solution", "customer", "business_model"))


def snapshot(state: dict) -> dict:
    """The parts of a result worth comparing between deck versions."""
    sc = state.get("scoring") or {}
    claims = (state.get("claims") or {}).get("claims", [])
    ms = state.get("market_size") or {}
    keyed: dict[str, str] = {}
    for c in claims:  # keyed by kind and slide: the model words the same claim differently each run
        base = f"{c['kind']}@slide{c['slide']}"
        key, n = base, 2
        while key in keyed:
            key, n = f"{base}#{n}", n + 1
        keyed[key] = c["value"]
    return {
        "score": state.get("score"),
        "recommendation": state.get("recommendation"),
        "deck_hash": state.get("deck_hash"),
        "criteria": {k: v.get("score") for k, v in sc.items() if isinstance(v, dict) and "score" in v},
        "claims": keyed,
        "market_size": {k: ms.get(k) for k in ("tam_usd", "sam_usd", "som_usd")} if "error" not in ms else {},
        "stage": (state.get("venture_profile") or {}).get("stage"),
    }


def _vec(text: str) -> np.ndarray:
    return np.asarray(get_embedder().embed([text])[0], dtype=np.float32)


async def remember(run_id: str, state: dict) -> None:
    p = state.get("venture_profile") or {}
    if "error" in p or not p:
        return
    v = _vec(_profile_text(p))
    await get_db().execute(
        "insert or replace into memory (run_id, company, company_key, created, embedding, profile, snapshot) "
        "values (?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            p.get("name"),
            company_key(p.get("name", "")),
            time.time(),
            v.tobytes(),
            json.dumps(p),
            json.dumps(snapshot(state), default=str),
        ),
    )


async def similar(run_id: str, profile: dict, k: int = 3) -> list[dict]:
    if not profile or "error" in profile:
        return []
    rows = await get_db().fetchall(
        "select run_id, company, company_key, created, embedding, snapshot from memory where run_id != ?", (run_id,)
    )
    if not rows:
        return []
    q = _vec(_profile_text(profile))
    own = company_key(profile.get("name", ""))
    out = []
    for r in rows:
        if own and r["company_key"] == own:
            continue  # earlier versions of the same deck are reported as a diff instead
        sim = float(np.frombuffer(r["embedding"], dtype=np.float32) @ q)
        if sim >= SIMILARITY_MIN:
            snap = json.loads(r["snapshot"])
            out.append(
                {
                    "run_id": r["run_id"],
                    "company": r["company"],
                    "similarity": round(sim, 3),
                    "score": snap.get("score"),
                    "recommendation": snap.get("recommendation"),
                    "created": r["created"],
                }
            )
    return sorted(out, key=lambda x: -x["similarity"])[:k]


async def previous_version(run_id: str, profile: dict) -> dict | None:
    key = company_key((profile or {}).get("name", ""))
    if not key:
        return None
    return await get_db().fetchone(
        "select run_id, created, snapshot from memory where company_key = ? and run_id != ? "
        "order by created desc limit 1",
        (key, run_id),
    )


def diff(old: dict, new: dict) -> dict:
    changes: dict = {}
    if new.get("deck_hash") and old.get("deck_hash") == new.get("deck_hash"):
        # identical deck text: whatever differs below is model variance, not a change in the company
        changes["same_deck"] = True
    if old.get("score") != new.get("score"):
        changes["score"] = {"from": old.get("score"), "to": new.get("score")}
    if old.get("recommendation") != new.get("recommendation"):
        changes["recommendation"] = {"from": old.get("recommendation"), "to": new.get("recommendation")}
    if old.get("stage") != new.get("stage"):
        changes["stage"] = {"from": old.get("stage"), "to": new.get("stage")}
    crit = {
        k: {"from": old["criteria"].get(k), "to": v}
        for k, v in new.get("criteria", {}).items()
        if old.get("criteria", {}).get(k) != v
    }
    if crit:
        changes["criteria"] = crit
    oc, nc = old.get("claims", {}), new.get("claims", {})
    claim_changes = {
        "added": {k: v for k, v in nc.items() if k not in oc},
        "removed": {k: v for k, v in oc.items() if k not in nc},
        "changed": {k: {"from": oc[k], "to": v} for k, v in nc.items() if k in oc and oc[k] != v},
    }
    if any(claim_changes.values()):
        changes["claims"] = claim_changes
    if old.get("market_size") != new.get("market_size") and new.get("market_size"):
        changes["market_size"] = {"from": old.get("market_size"), "to": new.get("market_size")}
    return changes


async def compare_with_history(run_id: str, state: dict) -> dict:
    profile = state.get("venture_profile") or {}
    out: dict = {"similar": await similar(run_id, profile)}
    prev = await previous_version(run_id, profile)
    if prev:
        out["previous_version"] = {
            "run_id": prev["run_id"],
            "created": prev["created"],
            "changes": diff(json.loads(prev["snapshot"]), snapshot(state)),
        }
    return out
