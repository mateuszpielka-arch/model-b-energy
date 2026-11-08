from hashlib import sha256
import json
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from shadow_ingest_manifest import ingest_manifest,load_batches
from shadow_postgres_archive import connect_test_database,initialize

FIX=Path(__file__).parent/'fixtures/tge_history_2026-10-27_2026-11-02'
MANIFEST=FIX/'manifest.json'
DSN=os.environ.get('FIX1_SHADOW_TEST_DSN')

class ManifestTests(unittest.TestCase):
    def test_real_seven_days_dry_run_without_database(self):
        report=ingest_manifest(MANIFEST)
        self.assertTrue(report['dry_run']);self.assertEqual((report['days'],report['periods']),(7,168))
        self.assertTrue(all(r['status']=='VALIDATED_DRY_RUN' for r in report['results']))
        self.assertEqual(report['days_by_status'], {'VALIDATED_DRY_RUN': 7})
        self.assertEqual(report['inserted_periods'], 0)
        self.assertTrue(all('?dateShow=' in r['source_url'] for r in report['results']))

    def test_compressed_official_saturday_evidence_passes_dry_run(self):
        manifest = FIX.with_name('tge_delivery_2026-11-07') / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        (batch, raw), = load_batches(manifest)
        self.assertEqual(len(raw), entry['bytes'])
        self.assertEqual(sha256(raw).hexdigest(), entry['raw_sha256'])
        self.assertEqual(batch.reference_sha256, entry['reference_sha256'])
        report = ingest_manifest(manifest)
        self.assertTrue(report['dry_run'])
        self.assertEqual((report['days'], report['periods']), (1, 24))

    def test_bad_compressed_evidence_fails_before_database_initialization(self):
        source = FIX.with_name('tge_delivery_2026-11-07')
        for change, error in (
            ('truncated', 'Invalid gzip'),
            ('wrong_size', 'byte count mismatch'),
            ('wrong_hash', 'hash mismatch'),
        ):
            with self.subTest(change=change), TemporaryDirectory() as tmp:
                root = Path(tmp) / 'capture'
                shutil.copytree(source, root)
                manifest = root / 'manifest.json'
                data = json.loads(manifest.read_text())
                entry, = data['entries']
                if change == 'truncated':
                    raw = root / entry['raw_file']
                    raw.write_bytes(raw.read_bytes()[:-8])
                elif change == 'wrong_size':
                    entry['bytes'] -= 1
                else:
                    entry['raw_sha256'] = '0' * 64
                manifest.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, error):
                    ingest_manifest(manifest, conn=object())

    def test_ambiguous_reference_csv_is_rejected_even_with_matching_hash(self):
        source = FIX.with_name('tge_delivery_2026-11-07')
        for change, error in (
            ('duplicate', 'duplicate CSV columns'),
            ('extra_field', 'wrong field count'),
            ('missing_field', 'wrong field count'),
            ('open_quote', 'Malformed Fix1 reference CSV'),
            ('missing_header', 'Missing required Fix1 reference CSV columns'),
        ):
            with self.subTest(change=change), TemporaryDirectory() as tmp:
                root = Path(tmp) / 'capture'
                shutil.copytree(source, root)
                manifest = root / 'manifest.json'
                data = json.loads(manifest.read_text())
                entry, = data['entries']
                reference = root / entry['reference_file']
                lines = reference.read_text().splitlines()
                if change == 'duplicate':
                    lines = [lines[0] + ',price_pln_mwh'] + [
                        line + ',' + line.rsplit(',', 1)[1] for line in lines[1:]
                    ]
                elif change == 'extra_field':
                    lines[1] += ',ignored'
                elif change == 'missing_field':
                    lines[1] = lines[1].rsplit(',', 1)[0]
                elif change == 'open_quote':
                    lines[-1] += ',"'
                else:
                    lines[0] = lines[0].replace('price_pln_mwh', 'unlabelled_price')
                malformed = ('\n'.join(lines) + '\n').encode()
                reference.write_bytes(malformed)
                entry['reference_sha256'] = sha256(malformed).hexdigest()
                manifest.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, error):
                    ingest_manifest(manifest, conn=object())

    def test_manifest_requires_matching_session_date_before_database_initialization(self):
        source = FIX.with_name('tge_delivery_2026-11-08')
        for session_date in (None, '2026-11-06', '2026-11-08', '07-11-2026'):
            with self.subTest(session_date=session_date), TemporaryDirectory() as tmp:
                root = Path(tmp) / 'capture'
                shutil.copytree(source, root)
                manifest = root / 'manifest.json'
                data = json.loads(manifest.read_text())
                entry, = data['entries']
                if session_date is None:
                    del entry['session_date']
                else:
                    entry['session_date'] = session_date
                manifest.write_text(json.dumps(data))

                with self.assertRaisesRegex(ValueError, 'session date must be 2026-11-07'):
                    ingest_manifest(manifest, conn=object())

    def test_manifest_requires_matching_hour_count_before_database_initialization(self):
        source = FIX.with_name('tge_delivery_2026-11-08')
        for hours in (None, 23, 25, '24', 24.0, True):
            with self.subTest(hours=hours), TemporaryDirectory() as tmp:
                root = Path(tmp) / 'capture'
                shutil.copytree(source, root)
                manifest = root / 'manifest.json'
                data = json.loads(manifest.read_text())
                entry, = data['entries']
                if hours is None:
                    del entry['hours']
                else:
                    entry['hours'] = hours
                manifest.write_text(json.dumps(data))

                with self.assertRaisesRegex(ValueError, 'hour count.*2026-11-08'):
                    ingest_manifest(manifest, conn=object())

    def test_manifest_requires_explicit_historical_session_url(self):
        source = FIX.with_name('tge_delivery_2026-11-08')
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / 'capture'
            shutil.copytree(source, root)
            manifest = root / 'manifest.json'
            data = json.loads(manifest.read_text())
            entry, = data['entries']
            url = entry['url']
            self.assertEqual(ingest_manifest(manifest)['periods'], 24)

            for source_url, error in (
                (None, 'Historical TGE session URL required'),
                (url.split('?')[0], 'Historical TGE session URL required'),
                (url.replace('07-11-2026', '08-11-2026'), 'session URL does not match'),
                (url + '&dateShow=07-11-2026', 'session URL does not match'),
            ):
                with self.subTest(source_url=source_url):
                    if source_url is None:
                        del entry['url']
                    else:
                        entry['url'] = source_url
                    manifest.write_text(json.dumps(data))

                    with self.assertRaisesRegex(ValueError, error):
                        ingest_manifest(manifest, conn=object())

    def test_manifest_requires_fix1_retrieval_after_forecast_cutoff(self):
        source = FIX.with_name('tge_delivery_2026-11-09')
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / 'capture'
            shutil.copytree(source, root)
            manifest = root / 'manifest.json'
            data = json.loads(manifest.read_text())
            entry, = data['entries']
            for stamp, error in (
                (None, 'requires an aware Fix1 retrieval timestamp'),
                ('2026-11-08T09:51:00', 'requires an aware Fix1 retrieval timestamp'),
                ('2026-11-08T08:49:59Z', 'retrieval must be after D-1 09:50'),
                ('2026-11-08T09:50:00+01:00', 'retrieval must be after D-1 09:50'),
            ):
                with self.subTest(stamp=stamp):
                    entry['retrieved_at_utc'] = stamp
                    manifest.write_text(json.dumps(data))
                    with self.assertRaisesRegex(ValueError, error):
                        ingest_manifest(manifest, conn=object())

            entry['retrieved_at_utc'] = '2026-11-08T08:50:01Z'
            manifest.write_text(json.dumps(data))
            self.assertEqual(ingest_manifest(manifest)['periods'], 24)

    def test_manifest_paths_cannot_escape_directory(self):
        with TemporaryDirectory() as tmp:
            data=json.loads(MANIFEST.read_text());data['entries'][0]['raw_file']=str((FIX/data['entries'][0]['raw_file']).resolve())
            p=Path(tmp)/'manifest.json';p.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'inside'):load_batches(p)

    def test_wrong_historical_session_and_altered_hash_fail(self):
        data=json.loads(MANIFEST.read_text());e=data['entries'][0]
        from shadow_archive_batch import prepare_batch
        batch,raw=load_batches(MANIFEST)[0]
        with self.assertRaisesRegex(ValueError,'session'):
            prepare_batch(raw=raw,day=e['delivery_date'],source_url=e['url'].replace('26-10','25-10'),
                          retrieved_at=batch.retrieved_at,reference_rows=[],reference_sha256=batch.reference_sha256)
        with TemporaryDirectory() as tmp:
            p=Path(tmp);e=dict(e);e['raw_sha256']='0'*64
            (p/e['raw_file']).write_bytes(raw)
            (p/e['reference_file']).write_bytes((FIX/e['reference_file']).read_bytes())
            m=p/'manifest.json';m.write_text(json.dumps({'entries':[e],'errors':[]}))
            with self.assertRaisesRegex(ValueError,'hash mismatch'):load_batches(m)

