import csv
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import unittest
from shadow_archive_batch import SOURCE_URL, prepare_batch, classify_existing

FIX = Path(__file__).parent/'fixtures'

class ArchiveAdmissionTests(unittest.TestCase):
    def args(self):
        raw=(FIX/'tge_rdn_captured_2026-11-01.html').read_bytes()
        ref=(FIX/'tge_fix1_2026-11-02_reference.csv').read_bytes()
        rows=csv.DictReader(x for x in ref.decode().splitlines() if not x.startswith('#'))
        return dict(raw=raw,day='2026-11-02',source_url=SOURCE_URL,
                    retrieved_at=datetime(2026,11,1,20,tzinfo=timezone.utc),
                    reference_rows=[(r['hour'],r['price_pln_mwh']) for r in rows],
                    reference_sha256=sha256(ref).hexdigest())

    def test_real_source_and_reference_bind_to_immutable_batch(self):
        kw=self.args(); batch=prepare_batch(**kw)
        self.assertEqual(len(batch.rows),24)
        self.assertEqual(batch.raw_sha256,sha256(kw['raw']).hexdigest())
        self.assertEqual(batch.rows[0][0],datetime(2026,11,1,23,tzinfo=timezone.utc))
        self.assertEqual(batch.rows[-1][0],datetime(2026,11,2,22,tzinfo=timezone.utc))

    def test_reference_mismatch_blocks_whole_batch(self):
        kw=self.args(); kw['reference_rows'][7]=('H08','1.00')
        with self.assertRaisesRegex(ValueError,'H08'): prepare_batch(**kw)

    def test_missing_duplicate_and_nonfinite_reference(self):
        for mode in ('missing','duplicate','nonfinite'):
            kw=self.args()
            if mode=='missing': kw['reference_rows'].pop()
            if mode=='duplicate': kw['reference_rows'].append(kw['reference_rows'][0])
            if mode=='nonfinite': kw['reference_rows'][0]=('H01','NaN')
            with self.subTest(mode=mode), self.assertRaises(ValueError): prepare_batch(**kw)

    def test_source_hash_and_timestamp_required(self):
        for field,value in [('source_url','https://example.com'),('reference_sha256',''),
                            ('retrieved_at',datetime(2026,11,1)),('raw',b'')]:
            kw=self.args(); kw[field]=value
            with self.subTest(field=field), self.assertRaises(ValueError): prepare_batch(**kw)

    def test_unchanged_replay_and_conflict_never_overwrite(self):
        batch=prepare_batch(**self.args()); old=list(batch.rows)
        self.assertEqual(classify_existing(batch,[]),'INSERT')
        self.assertEqual(classify_existing(batch,old),'REPLAY')
        p,v=old[0]; old[0]=(p,v+1); before=list(old)
        self.assertEqual(classify_existing(batch,old),'QUARANTINE')
        self.assertEqual(old,before)

    def test_partial_and_duplicate_existing_quarantined(self):
        batch=prepare_batch(**self.args())
        self.assertEqual(classify_existing(batch,batch.rows[:-1]),'QUARANTINE')
        self.assertEqual(classify_existing(batch,batch.rows+batch.rows[:1]),'QUARANTINE')

    def test_retrieval_change_does_not_change_identity(self):
        kw=self.args(); a=prepare_batch(**kw)
        kw['retrieved_at']=datetime(2026,11,2,tzinfo=timezone.utc)
        self.assertEqual(a.identity,prepare_batch(**kw).identity)
