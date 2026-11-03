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
        out=select_snapshot(a['delivery_date'],[b,a,c])
        self.assertEqual(out.snapshot_id,'latest-eligible'); self.assertEqual(out.rows[0][1],11000)
        self.assertEqual(len(out.rejected),1)

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
