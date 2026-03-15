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

    def test_dedup_key_uses_alarm_id_when_present(self):
        alarm = {'id': '4711', 'title': 'Probealarm'}
        self.assertEqual(self.module.alarm_dedup_key(alarm), 'id:4711')

    def test_dedup_key_falls_back_to_content_hash(self):
        alarm = {'title': 'Probealarm', 'address': 'Musterstrasse', 'text': 'Test'}
        key = self.module.alarm_dedup_key(alarm)
        self.assertTrue(key.startswith('content:'))

    def test_handle_divera_poll_deduplicates_repeated_alarm_ids(self):
        state = self.module.load_state('/tmp/nonexistent-state.json')
        state['active_fingerprints'] = []
        state['recent_fingerprints'] = []
        state['active_alarm_keys'] = []
        state['recent_alarm_keys'] = {'id:123': int(self.module.time.time())}

        alarm_payload = {'alarms': [{'id': '123', 'title': 'Probealarm', 'text': 'Test'}]}

        sent = []
        old_fetch = self.module.fetch_alarms
        old_publish = self.module.publish_message
        old_save = self.module.save_state
        old_resolve_cluster = self.module.resolve_cluster_status
        try:
            self.module.fetch_alarms = lambda: alarm_payload
            self.module.publish_message = lambda *_args, **_kwargs: sent.append('sent')
            self.module.save_state = lambda *_args, **_kwargs: None
            self.module.resolve_cluster_status = lambda force_refresh=False: {
                'leader_id': self.module.NODE_ID,
                'leader_priority': self.module.NODE_PRIORITY,
                'reachable': [self.module.NODE_ID],
            }
            self.module.handle_divera_poll(state)
        finally:
            self.module.fetch_alarms = old_fetch
            self.module.publish_message = old_publish
            self.module.save_state = old_save
            self.module.resolve_cluster_status = old_resolve_cluster

        self.assertEqual(sent, [])

    def test_save_config_to_env_file_writes_known_variables(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            env_file = f"{tmp}/alarm-gateway.env"
            old_env_file = os.environ.get('ALARM_GATEWAY_ENV_FILE')
            try:
                os.environ['ALARM_GATEWAY_ENV_FILE'] = env_file
                self.module.save_config_to_env_file({'NTFY_TOPIC': 'new-topic', 'WEBHOOK_ENABLED': 'true'})
            finally:
                if old_env_file is None:
                    os.environ.pop('ALARM_GATEWAY_ENV_FILE', None)
                else:
                    os.environ['ALARM_GATEWAY_ENV_FILE'] = old_env_file

            content = open(env_file, 'r', encoding='utf-8').read()
            self.assertIn('NTFY_TOPIC="new-topic"', content)
            self.assertIn('WEBHOOK_ENABLED="true"', content)

    def test_path_matches_accepts_trailing_slash(self):
        self.assertTrue(self.module.path_matches('/admin/config/', '/admin/config'))
        self.assertTrue(self.module.path_matches('/admin/config', '/admin/config/'))

    def test_path_matches_rejects_different_path(self):
        self.assertFalse(self.module.path_matches('/admin/configuration', '/admin/config'))



if __name__ == '__main__':
    unittest.main()
