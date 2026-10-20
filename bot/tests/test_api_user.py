import asyncio
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

import api


class ApiUserTest(unittest.TestCase):
    def test_missing_user_remains_not_found(self):
        connection = Mock()
        connection.cursor.return_value.fetchone.return_value = None

        with patch.object(api.sqlite3, "connect", return_value=connection):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(api.get_user(42))

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.detail, "Пользователь не найден")
        connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
