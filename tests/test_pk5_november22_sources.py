from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import gzip
from hashlib import sha256
import json
from pathlib import Path
import unittest

from shadow_asof import cutoff_for_day, select_snapshot
from shadow_pk5_snapshot import prepare_pk5_snapshot


FIXTURES = Path(__file__).parent / 'fixtures'


def source(name):
    directory = FIXTURES / name
    metadata = json.loads((directory / 'capture.json').read_text())
    raw = gzip.decompress((directory / metadata['raw_file']).read_bytes())
    capture = dict(raw=raw, day=metadata['delivery_date'], source_url=metadata['url'],
                   retrieved_at=datetime.fromisoformat(metadata['retrieved_at_utc']),
                   retrieval_completed_at=datetime.fromisoformat(metadata['retrieval_completed_at_utc']))
    return metadata, raw, capture, prepare_pk5_snapshot(**capture)


class November22Pk5Tests(unittest.TestCase):

    def test_real_mixed_publication_revision_selects_one_whole_capture_in_both_orders(self):
        _, _, _, early = source('pse_pk5_delivery_2026-11-22')
        metadata, raw, capture, latest = source('pse_pk5_delivery_2026-11-22_second')
        before = deepcopy((early, latest))
        payload = json.loads(raw, parse_float=Decimal)['value']
        expected_publications = {
            datetime(2026, 11, 21, 1, 3, 6, 832000, tzinfo=timezone.utc): 10,
            datetime(2026, 11, 21, 1, 12, 31, 438000, tzinfo=timezone.utc): 14,
        }
        self.assertEqual(Counter(row['publication_ts'] for row in latest['rows']),
                         expected_publications)
        self.assertEqual(
            [row['publication_ts'] for row in latest['rows']],
            [datetime.fromisoformat(row['publication_ts_utc']).replace(tzinfo=timezone.utc)
             for row in payload],
        )
        self.assertEqual(sha256(raw).hexdigest(), metadata['raw_sha256'])
        self.assertEqual(len(raw), metadata['bytes'])
        self.assertEqual(latest['id'], metadata['snapshot_id'])
        self.assertEqual((metadata['http_status'], metadata['rows']), (200, 24))
        previous = dict(select_snapshot(date(2026, 11, 22), [early]).rows)
        for candidates in ([early, latest], [latest, early]):
            selected = select_snapshot(date(2026, 11, 22), candidates)
            self.assertEqual(selected.snapshot_id, latest['id'])
            self.assertEqual(selected.latest_publication_ts_utc, max(expected_publications))
            self.assertLessEqual(selected.latest_publication_ts_utc, capture['retrieved_at'])
            self.assertLess(capture['retrieval_completed_at'], selected.cutoff_utc)
            self.assertEqual([value for _, value in selected.rows],
                             [row['fcst_gen_unit_stor_prov'] for row in payload])
            self.assertEqual(sum(previous[period] != value for period, value in selected.rows), 23)
            self.assertEqual(selected.rows[0][1], Decimal('6170.00000'))
            self.assertEqual(selected.rows[-1][1], Decimal('9472.00000'))
            self.assertEqual(selected.rejected, ((early['id'], 'SUPERSEDED_JWCD_PUBLICATIONS'),))
        self.assertEqual((early, latest), before)

    def test_real_early_weekend_capture_is_complete_and_selected_before_its_own_cutoff(self):
        metadata, raw, capture, snapshot = source('pse_pk5_delivery_2026-11-22')
        self.assertEqual((metadata['http_status'], metadata['rows'], metadata['bytes']),
                         (200, 24, len(raw)))
        self.assertEqual(sha256(raw).hexdigest(), metadata['raw_sha256'])
        self.assertEqual(snapshot['id'], metadata['snapshot_id'])
        self.assertEqual(metadata['response_url'], metadata['url'])
        selected = select_snapshot(date(2026, 11, 22), [snapshot])
        self.assertEqual(selected.snapshot_id, metadata['snapshot_id'])
        self.assertEqual(selected.cutoff_utc, datetime(2026, 11, 21, 8, 50, tzinfo=timezone.utc))
        self.assertEqual(selected.latest_publication_ts_utc,
                         datetime(2026, 11, 20, 20, 2, 37, 114000, tzinfo=timezone.utc))
        self.assertLessEqual(selected.latest_publication_ts_utc, capture['retrieved_at'])
        self.assertLessEqual(capture['retrieved_at'], capture['retrieval_completed_at'])
        self.assertLess(capture['retrieval_completed_at'], selected.cutoff_utc)
        payload = json.loads(raw, parse_float=Decimal)['value']
        self.assertEqual([value for _, value in selected.rows],
                         [row['fcst_gen_unit_stor_prov'] for row in payload])
        self.assertEqual(selected.rows[0],
                         (datetime(2026, 11, 21, 23, tzinfo=timezone.utc), Decimal('6192')))
        self.assertEqual(selected.rows[-1],
                         (datetime(2026, 11, 22, 22, tzinfo=timezone.utc), Decimal('9701')))

    def test_real_adjacent_delivery_candidates_cannot_mix_dates_or_override_cutoffs(self):
        _, _, _, previous = source('pse_pk5_delivery_2026-11-21_third')
        _, _, capture, current = source('pse_pk5_delivery_2026-11-22')
        before = deepcopy((previous, current))
        for candidates in ([previous, current], [current, previous]):
            for day, expected, other in (
                (date(2026, 11, 21), previous, current),
                (date(2026, 11, 22), current, previous),
            ):
                with self.subTest(day=day, order=[row['id'] for row in candidates]):
                    selected = select_snapshot(day, candidates)
                    self.assertEqual(selected.snapshot_id, expected['id'])
                    self.assertEqual(selected.rows, select_snapshot(day, [expected]).rows)
                    self.assertIn((other['id'], 'WRONG_DELIVERY_DATE'), selected.rejected)
        self.assertEqual((previous, current), before)
        cutoff = cutoff_for_day(date(2026, 11, 22))
        with self.assertRaisesRegex(ValueError, 'RETRIEVAL_STARTED_AFTER_CUTOFF'):
            prepare_pk5_snapshot(**{**capture,
                'retrieved_at': cutoff + timedelta(microseconds=1),
                'retrieval_completed_at': cutoff + timedelta(microseconds=2)})
