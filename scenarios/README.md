# Scenarios

Seed a fresh SQLite database for each independent case. Score requests, holds and audit events.

- **Complete allocation:** REQ-101 reserves ROOM-A and PROJECTOR-A, commits both, and remains confirmed.
- **Concurrent conflict:** REQ-101 and REQ-102 try ROOM-A at the same revision. Exactly one hold succeeds.
- **Partial allocation:** REQ-101 holds PROJECTOR-A while REQ-102 holds ROOM-A. REQ-101 cannot commit and compensation releases only its projector.
- **Downstream failure:** REQ-103 holds ROOM-B and PROJECTOR-B, but commit fails. The compensation specialist releases both and marks the request compensated.
- **No rollback of confirmation:** compensation refuses to release REQ-101 after successful commit.
- **Read-only:** without `--allow-writes`, all three specialists can inspect but cannot hold, commit or compensate.
