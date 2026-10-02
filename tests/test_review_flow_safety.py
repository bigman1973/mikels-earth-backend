import unittest

from src.routes.admin_klaviyo_routes import _review_request_safety_filters


class ReviewFlowSafetyFilterTests(unittest.TestCase):
    def test_filters_block_only_later_cancellation_or_review_submission(self):
        filters = _review_request_safety_filters('cancelled-metric', 'submitted-metric')

        self.assertEqual(len(filters['condition_groups']), 1)
        conditions = filters['condition_groups'][0]['conditions']
        self.assertEqual(len(conditions), 2)
        self.assertEqual(
            [condition['metric_id'] for condition in conditions],
            ['cancelled-metric', 'submitted-metric'],
        )
        for condition in conditions:
            self.assertEqual(condition['type'], 'profile-metric')
            self.assertEqual(condition['measurement'], 'count')
            self.assertEqual(
                condition['measurement_filter'],
                {'type': 'numeric', 'operator': 'equals', 'value': 0},
            )
            self.assertEqual(
                condition['timeframe_filter'],
                {'type': 'date', 'operator': 'flow-start'},
            )
            self.assertIsNone(condition['metric_filters'])

        self.assertIsNot(
            conditions[0]['measurement_filter'],
            conditions[1]['measurement_filter'],
        )


if __name__ == '__main__':
    unittest.main()
