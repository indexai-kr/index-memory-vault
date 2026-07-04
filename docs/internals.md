# Internals: transition diagnostics

`imv doctor` is backed by a small internal interface in `imv/probe.py`:

```python
class TransitionProbe(ABC):
    name: str
    def check(self, store: VaultStore) -> list[Finding]: ...
```

Two probes ship by default:

- **gap** (error): a memory's current state must match the last audited
  transition. A mismatch means the state changed outside the audit trail.
- **cycle** (warn): a memory that returns to a state it previously left
  has an unstable review history.

You do not need to know any of this to work on `save_memory` or
`search_memory`. If you change `approve`/`reject` or anything touching
`audit_log`, run `imv doctor` before opening a PR.

Transition histories admit structural invariants beyond reachability;
this module ships the two simplest probes, and the interface is the
extension point for richer ones.
