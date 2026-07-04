"""index-memory-vault: transition integrity probes.

Transition histories admit structural invariants beyond reachability.
This module ships the two simplest probes; richer invariants are left
to implementations of TransitionProbe.

- GapProbe:   a memory's current state must equal the last audited
              transition. A mismatch (or a memory with no audit trail)
              is a hole in the record: the state moved without an edge.
- CycleProbe: a memory that returns to a state it previously left has
              an unstable review history (e.g. verified -> blocked ->
              verified). Cycles are not necessarily wrong, but they are
              never invisible.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .store import VaultStore


@dataclass
class Finding:
    probe: str
    memory_id: str
    detail: str
    severity: str = "warn"  # "warn" | "error"


class TransitionProbe(ABC):
    """A structural check over the audit graph of state transitions."""

    name: str = "probe"

    @abstractmethod
    def check(self, store: VaultStore) -> list[Finding]: ...


def _histories(store: VaultStore) -> dict[str, list[tuple[str | None, str | None]]]:
    rows = store.db.execute(
        "SELECT memory_id, from_state, to_state FROM audit_log ORDER BY seq"
    ).fetchall()
    hist: dict[str, list[tuple[str | None, str | None]]] = {}
    for r in rows:
        hist.setdefault(r["memory_id"], []).append((r["from_state"], r["to_state"]))
    return hist


class GapProbe(TransitionProbe):
    name = "gap"

    def check(self, store: VaultStore) -> list[Finding]:
        hist = _histories(store)
        out: list[Finding] = []
        for mem in store.list(limit=None):
            edges = hist.get(mem.id)
            if not edges:
                out.append(Finding(
                    self.name, mem.id,
                    "this memory exists but has no audit trail at all — "
                    "it was written without going through the store",
                    severity="error"))
                continue
            last_to = edges[-1][1]
            if last_to != mem.q_state:
                out.append(Finding(
                    self.name, mem.id,
                    f"this memory is '{mem.q_state}', but no recorded "
                    f"review made it so (last recorded state: '{last_to}'). "
                    f"The state changed outside the audit trail.",
                    severity="error"))
        return out


class CycleProbe(TransitionProbe):
    name = "cycle"

    def check(self, store: VaultStore) -> list[Finding]:
        out: list[Finding] = []
        for mid, edges in _histories(store).items():
            visited: list[str] = []
            for _, to_state in edges:
                if to_state is None:
                    continue
                if to_state in visited and visited[-1] != to_state:
                    out.append(Finding(
                        self.name, mid,
                        f"review flip-flop: this memory returned to "
                        f"'{to_state}' after leaving it "
                        f"({' -> '.join(visited + [to_state])}). "
                        f"Not always wrong, but worth a human look.",
                        severity="warn"))
                visited.append(to_state)
        return out


DEFAULT_PROBES: tuple[TransitionProbe, ...] = (GapProbe(), CycleProbe())


def run_probes(store: VaultStore,
               probes: tuple[TransitionProbe, ...] = DEFAULT_PROBES
               ) -> list[Finding]:
    findings: list[Finding] = []
    for p in probes:
        findings.extend(p.check(store))
    return findings
