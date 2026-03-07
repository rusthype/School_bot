# Database Migration Scripts

## migrate_superuser_to_superadmin.py

This script removes the deprecated `superuser` value from the `user_role` enum
and replaces it with `superadmin`.

### When to run:
After updating the code to use `superadmin` instead of `superuser`.

### How to run:
```bash
python3 scripts/migrate_superuser_to_superadmin.py
```

### What it does:
1. Creates a new enum with values: `superadmin`, `teacher`, `librarian`
2. Updates all users with `role='superuser'` to `role='superadmin'`
3. Changes the column type to the new enum
4. Drops the old enum
5. Renames the new enum to `user_role`

### Rollback (if needed):
```sql
-- This is complex, better to restore from backup
```
