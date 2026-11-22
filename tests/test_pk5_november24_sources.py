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
from shadow_scoring import expected_periods


DIRECTORY = Path(__file__).parent / 'fixtures/pse_pk5_delivery_2026-11-24'
DAY = date(2026, 11, 24)


def source():
    metadata = json.loads((DIRECTORY / 'capture.json').read_text())
    raw = gzip.decompress((DIRECTORY / metadata['raw_file']).read_bytes())
    capture = dict(raw=raw, day=DAY.isoformat(), source_url=metadata['url'],
                   retrieved_at=datetime.fromisoformat(metadata['retrieved_at_utc']),
                   retrieval_completed_at=datetime.fromisoformat(metadata['retrieval_completed_at_utc']))
    return metadata, raw, capture


class November24Pk5Tests(unittest.TestCase):
    def test_real_early_capture_retains_all_values_publications_and_exact_proof(self):
        metadata, raw, capture = source()
        before = deepcopy(capture)
        snapshot = prepare_pk5_snapshot(**capture)
        selected = select_snapshot(DAY, [snapshot])
        self.assertEqual((metadata['http_status'], metadata['rows'], metadata['bytes']), (200, 24, 18663))
        self.assertEqual(len(raw), metadata['bytes'])
        self.assertEqual(sha256(raw).hexdigest(),
                         'f3a18364f4a41446dc67b163c3b258c5ce7665dd43ede9f0f705cc7ed88b55c5')
        self.assertEqual(snapshot['id'],
                         'pk5-capture-sha256:7b073cbe000adecf5232208a45d48bc1430e17ca11f7d038e501819967e19e10')
        self.assertEqual(selected.snapshot_id, metadata['snapshot_id'])
        self.assertEqual(metadata['retrieved_at_utc'], '2026-11-22T21:12:48.103863+00:00')
        self.assertEqual(metadata['retrieval_completed_at_utc'], '2026-11-22T21:12:55.417751+00:00')
        published = datetime(2026, 11, 22, 21, 5, 7, 376000, tzinfo=timezone.utc)
        self.assertEqual(Counter(row['publication_ts'] for row in snapshot['rows']), {published: 24})
        payload = json.loads(raw, parse_float=Decimal)['value']
        self.assertEqual([value for _, value in selected.rows],
                         [row['fcst_gen_unit_stor_prov'] for row in payload])
        self.assertEqual(tuple(period for period, _ in selected.rows), expected_periods(DAY))
        self.assertEqual((selected.rows[0][1], selected.rows[-1][1]),
                         (Decimal('8615.00000'), Decimal('12155.00000')))
        self.assertEqual(selected.cutoff_utc, datetime(2026, 11, 23, 8, 50, tzinfo=timezone.utc))
        self.assertLessEqual(published, capture['retrieved_at'])
        self.assertLessEqual(capture['retrieved_at'], capture['retrieval_completed_at'])
        self.assertLess(capture['retrieval_completed_at'], selected.cutoff_utc)
        self.assertEqual(capture, before)

    def test_real_capture_cannot_fill_another_day_or_bypass_its_own_cutoff(self):
        metadata, _, capture = source()
        snapshot = prepare_pk5_snapshot(**capture)
        before = deepcopy(snapshot)
        wrong = select_snapshot(date(2026, 11, 23), [snapshot])
        self.assertIsNone(wrong.snapshot_id)
        self.assertEqual(wrong.rejected, ((snapshot['id'], 'WRONG_DELIVERY_DATE'),))
        cutoff = cutoff_for_day(DAY)
        for updates, reason in (
            ({'retrieval_completed_at': cutoff + timedelta(microseconds=1)},
             'RETRIEVAL_COMPLETED_AFTER_CUTOFF'),
            ({'retrieved_at': cutoff + timedelta(microseconds=1),
              'retrieval_completed_at': cutoff + timedelta(microseconds=2)},
             'RETRIEVAL_STARTED_AFTER_CUTOFF'),
        ):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                prepare_pk5_snapshot(**{**capture, **updates})
        self.assertEqual(snapshot, before)
        self.assertEqual(select_snapshot(DAY, [snapshot]).snapshot_id, metadata['snapshot_id'])


if __name__ == '__main__':
    unittest.main()
