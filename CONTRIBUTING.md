# Contributing

- Small core, few dependencies. Keep it that way.
- Touching `save_memory` / `search_memory` / `list_memory`? Normal tests
  are enough — you can ignore `imv/probe.py` entirely.
- Touching `approve` / `reject` / `audit_log`? Run `imv doctor` and keep
  it clean. See `docs/internals.md` (2-minute read).
- Review authority stays human-side (CLI). Do not move approve/reject
  into the MCP tool surface — this is the project's core invariant.
- A CLA is required before your first PR can be merged (AGPL + commercial
  dual licensing).
