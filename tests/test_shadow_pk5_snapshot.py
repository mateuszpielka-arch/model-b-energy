from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import gzip
from hashlib import sha256
import json
from pathlib import Path
import unittest

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
