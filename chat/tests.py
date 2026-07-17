from django.test import SimpleTestCase

from chat import views


class ChatViewsImportTest(SimpleTestCase):
    def test_chat_views_import_without_gemini_configuration(self):
        self.assertTrue(hasattr(views, 'chat_home'))
