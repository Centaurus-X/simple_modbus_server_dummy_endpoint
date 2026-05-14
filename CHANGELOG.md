# Changelog

## 2.3.0

- Added consistent runtime timing synchronization.
- Added authoritative server metadata in initial state and configuration messages.
- Added batched value updates with device metadata and history samples.
- Added runtime sensor history snapshots and JSONL event recording.
- Cleaned repository layout for GitHub release.
- Removed generated logs, history snapshots and bytecode caches from the release archive.
- Pinned `pymodbus` to `<3.13` because `3.13+` changed the legacy datastore API.
