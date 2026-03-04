import importlib
import os
import unittest


class PriorityKeywordCaseInsensitiveTests(unittest.TestCase):
    def setUp(self):
        os.environ['NTFY_PRIORITY_KEYWORDS'] = 'MANV=4,Probealarm=1'
        os.environ['NTFY_DEFAULT_PRIORITY'] = '5'

        import alarm_gateway
        self.module = importlib.reload(alarm_gateway)

    def test_case_insensitive_for_all_keywords(self):
        for title in ['manv lage', 'MANV lage', 'ManV lage', 'mAnV lage']:
            self.assertEqual(self.module.resolve_ntfy_priority(title), '4')

    def test_case_insensitive_probealarm(self):
        for title in ['probealarm test', 'PROBEALARM test', 'ProbeAlarm test']:
            self.assertEqual(self.module.resolve_ntfy_priority(title), '1')


if __name__ == '__main__':
    unittest.main()