@unittest.skipUnless(DSN,'Set FIX1_SHADOW_TEST_DSN for isolated PostgreSQL')
class ManifestPostgresTests(unittest.TestCase):
    def setUp(self):
        self.conn=connect_test_database(DSN);initialize(self.conn)
        self.conn.execute('TRUNCATE fix1_shadow_test.quarantine,fix1_shadow_test.prices,fix1_shadow_test.batches RESTART IDENTITY CASCADE')
    def tearDown(self):self.conn.close()

    def test_seven_real_days_insert_then_replay_without_duplicates(self):
        first=ingest_manifest(MANIFEST,conn=self.conn)
        second=ingest_manifest(MANIFEST,conn=self.conn)
        self.assertEqual([r['status'] for r in first['results']],['INSERT']*7)
        self.assertEqual([r['status'] for r in second['results']],['REPLAY']*7)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fix1_shadow_test.prices').fetchone()[0],168)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fix1_shadow_test.batches').fetchone()[0],7)
        urls=self.conn.execute('SELECT source_url FROM fix1_shadow_test.batches').fetchall()
        self.assertTrue(all('?dateShow=' in row[0] for row in urls))

    def test_bad_manifest_writes_no_batches(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)/'captures';shutil.copytree(FIX,root)
            p=root/'manifest.json';data=json.loads(p.read_text())
            data['entries'][-1]['raw_sha256']='0'*64
            p.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'hash mismatch'):ingest_manifest(p,conn=self.conn)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fix1_shadow_test.prices').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fix1_shadow_test.batches').fetchone()[0],0)

    def test_failed_second_session_rolls_back_manifest_and_allows_retry(self):
        from psycopg.errors import CheckViolation

        self.conn.execute("""
            ALTER TABLE fix1_shadow_test.prices ADD CONSTRAINT reject_second_manifest_day
            CHECK (delivery_date <> DATE '2026-10-28')
        """)
        try:
            with self.assertRaises(CheckViolation) as raised:
                ingest_manifest(MANIFEST, conn=self.conn)
            self.assertEqual(raised.exception.diag.constraint_name, 'reject_second_manifest_day')
            counts = self.conn.execute("""
                SELECT (SELECT count(*) FROM fix1_shadow_test.batches),
                       (SELECT count(*) FROM fix1_shadow_test.prices),
                       (SELECT count(*) FROM fix1_shadow_test.quarantine)
            """).fetchone()
            self.assertEqual(counts, (0, 0, 0))
        finally:
            self.conn.execute("""
                ALTER TABLE fix1_shadow_test.prices DROP CONSTRAINT reject_second_manifest_day
            """)

        retry = ingest_manifest(MANIFEST, conn=self.conn)
        self.assertEqual([row['status'] for row in retry['results']], ['INSERT'] * 7)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fix1_shadow_test.prices').fetchone(), (168,))

    def test_manifest_commits_insert_replay_and_quarantine_together(self):
        from shadow_postgres_archive import archive_batch

        first, second, *_ = load_batches(MANIFEST)
        self.assertEqual(archive_batch(self.conn, *first), 'INSERT')
        self.assertEqual(archive_batch(self.conn, *second), 'INSERT')
        period, _ = second[0].rows[0]
        # Simulate a conflicting archived value in the isolated test database.
        self.conn.execute(
            'UPDATE fix1_shadow_test.prices SET price = price + 1 WHERE period_start_utc = %s',
            (period,),
        )
        original = self.conn.execute(
            'SELECT price, batch_id FROM fix1_shadow_test.prices WHERE period_start_utc = %s',
            (period,),
        ).fetchone()

        report = ingest_manifest(MANIFEST, conn=self.conn)
        self.assertEqual([row['status'] for row in report['results']],
                         ['REPLAY', 'QUARANTINE'] + ['INSERT'] * 5)
        self.assertEqual(report['days_by_status'], {'INSERT': 5, 'QUARANTINE': 1, 'REPLAY': 1})
        self.assertEqual((report['periods'], report['inserted_periods']), (168, 120))
        replay = ingest_manifest(MANIFEST, conn=self.conn)
        self.assertEqual(replay['days_by_status'], {'QUARANTINE': 1, 'REPLAY': 6})
        self.assertEqual(replay['inserted_periods'], 0)

        self.assertEqual(self.conn.execute(
            'SELECT price, batch_id FROM fix1_shadow_test.prices WHERE period_start_utc = %s',
            (period,),
        ).fetchone(), original)
        self.assertEqual(self.conn.execute(
            'SELECT count(*) FROM fix1_shadow_test.prices',
        ).fetchone(), (168,))
        self.assertEqual(self.conn.execute("""
            SELECT batch.delivery_date
            FROM fix1_shadow_test.quarantine AS quarantine
            JOIN fix1_shadow_test.batches AS batch ON batch.id = quarantine.batch_id
        """).fetchall(), [(second[0].delivery_date,)])
