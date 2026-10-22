import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / 'scripts/benchmark-drama-local.py'
spec = importlib.util.spec_from_file_location('drama_benchmark', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class StreamSupplyTests(unittest.TestCase):
    def test_startup_wait_is_separate_from_sustained_supply(self):
        result = module.stream_metrics([(2.0, 100), (2.08, 100), (2.16, 100)], bytes_per_second=1000)
        self.assertEqual(result['estimated_gap_count'], 0)
        self.assertEqual(result['required_lead_s'], 0)
        self.assertAlmostEqual(result['post_first_chunk_rtf'], 0.8)

    def test_repeated_shortfall_counts_each_gap(self):
        result = module.stream_metrics([(1.0, 100), (1.2, 100), (1.4, 100)], bytes_per_second=1000)
        self.assertEqual(result['estimated_gap_count'], 2)
        self.assertAlmostEqual(result['estimated_gap_s'], 0.2)
        self.assertAlmostEqual(result['required_lead_s'], 0.2)

    def test_queue_absorbs_jitter_and_single_chunk_has_no_decode_rate(self):
        result = module.stream_metrics([(1.0, 300), (1.25, 100)], bytes_per_second=1000)
        self.assertEqual(result['estimated_gap_count'], 0)
        self.assertIsNone(module.stream_metrics([(1.0, 100)])['post_first_chunk_rtf'])

    def test_invalid_arrivals_fail(self):
        for chunks in ([], [(1, 0)], [(1, 2), (0.9, 2)]):
            with self.assertRaises(ValueError):
                module.stream_metrics(chunks)