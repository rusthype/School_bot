BEGIN;

-- Backfill bot_groups that were missed due to UUID mismatch bug.
-- The bot joined these groups but failed to persist them.
--
-- IDs confirmed from production logs:
-- -1003643690746: A'lochi 4-"B" guruh | Uchko'prik 56-maktab
-- -1003753802579: A'lochi 1-"B" guruh | Uchko'prik 56-maktab
-- -1003767160085: S7 & Test

INSERT INTO bot_groups (chat_id, name, status, alochi_group_id, created_at, updated_at)
VALUES
  (-1003643690746, $$A'lochi 4-"B" guruh | Uchko'prik 56-maktab$$, 'pending', NULL, NOW(), NOW()),
  (-1003753802579, $$A'lochi 1-"B" guruh | Uchko'prik 56-maktab$$, 'pending', NULL, NOW(), NOW()),
  (-1003767160085, $$S7 & Test$$, 'pending', NULL, NOW(), NOW())
  -- TODO: Add the remaining 7 group chat_ids for 56-maktab below
  -- (chat_id, $$Group Name$$, 'pending', NULL, NOW(), NOW()),
ON CONFLICT (chat_id) DO NOTHING;

-- Verify the inserted rows
SELECT id, chat_id, name, status FROM bot_groups WHERE chat_id IN (
  -1003643690746, -1003753802579, -1003767160085
);

COMMIT;
