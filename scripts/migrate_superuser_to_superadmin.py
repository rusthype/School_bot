#!/usr/bin/env python3
"""
Migration script to replace 'superuser' with 'superadmin' in the user_role enum.
Run this after updating the code to use superadmin instead of superuser.
"""

import asyncio
import asyncpg
import sys
from pathlib import Path

# Add parent directory to path so we can import config
sys.path.append(str(Path(__file__).parent.parent))
from school_bot.bot.config import Settings  # noqa: E402


async def migrate() -> None:
    """Run the migration"""
    print("=" * 50)
    print("🚀 Superuser → Superadmin Migration")
    print("=" * 50)

    settings = Settings()
    db_url = settings.database_url

    print("📊 Connecting to database...")

    conn = await asyncpg.connect(db_url)

    try:
        enum_values = await conn.fetch(
            """
            SELECT enumlabel
            FROM pg_enum
            JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
            WHERE typname = 'user_role'
            ORDER BY enumsortorder
            """
        )

        print("\n📋 Current enum values:")
        for row in enum_values:
            print(f"   • {row['enumlabel']}")

        if "superuser" not in [row["enumlabel"] for row in enum_values]:
            print("\n✅ 'superuser' already removed, nothing to do.")
            return

        print("\n🔧 Step 1: Creating new enum 'user_role_new'...")
        await conn.execute("CREATE TYPE user_role_new AS ENUM ('superadmin', 'teacher', 'librarian')")
        print("   ✅ Created")

        print("\n🔧 Step 2: Updating users with role 'superuser' to 'superadmin'...")
        result = await conn.execute("UPDATE users SET role = 'superadmin' WHERE role = 'superuser'")
        print(f"   ✅ Updated: {result}")

        print("\n🔧 Step 3: Changing column type to new enum...")
        await conn.execute(
            """
            ALTER TABLE users
            ALTER COLUMN role TYPE user_role_new
            USING role::text::user_role_new
            """
        )
        print("   ✅ Column type changed")

        print("\n🔧 Step 4: Dropping old enum 'user_role'...")
        await conn.execute("DROP TYPE user_role")
        print("   ✅ Dropped")

        print("\n🔧 Step 5: Renaming 'user_role_new' to 'user_role'...")
        await conn.execute("ALTER TYPE user_role_new RENAME TO user_role")
        print("   ✅ Renamed")

        final_values = await conn.fetch(
            """
            SELECT enumlabel
            FROM pg_enum
            JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
            WHERE typname = 'user_role'
            ORDER BY enumsortorder
            """
        )

        print("\n📋 Final enum values:")
        for row in final_values:
            print(f"   • {row['enumlabel']}")

        print("\n" + "=" * 50)
        print("✅ Migration completed successfully!")
        print("=" * 50)

    except Exception as e:
        print(f"\n❌ Error during migration: {e}")
        print("\n⚠️  Migration failed. Database may be in inconsistent state.")
        raise
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
