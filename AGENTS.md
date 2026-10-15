# Agent notes

Keep shared allocation state transactional and require current resource revisions for every hold. Confirm only when all needed resources are held. Release only pending holds during compensation; never undo confirmed allocations through this path. The compensation specialist must inspect final state even when reservation reports success. Keep fixtures synthetic, write authorization explicit, and runtime SQLite state and keys out of Git. Scripted ADK runner tests are not live Gemini or Firedrill evidence.
