from copy import deepcopy
from datetime import date,datetime,timedelta,timezone
import unittest
from shadow_asof import select_snapshot
from shadow_scoring import expected_periods, WARSAW

UTC=timezone.utc

class AvailabilityTests(unittest.TestCase):
    def snapshot(self,day=date(2026,11,3),sid='early'):
        cutoff=(datetime.combine(day-timedelta(days=1),datetime.min.time(),WARSAW)+timedelta(hours=9,minutes=50)).astimezone(UTC)
        return dict(id=sid,delivery_date=day,retrieved_at=cutoff-timedelta(hours=1),
                    retrieval_completed_at=cutoff-timedelta(minutes=59),
                    rows=[dict(period_start_utc=p,publication_ts=cutoff-timedelta(hours=2),jwcd_mw=10000)
                          for p in expected_periods(day)])

    def test_latest_eligible_ignores_later_revision(self):
        a=self.snapshot(); b=deepcopy(a); b['id']='late'; b['retrieval_completed_at']+=timedelta(hours=2)
        c=deepcopy(a); c['id']='latest-eligible'; c['retrieval_completed_at']+=timedelta(minutes=20)
        c['rows'][0]['jwcd_mw']=11000
        c['rows'][0]['publication_ts'] += timedelta(minutes=1)
        out=select_snapshot(a['delivery_date'],[b,a,c])
        self.assertEqual(out.snapshot_id,'latest-eligible'); self.assertEqual(out.rows[0][1],11000)
        self.assertEqual(dict(out.rejected), {
            'late': 'RETRIEVAL_AFTER_CUTOFF_OR_INVALID_ORDER',
            'early': 'SUPERSEDED_JWCD_PUBLICATIONS',
        })

    def test_request_start_before_cutoff_completion_after_is_rejected(self):
        s=self.snapshot(); s['retrieval_completed_at']=datetime(2026,11,2,8,50,1,tzinfo=UTC)
        self.assertIsNone(select_snapshot(s['delivery_date'],[s]).snapshot_id)

    def test_malformed_candidates_do_not_abort_selection(self):
        good = self.snapshot()
        malformed_row = self.snapshot(sid='bad-row')
        malformed_row['rows'] = [None]
        missing_id = self.snapshot(sid=None)

        result = select_snapshot(good['delivery_date'], [None, malformed_row, missing_id, good])

        self.assertEqual(result.snapshot_id, 'early')
        self.assertEqual(result.rejected, (
            ('', 'INVALID_SNAPSHOT_OBJECT'),
            ('bad-row', 'INVALID_SNAPSHOT_ROW'),
            ('', 'MISSING_OR_DUPLICATE_SNAPSHOT_ID'),
        ))

    def test_out_of_range_utc_timestamps_do_not_discard_valid_autumn_snapshot(self):
        day = date(2026, 10, 25)
        good = self.snapshot(day)
        bad_retrieval = self.snapshot(day, sid='bad-retrieval')
        bad_retrieval['retrieved_at'] = datetime.fromisoformat('0001-01-01T00:00:00+01:00')
        bad_publication = self.snapshot(day, sid='bad-publication')
        bad_publication['rows'][2]['publication_ts'] = datetime.fromisoformat('9999-12-31T23:59:59-01:00')

        result = select_snapshot(day, [bad_retrieval, bad_publication, good])

        self.assertEqual(result.snapshot_id, 'early')
        self.assertEqual(len(result.rows), 25)
        self.assertEqual(result.rejected, (
            ('bad-retrieval', 'Timestamp is outside the supported UTC range'),
            ('bad-publication', 'Timestamp is outside the supported UTC range'),
        ))

    def test_interrupted_snapshot_collection_rejects_even_complete_early_candidate(self):
        good = self.snapshot()

        for error in (OSError, RuntimeError, ValueError):
            with self.subTest(error=error):
                def incomplete_collection():
                    yield good
                    raise error('Collection interrupted before remaining revisions')

                result = select_snapshot(good['delivery_date'], incomplete_collection())
                self.assertIsNone(result.snapshot_id)
                self.assertEqual(result.rows, ())
                self.assertEqual(result.rejected, (('', 'UNREADABLE_SNAPSHOT_COLLECTION'),))

    def test_unreadable_snapshot_rows_leave_complete_pre_cutoff_candidate(self):
        good = self.snapshot()
        broken = self.snapshot(sid='broken')
        broken['retrieval_completed_at'] += timedelta(minutes=10)

        def unreadable(rows):
            yield rows[0]
            raise OSError('Unexpected end of snapshot')

        broken['rows'] = unreadable(broken['rows'])
        result = select_snapshot(good['delivery_date'], [broken, good])

        self.assertEqual(result.snapshot_id, 'early')
        self.assertEqual(len(result.rows), 24)
        self.assertEqual(result.rejected, (('broken', 'UNREADABLE_SNAPSHOT_ROWS'),))

    def test_partial_read_from_custom_iterable_keeps_valid_snapshot(self):
        day = date(2026, 10, 25)
        good = self.snapshot(day)

        class InterruptedRows:
            def __iter__(self):
                yield good['rows'][0]
                raise OSError('Unexpected end of snapshot')

        broken = {**good, 'id': 'partial', 'rows': InterruptedRows()}
        result = select_snapshot(day, [broken, good])
        self.assertEqual(result.snapshot_id, good['id'])
        self.assertEqual(len(result.rows), 25)
        self.assertEqual(result.rejected, (('partial', 'UNREADABLE_SNAPSHOT_ROWS'),))

    def test_late_snapshot_rows_are_not_read(self):
        late = self.snapshot(sid='late')
        late['retrieval_completed_at'] += timedelta(hours=2)

        def must_not_read():
            self.fail('A late snapshot cannot be used')
            yield

        late['rows'] = must_not_read()
        result = select_snapshot(late['delivery_date'], [late])

        self.assertIsNone(result.snapshot_id)
        self.assertEqual(result.rejected, (('late', 'RETRIEVAL_AFTER_CUTOFF_OR_INVALID_ORDER'),))

    def test_missing_completion_is_unverifiable(self):
        s=self.snapshot(); del s['retrieval_completed_at']
        self.assertIsNone(select_snapshot(s['delivery_date'],[s]).snapshot_id)

    def test_one_late_publication_rejects_whole_snapshot(self):
        s=self.snapshot(); s['rows'][10]['publication_ts']=datetime(2026,11,2,9,tzinfo=UTC)
        self.assertIsNone(select_snapshot(s['delivery_date'],[s]).snapshot_id)

    def test_missing_and_duplicate_periods_rejected(self):
        for mode in ('missing','duplicate'):
            s=self.snapshot()
            if mode=='missing': s['rows'].pop()
            else: s['rows'].append(s['rows'][0])
            with self.subTest(mode=mode): self.assertIsNone(select_snapshot(s['delivery_date'],[s]).snapshot_id)

    def test_invalid_jwcd_rejected(self):
        for v in (None,'NaN','Infinity',True):
            s=self.snapshot(); s['rows'][0]['jwcd_mw']=v
            with self.subTest(v=v): self.assertIsNone(select_snapshot(s['delivery_date'],[s]).snapshot_id)

    def test_naive_publication_not_guessed(self):
        s=self.snapshot(); s['rows'][0]['publication_ts']=datetime(2026,11,2,6)
        self.assertIsNone(select_snapshot(s['delivery_date'],[s]).snapshot_id)

    def test_duplicate_ids_fail_closed(self):
        s=self.snapshot(); out=select_snapshot(s['delivery_date'],[s,deepcopy(s)])
        self.assertIsNone(out.snapshot_id); self.assertEqual(len(out.rejected),2)

    def test_late_copy_with_same_id_cannot_change_asof_selection(self):
        def unreadable_rows():
            self.fail('Rows of an ineligible snapshot were read')
            yield

        for day, periods in ((date(2026, 3, 29), 23), (date(2026, 10, 25), 25)):
            with self.subTest(day=day):
                early = self.snapshot(day)
                baseline = select_snapshot(day, [early])
                late = {
                    **early, 'rows': unreadable_rows(),
                    'retrieval_completed_at': baseline.cutoff_utc + timedelta(seconds=1),
                }
                wrong_day = {
                    **early, 'rows': unreadable_rows(),
                    'delivery_date': day + timedelta(days=1),
                }
                out = select_snapshot(day, [late, early, wrong_day])
                self.assertEqual((out.snapshot_id, out.rows), (baseline.snapshot_id, baseline.rows))
                self.assertEqual(len(out.rows), periods)
                self.assertEqual(out.rejected, (
                    ('early', 'RETRIEVAL_AFTER_CUTOFF_OR_INVALID_ORDER'),
                    ('early', 'WRONG_DELIVERY_DATE'),
                ))

    def test_both_dst_days_retain_every_utc_period(self):
        for day,n in [(date(2026,3,29),23),(date(2026,10,25),25)]:
            s=self.snapshot(day)
            with self.subTest(day=day): self.assertEqual(len(select_snapshot(day,[s]).rows),n)

    def test_summer_cutoff_is_0750_utc(self):
        s=self.snapshot(date(2026,7,2)); s['retrieval_completed_at']=datetime(2026,7,1,7,50,1,tzinfo=UTC)
        out=select_snapshot(s['delivery_date'],[s])
        self.assertEqual(out.cutoff_utc.hour,7); self.assertIsNone(out.snapshot_id)

    def test_cutoff_inclusive_and_input_not_mutated(self):
        s=self.snapshot(); s['retrieval_completed_at']=datetime(2026,11,2,8,50,tzinfo=UTC)
        before=deepcopy(s); self.assertEqual(select_snapshot(s['delivery_date'],[s]).snapshot_id,'early')
        self.assertEqual(s,before)

    def test_conflicting_snapshots_with_same_latest_times_block_selection(self):
        first = self.snapshot(sid='first')
        second = self.snapshot(sid='second')
        second['rows'][0]['jwcd_mw'] = 11000
        result = select_snapshot(first['delivery_date'], [second, first])
        self.assertIsNone(result.snapshot_id)
        self.assertEqual(result.rejected, (
            ('first', 'CONFLICTING_LATEST_SNAPSHOTS'),
            ('second', 'CONFLICTING_LATEST_SNAPSHOTS'),
        ))

    def test_equivalent_snapshots_with_same_times_remain_eligible(self):
        first = self.snapshot(sid='first')
        second = self.snapshot(sid='second')
        second['rows'].reverse()
        result = select_snapshot(first['delivery_date'], [first, second])
        self.assertEqual(result.snapshot_id, 'second')
        self.assertEqual(result.rejected, ())

    def test_equal_values_with_ambiguous_latest_publication_metadata_are_rejected(self):
        first = self.snapshot(sid='first')
        second = deepcopy(first)
        second['id'] = 'second'
        first['rows'][0]['publication_ts'] += timedelta(minutes=15)
        second['rows'][1]['publication_ts'] += timedelta(minutes=20)
        for snapshots in ([first, second], [second, first]):
            result = select_snapshot(first['delivery_date'], snapshots)
            self.assertIsNone(result.snapshot_id)
            self.assertEqual(result.rows, ())
            self.assertEqual(result.rejected, (
                ('first', 'CONFLICTING_LATEST_SNAPSHOTS'),
                ('second', 'CONFLICTING_LATEST_SNAPSHOTS'),
            ))

    def test_same_publication_with_changed_jwcd_blocks_later_download(self):
        original = self.snapshot(sid='original')
        later = deepcopy(original)
        later['id'] = 'later'
        later['retrieval_completed_at'] += timedelta(minutes=5)
        later['rows'][0]['jwcd_mw'] = 11000
        result = select_snapshot(original['delivery_date'], [later, original])
        self.assertIsNone(result.snapshot_id)
        self.assertEqual(result.rejected, (
            ('later', 'CONFLICTING_JWCD_PUBLICATION'),
            ('original', 'CONFLICTING_JWCD_PUBLICATION'),
        ))

        later['retrieval_completed_at'] = result.cutoff_utc + timedelta(seconds=1)
        result = select_snapshot(original['delivery_date'], [later, original])
        self.assertEqual(result.snapshot_id, 'original')

    def test_later_capture_cannot_roll_back_newer_complete_publications(self):
        newer = self.snapshot(sid='newer-publications')
        stale = deepcopy(newer)
        stale['id'] = 'later-capture'
        stale['retrieval_completed_at'] += timedelta(minutes=10)
        newer['rows'][0]['publication_ts'] += timedelta(minutes=15)
        newer['rows'][0]['jwcd_mw'] = 11000
        result = select_snapshot(newer['delivery_date'], [stale, newer])
        self.assertEqual(result.snapshot_id, 'newer-publications')
        self.assertEqual(result.rows[0][1], 11000)
        self.assertEqual(result.rejected, (('later-capture', 'SUPERSEDED_JWCD_PUBLICATIONS'),))

    def test_incomparable_publications_keep_whole_snapshot(self):
        first = self.snapshot(sid='first')
        later = deepcopy(first)
        later['id'] = 'later'
        later['retrieval_completed_at'] += timedelta(minutes=10)
        first['rows'][0]['publication_ts'] += timedelta(minutes=15)
        first['rows'][0]['jwcd_mw'] = 11000
        later['rows'][1]['publication_ts'] += timedelta(minutes=15)
        later['rows'][1]['jwcd_mw'] = 12000
        result = select_snapshot(first['delivery_date'], [later, first])
        self.assertEqual(result.snapshot_id, 'later')
        self.assertEqual([value for _, value in result.rows[:2]], [10000, 12000])
