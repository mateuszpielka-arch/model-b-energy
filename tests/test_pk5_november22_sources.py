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
