import importlib
import os
import unittest


class ConfigValidationTests(unittest.TestCase):
    def setUp(self):
        os.environ['NTFY_URL'] = 'https://primary.example'
        os.environ['NTFY_TOPIC'] = 'topic'
        os.environ['NTFY_RETRY_ATTEMPTS'] = '1'
        os.environ['NTFY_RETRY_DELAY_SECONDS'] = '0.1'
        os.environ['NTFY_RETRY_JITTER_SECONDS'] = '0'
        os.environ['HEALTH_PATH'] = '/healthz'
        os.environ['HEALTH_METRICS_PATH'] = '/metrics'
        os.environ['NODE_PRIORITY'] = '50'
        os.environ['WEBHOOK_REPLAY_PROTECTION'] = 'false'
        import alarm_gateway
        self.module = importlib.reload(alarm_gateway)

    def test_poll_seconds_falls_back_to_poll_interval_seconds(self):
        os.environ.pop('POLL_SECONDS', None)
        os.environ['POLL_INTERVAL_SECONDS'] = '13'
        import alarm_gateway
        module = importlib.reload(alarm_gateway)
        self.assertEqual(module.POLL_SECONDS, 13)

    def test_poll_seconds_overrides_poll_interval_seconds(self):
        os.environ['POLL_SECONDS'] = '7'
        os.environ['POLL_INTERVAL_SECONDS'] = '13'
        import alarm_gateway
        module = importlib.reload(alarm_gateway)
        self.assertEqual(module.POLL_SECONDS, 7)

    def test_validate_runtime_config_rejects_negative_retry_delay(self):
        os.environ['NTFY_RETRY_DELAY_SECONDS'] = '-1'
        import alarm_gateway
        module = importlib.reload(alarm_gateway)
        with self.assertRaises(SystemExit):
            module.validate_runtime_config()

    def test_validate_runtime_config_rejects_negative_retry_jitter(self):
        os.environ['NTFY_RETRY_JITTER_SECONDS'] = '-0.1'
        import alarm_gateway
        module = importlib.reload(alarm_gateway)
        with self.assertRaises(SystemExit):
            module.validate_runtime_config()


if __name__ == '__main__':
    unittest.main()
