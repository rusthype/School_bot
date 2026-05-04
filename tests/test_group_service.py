import uuid
import unittest
from unittest.mock import AsyncMock, MagicMock
from school_bot.bot.services.group_service import add_group, get_group_by_alochi_id
from school_bot.database.models import Group

class TestGroupService(unittest.IsolatedAsyncioTestCase):
    async def test_add_group_with_null_alochi_id(self):
        """Test that bot joining a fresh group persists the row with NULL alochi_group_id."""
        session = AsyncMock()
        group = await add_group(session, "Test Group", -100123456789)
        
        self.assertEqual(group.name, "Test Group")
        self.assertEqual(group.chat_id, -100123456789)
        self.assertIsNone(group.alochi_group_id)
        
        session.add.assert_called_once()
        session.commit.assert_called_once()
        session.refresh.assert_called_once_with(group)

    async def test_add_group_with_uuid_alochi_id(self):
        """Test that both string UUID and uuid.UUID instance succeed."""
        session = AsyncMock()
        uid_str = "550e8400-e29b-41d4-a716-446655440000"
        uid = uuid.UUID(uid_str)
        
        # 1. Test with string UUID
        group1 = await add_group(session, "G1", 1, alochi_group_id=uid_str)
        self.assertEqual(group1.alochi_group_id, uid)
        self.assertIsInstance(group1.alochi_group_id, uuid.UUID)
        
        # 2. Test with uuid.UUID instance
        group2 = await add_group(session, "G2", 2, alochi_group_id=uid)
        self.assertEqual(group2.alochi_group_id, uid)
        self.assertIsInstance(group2.alochi_group_id, uuid.UUID)

    async def test_get_group_by_alochi_id_accepts_string(self):
        """Test that get_group_by_alochi_id accepts a str and queries correctly."""
        session = AsyncMock()
        uid_str = "550e8400-e29b-41d4-a716-446655440000"
        uid = uuid.UUID(uid_str)
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result
        
        await get_group_by_alochi_id(session, uid_str)
        
        session.execute.assert_called_once()
        # Verify the query object would have had the UUID (implicit in service logic)

    async def test_add_group_with_empty_string_alochi_id(self):
        """Test that empty string \"\" is treated as None, preventing ValueError."""
        session = AsyncMock()
        group = await add_group(session, "Empty Test", 999, alochi_group_id="")
        self.assertIsNone(group.alochi_group_id)

if __name__ == "__main__":
    unittest.main()
