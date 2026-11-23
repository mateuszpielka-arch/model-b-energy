from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import gzip
from hashlib import sha256
import json
from itertools import permutations
from pathlib import Path
import unittest

from shadow_asof import cutoff_for_day, select_snapshot
from shadow_pk5_snapshot import prepare_pk5_snapshot
from shadow_scoring import expected_periods


DIRECTORY = Path(__file__).parent / 'fixtures/pse_pk5_delivery_2026-11-24'
DAY = date(2026, 11, 24)


def source(directory=DIRECTORY):
    metadata = json.loads((directory / 'capture.json').read_text())
    raw = gzip.decompress((directory / metadata['raw_file']).read_bytes())
    capture = dict(raw=raw, day=DAY.isoformat(), source_url=metadata['url'],
                   retrieved_at=datetime.fromisoformat(metadata['retrieved_at_utc']),
                   retrieval_completed_at=datetime.fromisoformat(metadata['retrieval_completed_at_utc']))
    return metadata, raw, capture


class November24Pk5Tests(unittest.TestCase):



    def test_sixth_real_revision_wins_all_720_orders_without_period_blending(self):
        older = [prepare_pk5_snapshot(**source(
            DIRECTORY.with_name(DIRECTORY.name + suffix))[2])
                 for suffix in ('', '_second', '_third', '_fourth', '_fifth')]
        metadata, raw, capture = source(DIRECTORY.with_name(DIRECTORY.name + '_sixth'))
        latest = prepare_pk5_snapshot(**capture)
        snapshots = [*older, latest]
        before = deepcopy(snapshots)
        self.assertEqual((metadata['http_status'], metadata['rows'], metadata['bytes']), (200, 24, 18677))
        self.assertEqual((len(raw), sha256(raw).hexdigest()), (
            metadata['bytes'], '1bfff18989de46fe2fb00df77e8dd936551e47d3875db3f90ee996bb103c05c6'))
        self.assertEqual(latest['id'], metadata['snapshot_id'])
        self.assertEqual(latest['id'],
                         'pk5-capture-sha256:c6fde113a3de2d6714c221404f38890e6ed1c5736cd50ae809a2d669cc5738b4')
        published = datetime(2026, 11, 23, 8, 13, 42, 117000, tzinfo=timezone.utc)
        self.assertEqual(Counter(row['publication_ts'] for row in latest['rows']), {published: 24})
        self.assertEqual(metadata['retrieved_at_utc'], '2026-11-23T08:19:27.368850+00:00')
        self.assertEqual(metadata['retrieval_completed_at_utc'], '2026-11-23T08:19:34.382677+00:00')
        self.assertLessEqual(published, capture['retrieved_at'])
        self.assertLessEqual(capture['retrieved_at'], capture['retrieval_completed_at'])
        self.assertLess(capture['retrieval_completed_at'], cutoff_for_day(DAY))
        payload = json.loads(raw, parse_float=Decimal)['value']
        previous_prices = dict(select_snapshot(DAY, older).rows)
        for candidates in permutations(snapshots):
            selected = select_snapshot(DAY, candidates)
            self.assertEqual(selected.snapshot_id, latest['id'])
            self.assertEqual(selected.latest_publication_ts_utc, published)
            self.assertEqual([value for _, value in selected.rows],
                             [row['fcst_gen_unit_stor_prov'] for row in payload])
            self.assertEqual((selected.rows[0][1], selected.rows[-1][1]),
                             (Decimal('8108.00000'), Decimal('11941.00000')))
            self.assertEqual(sum(previous_prices[period] != value for period, value in selected.rows), 24)
            self.assertCountEqual(selected.rejected, [
                (snapshot['id'], 'SUPERSEDED_JWCD_PUBLICATIONS') for snapshot in older
            ])
        self.assertEqual(snapshots, before)

    def test_fifth_real_revision_wins_all_120_orders_without_period_blending(self):
        older = [prepare_pk5_snapshot(**source(
            DIRECTORY.with_name(DIRECTORY.name + suffix))[2])
                 for suffix in ('', '_second', '_third', '_fourth')]
        metadata, raw, capture = source(DIRECTORY.with_name(DIRECTORY.name + '_fifth'))
        latest = prepare_pk5_snapshot(**capture)
        snapshots = [*older, latest]
        before = deepcopy(snapshots)
        self.assertEqual((metadata['http_status'], metadata['rows'], metadata['bytes']), (200, 24, 18677))
        self.assertEqual((len(raw), sha256(raw).hexdigest()), (
            metadata['bytes'], '5bc11ccd1a56b700fbffafb9d208c0b2ca95dc0bc6d47dc880304aa09c1fdc77'))
        self.assertEqual(latest['id'], metadata['snapshot_id'])
        self.assertEqual(latest['id'],
                         'pk5-capture-sha256:5e8bd37d651c7608c20935969b96a3ba5f115a81cecfe209c0536ab252eeb4b6')
        published = datetime(2026, 11, 23, 7, 13, 42, 104000, tzinfo=timezone.utc)
        self.assertEqual(Counter(row['publication_ts'] for row in latest['rows']), {published: 24})
        self.assertEqual(metadata['retrieved_at_utc'], '2026-11-23T07:18:12.912361+00:00')
        self.assertEqual(metadata['retrieval_completed_at_utc'], '2026-11-23T07:18:19.542186+00:00')
        self.assertLessEqual(published, capture['retrieved_at'])
        self.assertLessEqual(capture['retrieved_at'], capture['retrieval_completed_at'])
        self.assertLess(capture['retrieval_completed_at'], cutoff_for_day(DAY))
        payload = json.loads(raw, parse_float=Decimal)['value']
        previous_prices = dict(select_snapshot(DAY, older).rows)
        for candidates in permutations(snapshots):
            selected = select_snapshot(DAY, candidates)
            self.assertEqual(selected.snapshot_id, latest['id'])
            self.assertEqual(selected.latest_publication_ts_utc, published)
            self.assertEqual([value for _, value in selected.rows],
                             [row['fcst_gen_unit_stor_prov'] for row in payload])
            self.assertEqual((selected.rows[0][1], selected.rows[-1][1]),
                             (Decimal('8169.00000'), Decimal('11982.00000')))
            self.assertEqual(sum(previous_prices[period] != value for period, value in selected.rows), 24)
            self.assertCountEqual(selected.rejected, [
                (snapshot['id'], 'SUPERSEDED_JWCD_PUBLICATIONS') for snapshot in older
            ])
        self.assertEqual(snapshots, before)

    def test_fourth_real_revision_wins_all_24_orders_without_period_blending(self):
        older = [prepare_pk5_snapshot(**source(
            DIRECTORY.with_name(DIRECTORY.name + suffix))[2])
                 for suffix in ('', '_second', '_third')]
        metadata, raw, capture = source(DIRECTORY.with_name(DIRECTORY.name + '_fourth'))
        latest = prepare_pk5_snapshot(**capture)
        snapshots = [*older, latest]
        before = deepcopy(snapshots)
        self.assertEqual((metadata['http_status'], metadata['rows'], metadata['bytes']), (200, 24, 18662))
        self.assertEqual((len(raw), sha256(raw).hexdigest()), (
            metadata['bytes'], 'b52e3be34a8134c6d138e1678a9a1c8e06b3b1534894fc4b760f7249f93262a7'))
        self.assertEqual(latest['id'], metadata['snapshot_id'])
        self.assertEqual(latest['id'],
                         'pk5-capture-sha256:84fc58e86395342b9d10855b5a36f38616e3ddec5844907048887642056ab498')
        published = datetime(2026, 11, 23, 4, 13, 37, 112000, tzinfo=timezone.utc)
        self.assertEqual(Counter(row['publication_ts'] for row in latest['rows']), {published: 24})
        self.assertEqual(metadata['retrieved_at_utc'], '2026-11-23T04:16:33.099925+00:00')
        self.assertEqual(metadata['retrieval_completed_at_utc'], '2026-11-23T04:16:40.125791+00:00')
        self.assertLessEqual(published, capture['retrieved_at'])
        self.assertLessEqual(capture['retrieved_at'], capture['retrieval_completed_at'])
        self.assertLess(capture['retrieval_completed_at'], cutoff_for_day(DAY))
        payload = json.loads(raw, parse_float=Decimal)['value']
        previous_prices = dict(select_snapshot(DAY, older).rows)
        for candidates in permutations(snapshots):
            selected = select_snapshot(DAY, candidates)
            self.assertEqual(selected.snapshot_id, latest['id'])
            self.assertEqual(selected.latest_publication_ts_utc, published)
            self.assertEqual([value for _, value in selected.rows],
                             [row['fcst_gen_unit_stor_prov'] for row in payload])
            self.assertEqual((selected.rows[0][1], selected.rows[-1][1]),
                             (Decimal('8396.00000'), Decimal('12055.00000')))
            self.assertEqual(sum(previous_prices[period] != value for period, value in selected.rows), 23)
            self.assertCountEqual(selected.rejected, [
                (snapshot['id'], 'SUPERSEDED_JWCD_PUBLICATIONS') for snapshot in older
            ])
        self.assertEqual(snapshots, before)

    def test_third_real_revision_wins_all_six_orders_without_period_blending(self):
        _, _, early_capture = source()
        _, _, second_capture = source(DIRECTORY.with_name(DIRECTORY.name + '_second'))
        metadata, raw, latest_capture = source(DIRECTORY.with_name(DIRECTORY.name + '_third'))
        snapshots = [prepare_pk5_snapshot(**capture)
                     for capture in (early_capture, second_capture, latest_capture)]
        before = deepcopy(snapshots)
        self.assertEqual((metadata['http_status'], metadata['rows'], metadata['bytes']), (200, 24, 18662))
        self.assertEqual((len(raw), sha256(raw).hexdigest()), (
            metadata['bytes'], '8b585dc921d36ff5cf1acda0f3e0163d754161f8fba6248eae2657dbf6c37e7d'))
        latest = snapshots[-1]
        self.assertEqual(latest['id'], metadata['snapshot_id'])
        self.assertEqual(latest['id'],
                         'pk5-capture-sha256:82207eb47c997127091073af89607944eb67dfffd8e0f97c914c4d983530374a')
        published = datetime(2026, 11, 23, 1, 13, 27, 462000, tzinfo=timezone.utc)
        self.assertEqual(Counter(row['publication_ts'] for row in latest['rows']), {published: 24})
        self.assertEqual(metadata['retrieved_at_utc'], '2026-11-23T01:18:59.865233+00:00')
        self.assertEqual(metadata['retrieval_completed_at_utc'], '2026-11-23T01:19:09.192381+00:00')
        self.assertLessEqual(published, latest_capture['retrieved_at'])
        self.assertLessEqual(latest_capture['retrieved_at'], latest_capture['retrieval_completed_at'])
        self.assertLess(latest_capture['retrieval_completed_at'], cutoff_for_day(DAY))
        payload = json.loads(raw, parse_float=Decimal)['value']
        previous_prices = dict(select_snapshot(DAY, snapshots[:2]).rows)
        for candidates in permutations(snapshots):
            selected = select_snapshot(DAY, candidates)
            self.assertEqual(selected.snapshot_id, latest['id'])
            self.assertEqual(selected.latest_publication_ts_utc, published)
            self.assertEqual([value for _, value in selected.rows],
                             [row['fcst_gen_unit_stor_prov'] for row in payload])
            self.assertEqual((selected.rows[0][1], selected.rows[-1][1]),
                             (Decimal('8421.00000'), Decimal('12057.00000')))
            self.assertEqual(sum(previous_prices[period] != value for period, value in selected.rows), 24)
            self.assertCountEqual(selected.rejected, [
                (snapshot['id'], 'SUPERSEDED_JWCD_PUBLICATIONS') for snapshot in snapshots[:2]
            ])
        self.assertEqual(snapshots, before)

    def test_second_real_revision_wins_whole_capture_in_both_orders(self):
        _, _, early_capture = source()
        metadata, raw, latest_capture = source(DIRECTORY.with_name(DIRECTORY.name + '_second'))
        early = prepare_pk5_snapshot(**early_capture)
        latest = prepare_pk5_snapshot(**latest_capture)
        before = deepcopy((early, latest))
        self.assertEqual((len(raw), sha256(raw).hexdigest()), (
            18661, 'ee1a8d8ff8066f343265285f2ba1fdc087a6d04369a020e0284e38dac1b98699'))
        self.assertEqual(latest['id'], metadata['snapshot_id'])
        self.assertEqual(latest['id'],
                         'pk5-capture-sha256:f19185d51f1e25ddfbde34e69fd3edef88cbb1b3db277ddf6654ac41e3863ad1')
        published = datetime(2026, 11, 22, 23, 14, 20, 888000, tzinfo=timezone.utc)
        self.assertEqual(Counter(row['publication_ts'] for row in latest['rows']), {published: 24})
        self.assertEqual(metadata['retrieved_at_utc'], '2026-11-22T23:16:29.078131+00:00')
        self.assertEqual(metadata['retrieval_completed_at_utc'], '2026-11-22T23:16:38.369227+00:00')
        self.assertLessEqual(published, latest_capture['retrieved_at'])
        self.assertLessEqual(latest_capture['retrieved_at'], latest_capture['retrieval_completed_at'])
        self.assertLess(latest_capture['retrieval_completed_at'], cutoff_for_day(DAY))
        payload = json.loads(raw, parse_float=Decimal)['value']
        old_prices = dict(select_snapshot(DAY, [early]).rows)
        for candidates in ([early, latest], [latest, early]):
            selected = select_snapshot(DAY, candidates)
            self.assertEqual(selected.snapshot_id, latest['id'])
            self.assertEqual(selected.latest_publication_ts_utc, published)
            self.assertEqual([value for _, value in selected.rows],
                             [row['fcst_gen_unit_stor_prov'] for row in payload])
            self.assertEqual((selected.rows[0][1], selected.rows[-1][1]),
                             (Decimal('8522.00000'), Decimal('12169.00000')))
            self.assertEqual(sum(old_prices[period] != value for period, value in selected.rows), 23)
            self.assertEqual(selected.rejected,
                             ((early['id'], 'SUPERSEDED_JWCD_PUBLICATIONS'),))
        self.assertEqual((early, latest), before)

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
