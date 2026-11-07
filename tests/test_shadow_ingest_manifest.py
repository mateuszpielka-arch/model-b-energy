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
