import unittest

from profile import save_profile


class ProfileTests(unittest.TestCase):
    def test_save_profile_updates_allowed_fields(self):
        result = save_profile(
            {"name": "Ada", "email": "ada@example.com", "role": "admin"},
            {"name": "Grace", "email": "grace@example.com", "role": "owner"},
        )

        self.assertEqual("Grace", result["name"])
        self.assertEqual("grace@example.com", result["email"])
        self.assertEqual("admin", result["role"])


if __name__ == "__main__":
    unittest.main()
