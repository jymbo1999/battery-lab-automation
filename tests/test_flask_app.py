import unittest

from battery_lab.flask_app import create_app


class BatteryFlaskAppTests(unittest.TestCase):
    def test_packaged_flask_app_registers_battery_routes(self):
        app = create_app({"TESTING": True})

        client = app.test_client()
        root = client.get("/")
        self.assertEqual(root.status_code, 302)
        self.assertEqual(root.headers["Location"], "/battery/")

        health = client.get("/battery/health")
        self.assertEqual(health.status_code, 200)
        payload = health.get_json()
        self.assertIn("data_root", payload)
        self.assertIn("output_root", payload)


if __name__ == "__main__":
    unittest.main()
