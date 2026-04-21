import unittest

from prodesk.http_session import build_hardened_session


class HttpSessionTests(unittest.TestCase):
    def test_hardened_session_sets_user_agent_and_adapters(self):
        session = build_hardened_session(user_agent="bro-test-agent/1.0", total_retries=3)
        try:
            self.assertEqual(session.headers.get("User-Agent"), "bro-test-agent/1.0")
            http_adapter = session.adapters.get("http://")
            https_adapter = session.adapters.get("https://")
            self.assertIsNotNone(http_adapter)
            self.assertIsNotNone(https_adapter)
            self.assertEqual(int(http_adapter.max_retries.total), 3)
            self.assertEqual(int(https_adapter.max_retries.total), 3)
        finally:
            session.close()

    def test_hardened_session_clamps_retry_and_pool_bounds(self):
        session = build_hardened_session(
            user_agent="bro-test-agent/1.0",
            total_retries=-5,
            pool_connections=0,
            pool_maxsize=0,
        )
        try:
            http_adapter = session.adapters["http://"]
            self.assertEqual(int(http_adapter.max_retries.total), 0)
            self.assertGreaterEqual(int(http_adapter._pool_connections), 1)
            self.assertGreaterEqual(int(http_adapter._pool_maxsize), 1)
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
