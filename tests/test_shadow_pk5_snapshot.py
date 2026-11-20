from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import gzip
from hashlib import sha256
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from shadow_asof import cutoff_for_day, select_snapshot
from shadow_scoring import WARSAW
from shadow_pk5_snapshot import prepare_pk5_snapshot


FIXTURE = Path(__file__).parent / 'fixtures/pse_pk5_delivery_2026-11-20'


class PK5SnapshotTests(unittest.TestCase):
    def inputs(self, directory=FIXTURE):
        metadata = json.loads((directory / 'capture.json').read_text())
        raw = gzip.decompress((directory / metadata['raw_file']).read_bytes())
        self.assertEqual(sha256(raw).hexdigest(), metadata['raw_sha256'])
        self.assertEqual(len(raw), metadata['bytes'])
        return dict(raw=raw, day=metadata['delivery_date'], source_url=metadata['url'],
                    retrieved_at=datetime.fromisoformat(metadata['retrieved_at_utc']),
                    retrieval_completed_at=datetime.fromisoformat(metadata['retrieval_completed_at_utc']))

    def test_real_november_21_capture_is_complete_and_pre_cutoff(self):
        inputs = self.inputs(FIXTURE.with_name('pse_pk5_delivery_2026-11-21'))
        before = deepcopy(inputs)
        snapshot = prepare_pk5_snapshot(**inputs)
        day = date(2026, 11, 21)
        selected = select_snapshot(day, [snapshot])
        self.assertEqual(selected.rejected, ())
        self.assertEqual(len(selected.rows), 24)
        self.assertEqual(selected.rows[0],
                         (datetime(2026, 11, 20, 23, tzinfo=timezone.utc), Decimal('8655.00000')))
        self.assertEqual(selected.rows[-1],
                         (datetime(2026, 11, 21, 22, tzinfo=timezone.utc), Decimal('6983.00000')))
        self.assertEqual(selected.latest_publication_ts_utc,
                         datetime(2026, 11, 20, 4, 13, 6, 844000, tzinfo=timezone.utc))
        self.assertLess(selected.latest_publication_ts_utc, selected.retrieved_at_utc)
        self.assertLess(selected.retrieval_completed_at_utc, cutoff_for_day(day))
        source = json.loads(inputs['raw'], parse_float=Decimal)['value']
        self.assertEqual([value for _, value in selected.rows],
                         [row['fcst_gen_unit_stor_prov'] for row in source])
        self.assertEqual(inputs, before)

    def test_two_real_delivery_days_remain_separate_in_both_orders(self):
        november_20 = prepare_pk5_snapshot(**self.inputs())
        november_21 = prepare_pk5_snapshot(**self.inputs(
            FIXTURE.with_name('pse_pk5_delivery_2026-11-21')))
        before = deepcopy((november_20, november_21))
        for selected_snapshot, other in ((november_20, november_21), (november_21, november_20)):
            day = selected_snapshot['delivery_date']
            expected = select_snapshot(day, [selected_snapshot])
            for candidates in ([selected_snapshot, other], [other, selected_snapshot]):
                with self.subTest(day=day, order=[item['id'] for item in candidates]):
                    selection = select_snapshot(day, candidates)
                    self.assertEqual(selection.snapshot_id, selected_snapshot['id'])
                    self.assertEqual(selection.rows, expected.rows)
                    self.assertEqual(selection.rejected, ((other['id'], 'WRONG_DELIVERY_DATE'),))
        self.assertEqual((november_20, november_21), before)

    def test_repeated_payload_has_distinct_capture_identity_and_stable_utc_identity(self):
        inputs = self.inputs()
        first = prepare_pk5_snapshot(**inputs)
        # Same official bytes, synthetically shifted capture times before cutoff.
        later_inputs = {
            **inputs, 'retrieved_at': inputs['retrieved_at'] + timedelta(minutes=2),
            'retrieval_completed_at': inputs['retrieval_completed_at'] + timedelta(minutes=2),
        }
        second = prepare_pk5_snapshot(**later_inputs)
        self.assertEqual(first['raw_sha256'], second['raw_sha256'])
        self.assertNotEqual(first['id'], second['id'])
        for candidates in ([first, second], [second, first]):
            selected = select_snapshot(date(2026, 11, 20), candidates)
            self.assertEqual(selected.snapshot_id, second['id'])
            self.assertEqual(selected.rows, select_snapshot(date(2026, 11, 20), [first]).rows)
            self.assertEqual(selected.rejected, ())
        equivalent = prepare_pk5_snapshot(**{
            **later_inputs, 'retrieved_at': later_inputs['retrieved_at'].astimezone(WARSAW),
            'retrieval_completed_at': later_inputs['retrieval_completed_at'].astimezone(WARSAW),
        })
        self.assertEqual(equivalent['id'], second['id'])
        self.assertEqual(prepare_pk5_snapshot(**later_inputs)['id'], second['id'])

    def test_two_real_pre_cutoff_revisions_select_latest_publication_in_both_orders(self):
        early_inputs = self.inputs()
        late_inputs = self.inputs(FIXTURE.with_name('pse_pk5_delivery_2026-11-20_second'))
        early = prepare_pk5_snapshot(**early_inputs)
        late = prepare_pk5_snapshot(**late_inputs)
        before = deepcopy((early, late))
        day = date(2026, 11, 20)
        early_selection = select_snapshot(day, [early])
        late_selection = select_snapshot(day, [late])
        self.assertGreater(late_selection.latest_publication_ts_utc,
                           early_selection.latest_publication_ts_utc)
        self.assertLess(late_selection.retrieval_completed_at_utc, cutoff_for_day(day))
        self.assertNotEqual(early['raw_sha256'], late['raw_sha256'])
        for candidates in ([early, late], [late, early]):
            selected = select_snapshot(day, candidates)
            self.assertEqual(selected.snapshot_id, late['id'])
            self.assertEqual(selected.rows, late_selection.rows)
            self.assertEqual(selected.rejected, ((early['id'], 'SUPERSEDED_JWCD_PUBLICATIONS'),))
        self.assertEqual((early, late), before)

    def test_invalid_capture_timing_is_rejected_before_json_decode(self):
        inputs = self.inputs()
        cutoff = cutoff_for_day(date(2026, 11, 20))
        cases = (
            (cutoff + timedelta(microseconds=1), cutoff + timedelta(microseconds=1),
             'RETRIEVAL_STARTED_AFTER_CUTOFF'),
            (cutoff, cutoff + timedelta(microseconds=1), 'RETRIEVAL_COMPLETED_AFTER_CUTOFF'),
            (cutoff, cutoff - timedelta(microseconds=1), 'INVALID_RETRIEVAL_ORDER'),
        )
        for started, completed, reason in cases:
            with self.subTest(reason=reason):
                with patch('shadow_pk5_snapshot.json.loads') as decode:
                    with self.assertRaisesRegex(ValueError, reason):
                        prepare_pk5_snapshot(**{
                            **inputs, 'retrieved_at': started, 'retrieval_completed_at': completed,
                        })
                    decode.assert_not_called()

    def test_real_after_cutoff_capture_is_rejected_before_json_decode(self):
        inputs = self.inputs(FIXTURE.with_name('pse_pk5_delivery_2026-11-20_after_cutoff'))
        self.assertGreater(inputs['retrieved_at'], cutoff_for_day(date(2026, 11, 20)))
        with patch('shadow_pk5_snapshot.json.loads') as decode:
            with self.assertRaisesRegex(ValueError, 'RETRIEVAL_STARTED_AFTER_CUTOFF'):
                prepare_pk5_snapshot(**inputs)
            decode.assert_not_called()

    def test_submicrosecond_publication_cannot_be_truncated_into_cutoff(self):
        inputs = self.inputs()
        cutoff = cutoff_for_day(date(2026, 11, 20))
        for fraction, valid in (('000000', True), ('0000001', False)):
            payload = json.loads(inputs['raw'])
            payload['value'][0]['publication_ts_utc'] = '2026-11-19 08:50:00.' + fraction
            payload['value'][0]['publication_ts'] = '2026-11-19 09:50:00.' + fraction
            supplied = {**inputs, 'raw': json.dumps(payload).encode(),
                        'retrieved_at': cutoff, 'retrieval_completed_at': cutoff}
            with self.subTest(fraction=fraction):
                if valid:
                    self.assertEqual(len(prepare_pk5_snapshot(**supplied)['rows']), 24)
                else:
                    with self.assertRaisesRegex(ValueError, 'timestamp'):
                        prepare_pk5_snapshot(**supplied)

    def test_source_timestamps_require_full_time_and_supported_precision(self):
        inputs = self.inputs()
        cases = (
            ('plan_dtime_utc', '2026-11-20'),
            ('plan_dtime_utc', '2026-11-20 00:00:00.0000001'),
            ('publication_ts', '2026-11-19 08:12:42.3280001'),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                payload = json.loads(inputs['raw'])
                payload['value'][0][field] = value
                with self.assertRaisesRegex(ValueError, 'timestamp'):
                    prepare_pk5_snapshot(**{**inputs, 'raw': json.dumps(payload).encode()})

    def test_real_capture_is_complete_and_available_before_cutoff(self):
        inputs = self.inputs()
        snapshot = prepare_pk5_snapshot(**inputs)
        selection = select_snapshot(date(2026, 11, 20), [snapshot])
        self.assertEqual(selection.rejected, ())
        self.assertEqual(len(selection.rows), 24)
        self.assertEqual(selection.rows[0], (datetime(2026, 11, 19, 23, tzinfo=timezone.utc), Decimal('9395.0')))
        self.assertEqual(selection.rows[-1][0], datetime(2026, 11, 20, 22, tzinfo=timezone.utc))
        self.assertEqual(selection.latest_publication_ts_utc,
                         datetime(2026, 11, 19, 7, 12, 42, 328000, tzinfo=timezone.utc))
        self.assertLess(selection.retrieval_completed_at_utc, cutoff_for_day(date(2026, 11, 20)))
        source = json.loads(inputs['raw'], parse_float=Decimal)['value']
        self.assertEqual([value for _, value in selection.rows],
                         [row['fcst_gen_unit_stor_prov'] for row in source])

    def test_cutoff_boundary_applies_to_capture_completion_and_publication(self):
        inputs = self.inputs()
        cutoff = cutoff_for_day(date(2026, 11, 20))
        boundary = {**inputs, 'retrieved_at': cutoff, 'retrieval_completed_at': cutoff}
        self.assertEqual(len(prepare_pk5_snapshot(**boundary)['rows']), 24)
        with self.assertRaisesRegex(ValueError, 'RETRIEVAL_COMPLETED_AFTER_CUTOFF'):
            prepare_pk5_snapshot(**{**boundary, 'retrieval_completed_at': cutoff + timedelta(microseconds=1)})
        for late in (False, True):
            payload = json.loads(inputs['raw'])
            published = cutoff + timedelta(microseconds=int(late))
            payload['value'][0]['publication_ts_utc'] = published.replace(tzinfo=None).isoformat(' ')
            payload['value'][0]['publication_ts'] = (published + timedelta(hours=1)).replace(tzinfo=None).isoformat(' ')
            supplied = {**boundary, 'raw': json.dumps(payload).encode()}
            if late:
                with self.assertRaisesRegex(ValueError, 'PUBLICATION_AFTER_CUTOFF'):
                    prepare_pk5_snapshot(**supplied)
            else:
                self.assertEqual(len(prepare_pk5_snapshot(**supplied)['rows']), 24)

    def test_rejects_wrong_period_delivery_duplicate_and_partial_response(self):
        inputs = self.inputs()
        original = json.loads(inputs['raw'])
        for case in ('period', 'delivery', 'duplicate', 'partial', 'pagination', 'publication_missing', 'publication_utc_missing', 'publication_local', 'jwcd'):
            payload = deepcopy(original)
            if case == 'period':
                payload['value'][0]['plan_dtime_utc'] = '2026-11-20 01:00:00'
            elif case == 'delivery':
                payload['value'][0]['business_date'] = '2026-11-19'
            elif case == 'duplicate':
                payload['value'][-1] = payload['value'][0]
            elif case == 'partial':
                payload['value'].pop()
            elif case == 'pagination':
                payload['nextLink'] = 'https://api.raporty.pse.pl/api/pk5l-wp?page=2'
            elif case == 'publication_missing':
                payload['value'][0].pop('publication_ts')
            elif case == 'publication_utc_missing':
                payload['value'][0].pop('publication_ts_utc')
            elif case == 'publication_local':
                payload['value'][0]['publication_ts'] = '2026-11-19 09:12:42.328'
            else:
                payload['value'][0]['fcst_gen_unit_stor_prov'] = None
            with self.subTest(case=case), self.assertRaises(ValueError):
                prepare_pk5_snapshot(**{**inputs, 'raw': json.dumps(payload).encode()})
        self.assertEqual(json.loads(inputs['raw']), original)

    def test_rejects_ambiguous_source_fields_and_dst(self):
        inputs = self.inputs()
        with self.assertRaisesRegex(ValueError, 'Duplicate PK5 JSON field'):
            prepare_pk5_snapshot(**{**inputs, 'raw': b'{"value": [], "value": []}'})
        with self.assertRaisesRegex(ValueError, 'source URL'):
            prepare_pk5_snapshot(**{**inputs, 'source_url': 'https://example.com/api/pk5l-wp'})
        for day in ('2026-03-29', '2026-10-25'):
            with self.subTest(day=day), self.assertRaisesRegex(ValueError, 'DST mapping'):
                prepare_pk5_snapshot(**{**inputs, 'day': day})
