import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import test_shadow_archive_batch as fixtures
from shadow_archive_batch import prepare_batch
from shadow_postgres_archive import connect_test_database, initialize, archive_batch
from shadow_postgres_scores import initialize_scores, score_archived_day

DSN=os.environ.get('FIX1_SHADOW_TEST_DSN')

@unittest.skipUnless(DSN,'Set FIX1_SHADOW_TEST_DSN for PostgreSQL integration')
class ScorePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.conn=connect_test_database(DSN); initialize(self.conn); initialize_scores(self.conn)
        self.conn.execute('TRUNCATE fix1_shadow_test.scores,fix1_shadow_test.quarantine,fix1_shadow_test.prices,fix1_shadow_test.batches RESTART IDENTITY')
        kw=fixtures.ArchiveAdmissionTests().args(); self.batch=prepare_batch(**kw)
        archive_batch(self.conn,self.batch,kw['raw'])
        self.kw=dict(day=self.batch.delivery_date,forecast=list(self.batch.rows),
            forecast_id='synthetic-equals-actual',model_version='synthetic-test-only',forecast_status='SHADOW',
            issued_at=datetime(2026,11,1,8,tzinfo=timezone.utc),
            publication_ts=datetime(2026,11,1,6,tzinfo=timezone.utc),
            retrieved_at=datetime(2026,11,1,7,tzinfo=timezone.utc))

    def tearDown(self): self.conn.close()
    def count(self): return self.conn.execute('SELECT count(*) FROM fix1_shadow_test.scores').fetchone()[0]

    def test_store_replay_zero_metrics_and_coverage(self):
        decision,s=score_archived_day(self.conn,**self.kw)
        self.assertEqual(decision,'INSERT'); self.assertEqual(s.mae,0)
        self.assertEqual(s.mape_periods,24); self.assertEqual(s.bias,0)
        self.kw['forecast']=list(reversed(self.kw['forecast']))
        self.assertEqual(score_archived_day(self.conn,**self.kw)[0],'REPLAY')
        self.assertEqual(self.count(),1)

    def test_equivalent_decimal_spellings_replay_without_writing(self):
        first, *rest = self.kw['forecast']
        period, _ = first
        self.kw['forecast'] = [(period, Decimal('-0.00')), *rest]
        decision, expected = score_archived_day(self.conn, **self.kw)
        self.assertEqual(decision, 'INSERT')
        before = self.conn.execute('SELECT input_sha256,payload FROM fix1_shadow_test.scores').fetchone()

        self.kw['forecast'] = [
            (p, text + ('0' if '.' in text else '.0'))
            for p, value in reversed(rest)
            for text in [format(value, 'f')]
        ]
        self.kw['forecast'].append((period, '0E+3'))
        decision, replay = score_archived_day(self.conn, **self.kw)

        self.assertEqual(decision, 'REPLAY')
        self.assertEqual(replay, expected)
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.conn.execute('SELECT input_sha256,payload FROM fix1_shadow_test.scores').fetchone(),
                         before)

    def test_legacy_fingerprint_can_replay_original_inputs(self):
        from dataclasses import asdict
        from hashlib import sha256
        import json
        from psycopg.types.json import Jsonb

        period, _ = self.kw['forecast'][0]
        self.kw['forecast'][0] = (period, '0.000')
        _, score = score_archived_day(self.conn, **self.kw)
        inputs = dict(
            day=self.kw['day'].isoformat(),
            forecast_status=self.kw['forecast_status'],
            forecast=sorted((p.isoformat(), str(Decimal(str(v)))) for p, v in self.kw['forecast']),
        )
        for key in ('issued_at', 'publication_ts', 'retrieved_at'):
            inputs[key] = self.kw[key].isoformat()
        legacy_hash = sha256(json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        legacy_payload = json.loads(json.dumps(asdict(score), default=str))
        self.conn.execute('UPDATE fix1_shadow_test.scores SET input_sha256=%s,payload=%s',
                          (legacy_hash, Jsonb(legacy_payload)))

        self.assertEqual(score_archived_day(self.conn, **self.kw)[0], 'REPLAY')
        self.assertEqual(self.conn.execute('SELECT input_sha256,payload FROM fix1_shadow_test.scores').fetchone(),
                         (legacy_hash, legacy_payload))

    def test_changed_forecast_same_identity_never_overwrites(self):
        score_archived_day(self.conn,**self.kw)
        old=self.conn.execute('SELECT payload FROM fix1_shadow_test.scores').fetchone()
        p,v=self.kw['forecast'][0]; self.kw['forecast'][0]=(p,v+10)
        with self.assertRaisesRegex(ValueError, 'identity conflict: input fingerprint differs'):
            score_archived_day(self.conn, **self.kw)
        self.assertEqual(self.conn.execute('SELECT payload FROM fix1_shadow_test.scores').fetchone(),old)
        self.assertEqual(self.count(),1)

    def test_mismatched_payload_same_fingerprint_never_overwrites(self):
        score_archived_day(self.conn, **self.kw)
        self.conn.execute(
            "UPDATE fix1_shadow_test.scores SET payload = jsonb_set(payload, '{mae}', to_jsonb('999'::text))"
        )
        query = 'SELECT input_sha256,payload FROM fix1_shadow_test.scores'
        before = self.conn.execute(query).fetchone()

        with self.assertRaisesRegex(ValueError, 'identity conflict: stored score payload differs'):
            score_archived_day(self.conn, **self.kw)

        self.assertEqual(self.conn.execute(query).fetchone(), before)
        self.assertEqual(self.count(), 1)

    def test_wrong_actual_or_missing_period_blocks_scoring(self):
        p,_=self.batch.rows[0]
        self.conn.execute('UPDATE fix1_shadow_test.prices SET price=60 WHERE period_start_utc=%s',(p,))
        with self.assertRaises(ValueError): score_archived_day(self.conn,**self.kw)
        self.assertEqual(self.count(),0)
        self.conn.execute('DELETE FROM fix1_shadow_test.prices WHERE period_start_utc=%s',(p,))
        with self.assertRaises(ValueError): score_archived_day(self.conn,**self.kw)

    def test_invalid_evidence_blocks_score_replay_without_changing_saved_score(self):
        score_archived_day(self.conn, **self.kw)
        query = 'SELECT input_sha256, payload FROM fix1_shadow_test.scores'
        stored = self.conn.execute(query).fetchall()
        for field, value, error in (
            ('raw_html', b'corrupt HTML', 'Archived evidence hash/date mismatch'),
            ('reference_sha256', 'missing', 'Invalid archived reference SHA-256'),
            ('parser_version', 'unknown', 'Unsupported archived parser version'),
            ('source_url', 'https://example.test/energia-elektryczna-rdn', 'Unexpected source URL'),
            ('source_url', 'https://tge.pl/energia-elektryczna-rdn?dateShow=01-01-2026',
             'Historical session URL does not match delivery date'),
        ):
            with self.subTest(field=field, error=error):
                original, = self.conn.execute(
                    f'SELECT {field} FROM fix1_shadow_test.batches'
                ).fetchone()
                try:
                    self.conn.execute(f'UPDATE fix1_shadow_test.batches SET {field} = %s', (value,))
                    with self.assertRaisesRegex(ValueError, error):
                        score_archived_day(self.conn, **self.kw)
                    self.assertEqual(self.conn.execute(query).fetchall(), stored)
                finally:
                    self.conn.execute(f'UPDATE fix1_shadow_test.batches SET {field} = %s', (original,))

        self.assertEqual(score_archived_day(self.conn, **self.kw)[0], 'REPLAY')

    def test_quarantined_batch_blocks_scoring(self):
        bid=self.conn.execute('SELECT id FROM fix1_shadow_test.batches').fetchone()[0]
        self.conn.execute('INSERT INTO fix1_shadow_test.quarantine(batch_id,reason) VALUES (%s,%s)',(bid,'test'))
        with self.assertRaises(ValueError): score_archived_day(self.conn,**self.kw)
        self.assertEqual(self.count(),0)

    def test_quarantine_of_another_batch_for_day_blocks_replay(self):
        score_archived_day(self.conn, **self.kw)
        previous_score = self.conn.execute(
            'SELECT payload FROM fix1_shadow_test.scores'
        ).fetchone()
        conflicting_batch_id = self.conn.execute(
            """INSERT INTO fix1_shadow_test.batches
                (delivery_date, raw_sha256, reference_sha256, parser_version,
                 source_url, retrieved_at, raw_html)
                SELECT delivery_date, raw_sha256, %s, parser_version,
                       source_url, retrieved_at, raw_html
                FROM fix1_shadow_test.batches
                RETURNING id""",
            ('a' * 64,),
        ).fetchone()[0]
        self.conn.execute(
            'INSERT INTO fix1_shadow_test.quarantine(batch_id,reason) VALUES (%s,%s)',
            (conflicting_batch_id, 'conflicting source'),
        )

        with self.assertRaisesRegex(ValueError, 'Quarantined delivery day'):
            score_archived_day(self.conn, **self.kw)
        self.assertEqual(self.count(), 1)
        self.assertEqual(
            self.conn.execute('SELECT payload FROM fix1_shadow_test.scores').fetchone(),
            previous_score,
        )

    def test_archived_actual_capture_must_follow_issuance(self):
        issued = self.kw['issued_at']
        for seconds in (-1, 0, 1):
            with self.subTest(seconds_after_issuance=seconds):
                self.conn.execute(
                    'UPDATE fix1_shadow_test.batches SET retrieved_at = %s',
                    (issued + timedelta(seconds=seconds),),
                )
                if seconds <= 0:
                    with self.assertRaisesRegex(ValueError, 'capture must be after forecast issuance'):
                        score_archived_day(self.conn, **self.kw)
                    self.assertEqual(self.count(), 0)
                else:
                    self.assertEqual(score_archived_day(self.conn, **self.kw)[0], 'INSERT')

        self.conn.execute('UPDATE fix1_shadow_test.batches SET retrieved_at = %s', (issued,))
        with self.assertRaisesRegex(ValueError, 'capture must be after forecast issuance'):
            score_archived_day(self.conn, **self.kw)
        self.assertEqual(self.count(), 1)

    def test_evaluation_time_rejects_later_capture_and_allows_exact_boundary(self):
        captured = self.batch.retrieved_at
        too_early = captured - timedelta(seconds=1)
        with self.assertRaisesRegex(ValueError, 'not yet available at evaluation time'):
            score_archived_day(self.conn, **self.kw, evaluated_at=too_early)
        self.assertEqual(self.count(), 0)

        same_time = captured.astimezone(timezone(timedelta(hours=1)))
        self.assertEqual(score_archived_day(self.conn, **self.kw, evaluated_at=same_time)[0], 'INSERT')
        self.assertEqual(score_archived_day(self.conn, **self.kw,
                                            evaluated_at=captured + timedelta(seconds=1))[0], 'REPLAY')
        with self.assertRaisesRegex(ValueError, 'not yet available at evaluation time'):
            score_archived_day(self.conn, **self.kw, evaluated_at=too_early)
        self.assertEqual(self.count(), 1)

    def test_late_forecast_has_no_stored_score(self):
        self.kw['issued_at']=datetime(2026,11,1,8,51,tzinfo=timezone.utc)
        with self.assertRaises(ValueError): score_archived_day(self.conn,**self.kw)
        self.assertEqual(self.count(),0)

    def test_four_concurrent_scores_only_insert_once(self):
        def write(_):
            with connect_test_database(DSN) as conn: return score_archived_day(conn,**self.kw)[0]
        with ThreadPoolExecutor(max_workers=4) as pool: decisions=list(pool.map(write,range(4)))
        self.assertEqual(decisions.count('INSERT'),1); self.assertEqual(decisions.count('REPLAY'),3)
        self.assertEqual(self.count(),1)
