-- Unified work migration (PostgreSQL). Run after the legacy schema exists.
-- The Python migrate_unified_work() routine performs the idempotent data copy.

ALTER TABLE assignments ALTER COLUMN event_id DROP NOT NULL;
ALTER TABLE assignments ADD COLUMN IF NOT EXISTS task_id INTEGER REFERENCES tasks(id);
ALTER TABLE assignments ADD COLUMN IF NOT EXISTS target_type VARCHAR(8) NOT NULL DEFAULT 'event';
ALTER TABLE assignments DROP CONSTRAINT IF EXISTS uq_event_user;
ALTER TABLE assignments ADD CONSTRAINT uq_event_user UNIQUE (event_id, user_jid);
ALTER TABLE assignments ADD CONSTRAINT uq_task_user UNIQUE (task_id, user_jid);
ALTER TABLE assignments ADD CONSTRAINT ck_assignment_one_target CHECK
  ((event_id IS NOT NULL AND task_id IS NULL) OR
   (event_id IS NULL AND task_id IS NOT NULL));

CREATE TABLE IF NOT EXISTS progress_revisions (
  id SERIAL PRIMARY KEY,
  assignment_id INTEGER NOT NULL REFERENCES assignments(id),
  field VARCHAR(64) NOT NULL,
  value TEXT NOT NULL,
  author_jid VARCHAR(128) NOT NULL,
  timestamp TIMESTAMPTZ NOT NULL,
  superseded_revision_id INTEGER REFERENCES progress_revisions(id)
);
CREATE INDEX IF NOT EXISTS ix_progress_revisions_assignment_id
  ON progress_revisions(assignment_id);

-- Run the data migration before this final cleanup statement.
-- ALTER TABLE tasks DROP COLUMN assignee_jid;
