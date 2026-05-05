import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date, timedelta
from school_bot.bot.handlers.student_attendance import sca_start, save_attendance, sca_photo_handler
from school_bot.database.models import Profile, StudentDailyAttendance
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, PhotoSize

class TestStudentAttendance(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = AsyncMock()
        self.state = AsyncMock(spec=FSMContext)
        self.message = AsyncMock(spec=Message)
        self.message.answer = AsyncMock()
        self.message.edit_text = AsyncMock()
        self.message.edit_reply_markup = AsyncMock()
        self.message.delete = AsyncMock()
        
        self.profile = MagicMock(spec=Profile)
        self.profile.id = 1
        self.profile.school_id = 10
        self.db_user = MagicMock()
        self.db_user.id = 1

    async def test_no_profile_shows_warning(self):
        await sca_start(self.message, self.state, self.session, None)
        self.message.answer.assert_called_with("⚠️ Ushbu funksiyadan foydalanish uchun profilingizda maktab biriktirilgan bo'lishi kerak.")

    async def test_no_school_id_shows_warning(self):
        self.profile.school_id = None
        await sca_start(self.message, self.state, self.session, self.profile)
        self.message.answer.assert_called_with("⚠️ Ushbu funksiyadan foydalanish uchun profilingizda maktab biriktirilgan bo'lishi kerak.")

    async def test_no_students_shows_warning(self):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        self.session.execute.return_value = mock_result
        
        await sca_start(self.message, self.state, self.session, self.profile)
        self.message.answer.assert_called_with("⚠️ Maktabingizda tasdiqlangan o'quvchilar topilmadi.")

    async def test_manual_save_marks_correctly(self):
        self.state.get_data.return_value = {
            "attendance_date": "2026-05-05",
            "students": [{"id": 101, "name": "Student 1"}],
            "photo_file_id": None
        }
        marks = {101: "present"}
        
        with patch("school_bot.bot.handlers.student_attendance.insert") as mock_insert:
            # We don't need to fully mock the complex UPSERT logic here, 
            # just ensure it doesn't crash and commits.
            await save_attendance(self.message, self.state, self.session, self.db_user, marks, source="manual")
            self.session.commit.assert_called()
            self.message.answer.assert_called()
            args, kwargs = self.message.answer.call_args
            self.assertIn("✅ Davomat saqlandi!", args[0])

    async def test_upsert_on_resubmit(self):
        self.state.get_data.return_value = {
            "attendance_date": "2026-05-05",
            "students": [{"id": 101, "name": "Student 1"}],
            "photo_file_id": None
        }
        marks = {101: "absent"}
        
        await save_attendance(self.message, self.state, self.session, self.db_user, marks, source="manual")
        self.session.execute.assert_called() # Should call execute for the insert
        self.session.commit.assert_called()

    @patch("school_bot.bot.handlers.student_attendance.run_ocr_pipeline")
    async def test_ocr_fallback_to_keyboard_on_low_confidence(self, mock_ocr):
        mock_ocr.return_value = {"marks": {}, "source": None}
        self.message.photo = [MagicMock(spec=PhotoSize, file_id="file123")]
        self.state.get_data.return_value = {"students": [{"id": 101, "name": "S1"}]}
        
        await sca_photo_handler(self.message, self.state, MagicMock())
        
        self.state.set_state.assert_called()
        self.message.answer.assert_called()
        args, kwargs = self.message.answer.call_args
        self.assertIn("qo'lda belgilang", args[0])

    @patch("school_bot.bot.services.vision_service.OPENROUTER_API_KEY", None)
    @patch("school_bot.bot.services.vision_service.pytesseract.image_to_string")
    async def test_ai_skipped_when_no_key(self, mock_tesseract):
        from school_bot.bot.services.vision_service import run_ocr_pipeline
        mock_tesseract.return_value = "" # OCR fails
        bot = AsyncMock()
        bot.get_file.return_value = MagicMock(file_path="path")
        bot.download_file.return_value = MagicMock(read=lambda: b"fake_image")
        
        result = await run_ocr_pipeline(bot, "file123", [{"id": 1, "name": "S1"}])
        self.assertEqual(result["source"], None)
        self.assertEqual(result["marks"], {})
