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

    def test_webhook_payload_rejects_out_of_range_priority(self):
        with self.assertRaises(ValueError):
            self.module.build_alarm_from_webhook_payload({'title': 'Test', 'priority': '6'})

    def test_webhook_payload_accepts_alarm_level_alias(self):
        alarm = self.module.build_alarm_from_webhook_payload({'title': 'Test', 'alarm_level': '2'})
        self.assertEqual(alarm.get('priority'), '2')

    def test_get_alarms_list_nested_items_and_sorting(self):
        data = {
            'Data': {
                'Alarm': {
                    'items': {
                        '42': {'title': 'B-Alarm', 'date': '2024-01-01T12:00:00Z'},
                        '43': {'title': 'A-Alarm', 'date': '2024-01-01T12:01:00Z'},
                    },
                    'sorting': ['43', '42'],
                }
            }
        }

        alarms = self.module.get_alarms_list(data)
        self.assertEqual([a.get('id') for a in alarms], ['43', '42'])

    def test_replay_guard_rejects_outdated_timestamp(self):
        ts = 1_700_000_000
        payload = {'title': 'A', 'text': 'B', 'address': 'C', 'priority': '3', 'ts': ts}
        sig = self.module._build_webhook_signature(payload, ts)
        payload['sig'] = sig

        old_time = self.module.time.time
        try:
            self.module.time.time = lambda: ts + self.module.WEBHOOK_MAX_SKEW_SECONDS + 5
            with self.assertRaises(ValueError):
                self.module._verify_replay_guard(payload, {})
        finally:
            self.module.time.time = old_time

    def test_replay_guard_rejects_invalid_signature(self):
        ts = 1_700_000_000
        payload = {'title': 'A', 'text': 'B', 'address': 'C', 'priority': '3', 'ts': ts, 'sig': 'deadbeef'}

        old_time = self.module.time.time
        try:
            self.module.time.time = lambda: ts
            with self.assertRaises(ValueError):
                self.module._verify_replay_guard(payload, {})
        finally:
            self.module.time.time = old_time

    def test_ntfy_retry_sleep_includes_jitter(self):
        os.environ['NTFY_RETRY_ATTEMPTS'] = '2'
        os.environ['NTFY_RETRY_DELAY_SECONDS'] = '1.5'
        os.environ['NTFY_RETRY_JITTER_SECONDS'] = '0.5'
        import alarm_gateway
        module = importlib.reload(alarm_gateway)

        class Resp:
            def raise_for_status(self):
                raise RuntimeError('fail')

        sleep_calls = []
        old_post = module.requests.post
        old_sleep = module.time.sleep
        old_uniform = module.random.uniform
        try:
            module.requests.post = lambda *args, **kwargs: Resp()
            module.time.sleep = lambda seconds: sleep_calls.append(seconds)
            module.random.uniform = lambda a, b: 0.2

            with self.assertRaises(RuntimeError):
                module.ntfy_publish('Titel', 'Text', None)
        finally:
            module.requests.post = old_post
            module.time.sleep = old_sleep
            module.random.uniform = old_uniform

        self.assertEqual(len(sleep_calls), 1)
        self.assertAlmostEqual(sleep_calls[0], 1.7, places=5)


if __name__ == '__main__':
    unittest.main()
