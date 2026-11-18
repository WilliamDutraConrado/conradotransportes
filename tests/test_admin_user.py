import unittest
from unittest.mock import MagicMock

from conrado.admin_user import create_admin_account
from conrado.store import BusinessError


class AdminUserTests(unittest.TestCase):
    def test_matching_password_is_delegated_without_logging(self):
        store = MagicMock()
        store.create_admin.return_value = {"admin_id": 1, "username": "gestor"}

        result = create_admin_account(store, "gestor", "SenhaAdministrativaSegura!", "SenhaAdministrativaSegura!")

        self.assertEqual(result["username"], "gestor")
        store.create_admin.assert_called_once_with("gestor", "SenhaAdministrativaSegura!", "admin")

    def test_mismatched_password_is_rejected_before_store(self):
        store = MagicMock()
        with self.assertRaisesRegex(BusinessError, "não coincidem"):
            create_admin_account(store, "gestor", "SenhaAdministrativaSegura!", "outra")
        store.create_admin.assert_not_called()


if __name__ == "__main__":
    unittest.main()
