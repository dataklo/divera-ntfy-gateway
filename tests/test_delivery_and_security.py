import importlib
import os
import unittest


class DeliveryAndSecurityTests(unittest.TestCase):
    def setUp(self):
        os.environ['NTFY_URL'] = 'https://primary.example'
        os.environ['NTFY_FALLBACK_URLS'] = 'https://fallback1.example,https://fallback2.example'
        os.environ['NTFY_TOPIC'] = 'topic'
        os.environ['NTFY_RETRY_ATTEMPTS'] = '1'
        os.environ['WEBHOOK_REPLAY_PROTECTION'] = 'true'
        os.environ['WEBHOOK_HMAC_SECRET'] = 'secret123'
        import alarm_gateway
        self.module = importlib.reload(alarm_gateway)

    def test_ntfy_fallback_targets_used(self):
        calls = []

        class Resp:
            def __init__(self, ok):
                self.ok = ok

            def raise_for_status(self):
                if not self.ok:
                    raise RuntimeError('fail')

        def fake_post(url, **kwargs):
            calls.append(url)
            if url.startswith('https://primary.example'):
                return Resp(False)
            return Resp(True)

        old_post = self.module.requests.post
        try:
            self.module.requests.post = fake_post
            self.module.ntfy_publish('Titel', 'Text', None)
        finally:
            self.module.requests.post = old_post

        self.assertTrue(any(u.startswith('https://primary.example') for u in calls))
        self.assertTrue(any(u.startswith('https://fallback1.example') for u in calls))

    def test_replay_signature(self):
        payload = {'title': 'A', 'text': 'B', 'address': 'C', 'priority': '3'}
        ts = 1_700_000_000
        sig = self.module._build_webhook_signature(payload, ts)
        self.assertTrue(sig)


if __name__ == '__main__':
    unittest.main()
