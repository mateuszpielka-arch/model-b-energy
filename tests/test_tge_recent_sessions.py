import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from threading import Barrier
from time import monotonic, sleep
import unittest
from unittest.mock import patch

from shadow_archive_batch import prepare_batch, read_reference_csv
from shadow_asof import cutoff_for_day
from shadow_ingest_manifest import ingest_manifest, load_batches
from shadow_postgres_archive import archive_batch, connect_test_database, initialize
from shadow_postgres_scores import initialize_scores, score_archived_day
from shadow_scoring import expected_periods
from tge_parser_safe import parse_complete_standard_day
from tools.capture_tge_validation import capture, extract_reference


FIXTURES = Path(__file__).parent / 'fixtures/tge_history_2026-11-04_2026-11-06'
NOV_7_FIXTURES = FIXTURES.with_name('tge_delivery_2026-11-07')
NOV_8_FIXTURES = FIXTURES.with_name('tge_delivery_2026-11-08')
NOV_9_FIXTURES = FIXTURES.with_name('tge_delivery_2026-11-09')
NOV_10_FIXTURES = FIXTURES.with_name('tge_delivery_2026-11-10')
NOV_11_FIXTURES = FIXTURES.with_name('tge_delivery_2026-11-11')
NOV_12_FIXTURES = FIXTURES.with_name('tge_delivery_2026-11-12')
NOV_13_FIXTURES = FIXTURES.with_name('tge_delivery_2026-11-13')
NOV_14_FIXTURES = FIXTURES.with_name('tge_delivery_2026-11-14')
NOV_15_FIXTURES = FIXTURES.with_name('tge_delivery_2026-11-15')
NOV_16_FIXTURES = FIXTURES.with_name('tge_delivery_2026-11-16')
NOV_17_FIXTURES = FIXTURES.with_name('tge_delivery_2026-11-17')
NOV_18_FIXTURES = FIXTURES.with_name('tge_delivery_2026-11-18')
NOV_19_FIXTURES = FIXTURES.with_name('tge_delivery_2026-11-19')
DSN = os.environ.get('FIX1_SHADOW_TEST_DSN')


def load_session(entry, directory=FIXTURES):
    raw = gzip.decompress((directory / entry['raw_file']).read_bytes())
    reference = (directory / entry['reference_file']).read_bytes()
    return raw, reference, list(csv.DictReader(reference.decode('utf-8').splitlines()))


def prepare_session(entry, raw, reference_rows):
    return prepare_batch(
        raw=raw,
        day=entry['delivery_date'],
        source_url=entry['url'],
        retrieved_at=datetime.fromisoformat(entry['retrieved_at_utc']),
        reference_rows=reference_rows,
        reference_sha256=entry['reference_sha256'],
    )


class RecentOfficialSessionsTests(unittest.TestCase):
    def test_captured_fixing_i_prices_pass_parser_and_archive_admission(self):
        self.check_sessions(FIXTURES, ['2026-11-04', '2026-11-05', '2026-11-06'])

    def test_saturday_fixing_i_prices_pass_parser_and_archive_admission(self):
        self.check_sessions(NOV_7_FIXTURES, ['2026-11-07'])

    def test_sunday_fixing_i_prices_pass_parser_and_archive_admission(self):
        self.check_sessions(NOV_8_FIXTURES, ['2026-11-08'])

    def test_monday_fixing_i_prices_pass_parser_and_manifest_admission(self):
        self.check_sessions(NOV_9_FIXTURES, ['2026-11-09'])
        report = ingest_manifest(NOV_9_FIXTURES / 'manifest.json')
        self.assertEqual(report['days_by_status'], {'VALIDATED_DRY_RUN': 1})
        self.assertEqual((report['periods'], report['inserted_periods']), (24, 0))

    def test_first_standard_day_after_autumn_change_matches_official_tge(self):
        day = date(2026, 10, 26)
        directory = FIXTURES.with_name(f'tge_delivery_{day}')
        self.check_sessions(directory, [day.isoformat()])
        manifest = directory / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        _, _, rows = load_session(entry, directory)
        self.assertEqual(
            [row['period_start_utc'] for row in rows],
            [period.isoformat() for period in expected_periods(day)],
        )
        self.assertEqual(rows[0]['period_start_utc'], '2026-10-25T23:00:00+00:00')
        report = ingest_manifest(manifest)
        self.assertEqual((report['periods'], report['inserted_periods']), (24, 0))

    def test_tuesday_fixing_i_prices_and_utc_match_official_tge(self):
        day = date(2026, 11, 10)
        self.check_sessions(NOV_10_FIXTURES, [day.isoformat()])
        manifest = NOV_10_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        _, _, rows = load_session(entry, NOV_10_FIXTURES)
        self.assertEqual(
            [row['period_start_utc'] for row in rows],
            [period.isoformat() for period in expected_periods(day)],
        )
        report = ingest_manifest(manifest)
        self.assertEqual((report['periods'], report['inserted_periods']), (24, 0))

    def test_november_11_fixing_i_matches_tge_alongside_continuous_trading(self):
        day = date(2026, 11, 11)
        self.check_sessions(NOV_11_FIXTURES, [day.isoformat()])
        manifest = NOV_11_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        raw, _, rows = load_session(entry, NOV_11_FIXTURES)
        self.assertEqual([row['period_start_utc'] for row in rows],
                         [period.isoformat() for period in expected_periods(day)])
        # The H07 continuous trading price (953.00) is different.
        prices = parse_complete_standard_day(raw.decode('utf-8'), day.isoformat())
        self.assertEqual(Decimal(str(prices[7])), Decimal('950.67'))
        report = ingest_manifest(manifest)
        self.assertEqual(report['days_by_status'], {'VALIDATED_DRY_RUN': 1})
        self.assertEqual((report['periods'], report['inserted_periods']), (24, 0))

    def test_november_12_fixing_i_preserves_high_prices_and_utc_periods(self):
        day = date(2026, 11, 12)
        self.check_sessions(NOV_12_FIXTURES, [day.isoformat()])
        manifest = NOV_12_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        raw, _, rows = load_session(entry, NOV_12_FIXTURES)
        self.assertEqual([row['period_start_utc'] for row in rows],
                         [period.isoformat() for period in expected_periods(day)])
        # H10 continuous trading was 2700.70; Fixing I was 2700.83.
        prices = parse_complete_standard_day(raw.decode('utf-8'), day.isoformat())
        self.assertEqual(Decimal(str(prices[10])), Decimal('2700.83'))
        report = ingest_manifest(manifest)
        self.assertEqual(report['days_by_status'], {'VALIDATED_DRY_RUN': 1})
        self.assertEqual((report['periods'], report['inserted_periods']), (24, 0))

    def test_november_13_fixing_i_accepts_missing_volume_and_preserves_utc(self):
        day = date(2026, 11, 13)
        self.check_sessions(NOV_13_FIXTURES, [day.isoformat()])
        manifest = NOV_13_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        raw, _, rows = load_session(entry, NOV_13_FIXTURES)
        self.assertEqual([row['period_start_utc'] for row in rows],
                         [period.isoformat() for period in expected_periods(day)])
        # H08 has no Fixing I volume. Its continuous-trading price is 712.00.
        prices = parse_complete_standard_day(raw.decode('utf-8'), day.isoformat())
        self.assertEqual(Decimal(str(prices[8])), Decimal('711.58'))
        report = ingest_manifest(manifest)
        self.assertEqual(report['days_by_status'], {'VALIDATED_DRY_RUN': 1})
        self.assertEqual((report['periods'], report['inserted_periods']), (24, 0))

    def test_november_14_fixing_i_matches_reference_and_admits_utc_periods(self):
        day = date(2026, 11, 14)
        self.check_sessions(NOV_14_FIXTURES, [day.isoformat()])
        manifest = NOV_14_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        raw, _, rows = load_session(entry, NOV_14_FIXTURES)
        self.assertEqual([row['period_start_utc'] for row in rows],
                         [period.isoformat() for period in expected_periods(day)])
        prices = parse_complete_standard_day(raw.decode('utf-8'), day.isoformat())
        self.assertEqual(Decimal(str(prices[17])), Decimal('1105.60'))
        report = ingest_manifest(manifest)
        self.assertEqual(report['days_by_status'], {'VALIDATED_DRY_RUN': 1})
        self.assertEqual((report['periods'], report['inserted_periods']), (24, 0))

    def test_november_15_fixing_i_matches_official_reference(self):
        day = date(2026, 11, 15)
        self.check_sessions(NOV_15_FIXTURES, [day.isoformat()])
        manifest = NOV_15_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        _, _, rows = load_session(entry, NOV_15_FIXTURES)
        self.assertEqual([row['period_start_utc'] for row in rows],
                         [period.isoformat() for period in expected_periods(day)])
        self.assertEqual(rows[0]['price_pln_mwh'], '763.14')
        report = ingest_manifest(manifest)
        self.assertEqual(report['days_by_status'], {'VALIDATED_DRY_RUN': 1})
        self.assertEqual((report['periods'], report['inserted_periods']), (24, 0))

    def test_november_16_fixing_i_matches_official_reference(self):
        day = date(2026, 11, 16)
        self.check_sessions(NOV_16_FIXTURES, [day.isoformat()])
        manifest = NOV_16_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        _, _, rows = load_session(entry, NOV_16_FIXTURES)
        self.assertEqual([row['period_start_utc'] for row in rows],
                         [period.isoformat() for period in expected_periods(day)])
        self.assertEqual(rows[0]['price_pln_mwh'], '471.34')
        report = ingest_manifest(manifest)
        self.assertEqual((report['periods'], report['inserted_periods']), (24, 0))

    def test_november_19_fixing_i_matches_official_reference_after_unpriced_capture(self):
        day = date(2026, 11, 19)
        self.check_sessions(NOV_19_FIXTURES, [day.isoformat()])
        manifest = NOV_19_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        raw, _, rows = load_session(entry, NOV_19_FIXTURES)
        earlier = json.loads((
            FIXTURES.with_name('tge_delivery_2026-11-19_unpriced') / 'capture.json'
        ).read_text())
        self.assertEqual(entry['url'], earlier['url'])
        self.assertGreater(datetime.fromisoformat(entry['retrieved_at_utc']),
                           datetime.fromisoformat(earlier['retrieved_at_utc']))
        self.assertNotEqual(entry['raw_sha256'], earlier['raw_sha256'])
        self.assertEqual([row['period_start_utc'] for row in rows],
                         [period.isoformat() for period in expected_periods(day)])
        self.assertEqual(rows[0]['price_pln_mwh'], '623.98')
        self.assertEqual(rows[-1]['price_pln_mwh'], '765.41')
        self.assertEqual(len(parse_complete_standard_day(raw.decode('utf-8'), day.isoformat())), 24)
        report = ingest_manifest(manifest)
        self.assertEqual((report['periods'], report['inserted_periods']), (24, 0))

    def test_november_18_fixing_i_matches_official_reference(self):
        day = date(2026, 11, 18)
        self.check_sessions(NOV_18_FIXTURES, [day.isoformat()])
        manifest = NOV_18_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        _, _, rows = load_session(entry, NOV_18_FIXTURES)
        self.assertEqual([row['period_start_utc'] for row in rows],
                         [period.isoformat() for period in expected_periods(day)])
        self.assertEqual(rows[0]['price_pln_mwh'], '619.88')
        self.assertEqual(rows[-1]['price_pln_mwh'], '589.64')
        report = ingest_manifest(manifest)
        self.assertEqual((report['periods'], report['inserted_periods']), (24, 0))

    def test_november_17_fixing_i_matches_official_reference(self):
        day = date(2026, 11, 17)
        self.check_sessions(NOV_17_FIXTURES, [day.isoformat()])
        manifest = NOV_17_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        _, _, rows = load_session(entry, NOV_17_FIXTURES)
        self.assertEqual([row['period_start_utc'] for row in rows],
                         [period.isoformat() for period in expected_periods(day)])
        self.assertEqual(rows[0]['price_pln_mwh'], '738.93')
        report = ingest_manifest(manifest)
        self.assertEqual((report['periods'], report['inserted_periods']), (24, 0))

    def test_november_17_rejects_ambiguous_rdn_table_id(self):
        manifest = NOV_17_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        raw, _, rows = load_session(entry, NOV_17_FIXTURES)
        source = raw.decode('utf-8')
        day = entry['delivery_date']
        self.assertEqual(len(parse_complete_standard_day(source, day)), 24)
        table = re.search(r"""<table\b[^>]*\sid\s*=\s*(['"])rdn\1[^>]*>""", source, re.IGNORECASE)
        self.assertIsNotNone(table)
        tag = table.group(0)
        reference_rows = [(row['hour'], row['price_pln_mwh']) for row in rows]
        for duplicate in (
            '<table id="other"' + tag[len('<table'):],
            tag[:-1] + ' id="other">',
        ):
            with self.subTest(duplicate=duplicate):
                changed = source[:table.start()] + duplicate + source[table.end():]
                with self.assertRaisesRegex(ValueError, 'Ambiguous id attributes on RDN table'):
                    prepare_session(entry, changed.encode('utf-8'), reference_rows)

    def test_november_17_rejects_ambiguous_or_valueless_cell_spans(self):
        manifest = NOV_17_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        raw, _, rows = load_session(entry, NOV_17_FIXTURES)
        source = raw.decode('utf-8')
        self.assertEqual(len(parse_complete_standard_day(source, entry['delivery_date'])), 24)
        table = re.search(r"""<table\b[^>]*\sid\s*=\s*(['"])rdn\1[^>]*>""", source, re.IGNORECASE)
        self.assertIsNotNone(table)
        header = re.search(r'<th\b[^>]*>', source[table.end():], re.IGNORECASE)
        self.assertIsNotNone(header)
        start, end = (table.end() + offset for offset in header.span())
        reference_rows = [(row['hour'], row['price_pln_mwh']) for row in rows]
        for attrs, error in (
            ('colspan="2" colspan="1"', 'Ambiguous RDN cell span attributes'),
            ('rowspan="2" rowspan="1"', 'Ambiguous RDN cell span attributes'),
            ('colspan', 'Invalid RDN cell colspan'),
        ):
            with self.subTest(attrs=attrs):
                changed = source[:start] + f'<th {attrs}>' + source[end:]
                with self.assertRaisesRegex(ValueError, error):
                    prepare_session(entry, changed.encode('utf-8'), reference_rows)

    def test_november_17_rejects_hourly_row_outside_rdn_table_sections(self):
        manifest = NOV_17_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        raw, _, rows = load_session(entry, NOV_17_FIXTURES)
        source = raw.decode('utf-8')
        day = entry['delivery_date']
        self.assertEqual(len(parse_complete_standard_day(source, day)), 24)
        table = re.search(r"""<table\b[^>]*\sid\s*=\s*(['"])rdn\1[^>]*>""", source, re.IGNORECASE)
        self.assertIsNotNone(table)
        extra_row = f'<tr><td>{day}_H01</td><td>60</td><td>99,00</td></tr>'
        changed = source[:table.end()] + extra_row + source[table.end():]
        reference_rows = [(row['hour'], row['price_pln_mwh']) for row in rows]
        with self.assertRaisesRegex(ValueError, 'RDN row outside a table section'):
            prepare_session(entry, changed.encode('utf-8'), reference_rows)

    def test_november_17_rejects_hourly_prices_in_rdn_footer(self):
        entry, = json.loads((NOV_17_FIXTURES / 'manifest.json').read_text())['entries']
        raw, _, rows = load_session(entry, NOV_17_FIXTURES)
        source = raw.decode('utf-8')
        day = entry['delivery_date']
        expected = parse_complete_standard_day(source, day, exact=True)
        table = re.search(r"""<table\b[^>]*\sid\s*=\s*(['"])rdn\1[^>]*>""", source, re.IGNORECASE)
        self.assertIsNotNone(table)
        body_end = re.search(r'</tbody\s*>', source[table.end():], re.IGNORECASE)
        self.assertIsNotNone(body_end)
        position = table.end() + body_end.end()
        summary = '<tfoot><tr><td>Razem</td><td>99,00</td></tr></tfoot>'
        with_summary = source[:position] + summary + source[position:]
        self.assertEqual(parse_complete_standard_day(with_summary, day, exact=True), expected)

        reference_rows = [(row['hour'], row['price_pln_mwh']) for row in rows]
        with_hour = with_summary.replace(summary, summary.replace('Razem', f'{day}_H01'), 1)
        with self.assertRaisesRegex(ValueError, 'Hourly RDN row found in tfoot'):
            prepare_session(entry, with_hour.encode('utf-8'), reference_rows)

    def test_november_17_rejects_cells_without_a_row_in_rdn(self):
        entry, = json.loads((NOV_17_FIXTURES / 'manifest.json').read_text())['entries']
        raw, _, rows = load_session(entry, NOV_17_FIXTURES)
        source = raw.decode('utf-8')
        day = entry['delivery_date']
        self.assertEqual(len(parse_complete_standard_day(source, day)), 24)
        table = re.search(r"""<table\b[^>]*\sid\s*=\s*(['"])rdn\1[^>]*>""", source, re.IGNORECASE)
        self.assertIsNotNone(table)
        reference_rows = [(row['hour'], row['price_pln_mwh']) for row in rows]
        for section, cell in (('thead', 'th'), ('tbody', 'td'), ('tfoot', 'td')):
            with self.subTest(section=section):
                if section == 'tfoot':
                    position = source.index('</table>', table.end())
                    orphan = f'<tfoot><{cell}>{day}_H01</{cell}></tfoot>'
                else:
                    opening = re.search(rf'<{section}\b[^>]*>', source[table.end():], re.IGNORECASE)
                    self.assertIsNotNone(opening)
                    position = table.end() + opening.end()
                    orphan = f'<{cell}>{day}_H01</{cell}>'
                changed = source[:position] + orphan + source[position:]
                with self.assertRaisesRegex(ValueError, 'RDN cell outside a row'):
                    prepare_session(entry, changed.encode('utf-8'), reference_rows)

    def test_november_17_rejects_self_closing_rdn_structure(self):
        entry, = json.loads((NOV_17_FIXTURES / 'manifest.json').read_text())['entries']
        raw, _, rows = load_session(entry, NOV_17_FIXTURES)
        source = raw.decode('utf-8')
        day = entry['delivery_date']
        expected = parse_complete_standard_day(source, day, exact=True)
        table = re.search(r"""<table\b[^>]*\sid\s*=\s*(['"])rdn\1[^>]*>""", source, re.IGNORECASE)
        self.assertIsNotNone(table)
        position = source.index('</table>', table.end())
        summary = '<tfoot><tr><td>Razem<br /></td></tr></tfoot>'
        self.assertEqual(
            parse_complete_standard_day(source[:position] + summary + source[position:], day, exact=True),
            expected,
        )

        reference_rows = [(row['hour'], row['price_pln_mwh']) for row in rows]
        for tag, markup in (
            ('tfoot', '<tfoot />'),
            ('tr', '<tfoot><tr /></tfoot>'),
            ('td', '<tfoot><tr><td /></tr></tfoot>'),
            ('th', '<tfoot><tr><th /></tr></tfoot>'),
        ):
            with self.subTest(tag=tag):
                changed = source[:position] + markup + source[position:]
                with self.assertRaisesRegex(ValueError, f'Self-closing RDN table element: {tag}'):
                    prepare_session(entry, changed.encode('utf-8'), reference_rows)

    def test_november_17_rejects_script_or_style_as_a_fixing_i_price(self):
        entry, = json.loads((NOV_17_FIXTURES / 'manifest.json').read_text())['entries']
        raw, _, rows = load_session(entry, NOV_17_FIXTURES)
        source = raw.decode('utf-8')
        day = entry['delivery_date']
        expected = parse_complete_standard_day(source, day, exact=True)
        self.assertEqual(expected[1], Decimal('738.93'))
        row_start = source.index(f'{day}_H01')
        row_end = source.index('</tr>', row_start)
        price = '738,93'
        price_start = source.index(price, row_start, row_end)
        before, after = source[:price_start], source[price_start + len(price):]
        with_span = f'{before}<span>{price}</span>{after}'
        self.assertEqual(parse_complete_standard_day(with_span, day, exact=True), expected)
        self.assertEqual(extract_reference(with_span.encode('utf-8'), day), extract_reference(raw, day))

        reference_rows = [(row['hour'], row['price_pln_mwh']) for row in rows]
        for tag in ('script', 'style'):
            with self.subTest(tag=tag):
                changed = f'{before}<{tag}>{price}</{tag}>{after}'
                with self.assertRaisesRegex(ValueError, f'Unexpected {tag} in RDN cell'):
                    prepare_session(entry, changed.encode('utf-8'), reference_rows)
                with TemporaryDirectory() as tmp, patch(
                    'tools.capture_tge_validation._fetch_session',
                    return_value=(200, changed.encode('utf-8')),
                ):
                    output = Path(tmp)
                    with self.assertRaisesRegex(ValueError, 'Unexpected script or style in Fix1 reference row'):
                        capture(date.fromisoformat(day), output)
                    self.assertEqual(list(output.iterdir()), [])

    def test_november_17_later_official_capture_keeps_all_fixing_i_prices(self):
        (original, original_raw), = load_batches(NOV_17_FIXTURES / 'manifest.json')
        (later, later_raw), = load_batches(NOV_17_FIXTURES / 'manifest_1726.json')
        self.assertGreater(later.retrieved_at, original.retrieved_at)
        self.assertNotEqual(later_raw, original_raw)
        self.assertNotEqual(later.raw_sha256, original.raw_sha256)
        self.assertEqual(later.reference_sha256, original.reference_sha256)
        self.assertEqual(later.rows, original.rows)

    def test_november_17_unpriced_official_session_cannot_create_evidence(self):
        day = date(2026, 11, 17)
        directory = FIXTURES.with_name('tge_delivery_2026-11-17_unpriced')
        raw = gzip.decompress((directory / 'tge_delivery_2026-11-17_unpriced.html.gz').read_bytes())
        self.assertEqual(sha256(raw).hexdigest(),
                         '4bb601a5780ce3796f10ea7d69044b42228331c05f5be378db4e438cb42558f9')
        with self.assertRaisesRegex(ValueError, r"Invalid Fixing I price for H01: '-'"):
            parse_complete_standard_day(raw.decode('utf-8'), day.isoformat(), exact=True)
        with TemporaryDirectory() as tmp, patch(
            'tools.capture_tge_validation._fetch_session', return_value=(200, raw),
        ) as fetch:
            output = Path(tmp)
            with self.assertRaisesRegex(ValueError, 'Fixing I price unavailable for 2026-11-17_H01'):
                capture(day, output)
            fetch.assert_called_once_with(
                'https://tge.pl/energia-elektryczna-rdn?dateShow=16-11-2026', day,
            )
            self.assertEqual(list(output.iterdir()), [])

    def test_november_19_unpriced_session_cannot_create_a_price_package(self):
        from lxml import html

        day = date(2026, 11, 19)
        directory = FIXTURES.with_name('tge_delivery_2026-11-19_unpriced')
        metadata = json.loads((directory / 'capture.json').read_text())
        raw = gzip.decompress((directory / metadata['raw_file']).read_bytes())
        self.assertEqual(sha256(raw).hexdigest(), metadata['raw_sha256'])
        self.assertEqual(len(raw), metadata['bytes'])
        self.assertEqual(metadata['http_status'], 200)
        self.assertLess(datetime.fromisoformat(metadata['retrieved_at_utc']), cutoff_for_day(day))
        cells = html.fromstring(raw).xpath(
            '//table[@id="rdn"]/tbody/tr[starts-with(normalize-space(td[1]), "2026-11-19_H")]/td[3]'
        )
        self.assertEqual([cell.text_content().strip() for cell in cells], ['-'] * 24)
        self.assertEqual(metadata['missing_fixing_i_prices'], len(cells))
        with self.assertRaisesRegex(ValueError, r"Invalid Fixing I price for H01: '-'"):
            parse_complete_standard_day(raw.decode('utf-8'), day.isoformat(), exact=True)
        with TemporaryDirectory() as tmp, patch(
            'tools.capture_tge_validation._fetch_session', return_value=(200, raw),
        ) as fetch:
            output = Path(tmp)
            with self.assertRaisesRegex(ValueError, 'Fixing I price unavailable for 2026-11-19_H01'):
                capture(day, output)
            fetch.assert_called_once_with(metadata['url'], day)
            self.assertEqual(list(output.iterdir()), [])

    def check_sessions(self, directory, days):
        manifest = json.loads((directory / 'manifest.json').read_text())
        self.assertEqual(manifest['errors'], [])
        self.assertEqual([entry['delivery_date'] for entry in manifest['entries']], days)
        for entry in manifest['entries']:
            day = entry['delivery_date']
            with self.subTest(delivery_date=day):
                session = date.fromisoformat(day) - timedelta(days=1)
                self.assertEqual(entry['session_date'], session.isoformat())
                self.assertEqual(entry['http_status'], 200)
                raw, reference, rows = load_session(entry, directory)
                self.assertEqual(len(raw), entry['bytes'])
                self.assertEqual(sha256(raw).hexdigest(), entry['raw_sha256'])
                self.assertEqual(sha256(reference).hexdigest(), entry['reference_sha256'])

                self.assertEqual([row['delivery_date'] for row in rows], [day] * 24)
                expected = [(row['hour'], Decimal(row['price_pln_mwh'])) for row in rows]
                self.assertEqual(extract_reference(raw, day), expected)
                actual = parse_complete_standard_day(raw.decode('utf-8'), day)
                self.assertEqual(
                    [(f'H{hour:02d}', Decimal(str(price))) for hour, price in sorted(actual.items())],
                    expected,
                )
                batch = prepare_session(entry, raw, expected)
                self.assertEqual(len(batch.rows), 24)
                self.assertEqual(batch.raw_sha256, entry['raw_sha256'])


@unittest.skipUnless(DSN, 'Set FIX1_SHADOW_TEST_DSN for isolated PostgreSQL integration')
class RecentOfficialSessionsPostgresTests(unittest.TestCase):
    def isolated_connection(self):
        conn = connect_test_database(DSN)
        self.addCleanup(conn.close)
        initialize(conn)
        conn.execute('''TRUNCATE fix1_shadow_test.quarantine, fix1_shadow_test.prices,
                        fix1_shadow_test.batches RESTART IDENTITY CASCADE''')
        return conn

    def test_monday_manifest_is_archived_and_replayed_without_duplicate_rows(self):
        self.check_manifest_archive_and_replay(NOV_9_FIXTURES)

    def test_tuesday_manifest_with_utc_is_archived_and_replayed_without_duplicate_rows(self):
        self.check_manifest_archive_and_replay(NOV_10_FIXTURES)

    def test_november_11_manifest_is_archived_and_replayed_without_duplicate_rows(self):
        self.check_manifest_archive_and_replay(NOV_11_FIXTURES)

    def test_november_12_manifest_is_archived_and_replayed_without_duplicate_rows(self):
        self.check_manifest_archive_and_replay(NOV_12_FIXTURES)

    def test_november_13_manifest_is_archived_and_replayed_without_duplicate_rows(self):
        self.check_manifest_archive_and_replay(NOV_13_FIXTURES)

    def test_november_14_manifest_is_archived_and_replayed_without_duplicate_rows(self):
        self.check_manifest_archive_and_replay(NOV_14_FIXTURES)

    def test_november_15_manifest_is_archived_and_replayed_without_duplicate_rows(self):
        self.check_manifest_archive_and_replay(NOV_15_FIXTURES)

    def test_november_16_manifest_is_archived_and_replayed_without_duplicate_rows(self):
        self.check_manifest_archive_and_replay(NOV_16_FIXTURES)

    def test_november_19_manifest_is_archived_and_replayed_without_duplicate_rows(self):
        self.check_manifest_archive_and_replay(NOV_19_FIXTURES)

    def test_november_18_manifest_is_archived_and_replayed_without_duplicate_rows(self):
        self.check_manifest_archive_and_replay(NOV_18_FIXTURES)

    def test_november_17_manifest_is_archived_and_replayed_without_duplicate_rows(self):
        self.check_manifest_archive_and_replay(NOV_17_FIXTURES)

    def test_november_17_later_official_capture_replays_without_replacing_accepted_prices(self):
        earlier = NOV_17_FIXTURES / 'manifest.json'
        later = NOV_17_FIXTURES / 'manifest_1726.json'
        (original, _), = load_batches(earlier)
        (recaptured, _), = load_batches(later)
        with self.isolated_connection() as conn:
            self.assertEqual(ingest_manifest(earlier, conn=conn)['days_by_status'], {'INSERT': 1})
            for _ in range(2):
                report = ingest_manifest(later, conn=conn)
                self.assertEqual(report['days_by_status'], {'REPLAY': 1})
                self.assertEqual(report['inserted_periods'], 0)
                self.assertFalse(report['results'][0]['scoring_blocked_by_quarantine'])
                self.assertEqual(report['results'][0]['raw_sha256'], recaptured.raw_sha256)
                self.assertEqual(report['results'][0]['accepted_capture'], {
                    'raw_sha256': original.raw_sha256,
                    'retrieved_at_utc': original.retrieved_at.isoformat(),
                })
            self.assertEqual(conn.execute('''
                SELECT raw_sha256, retrieved_at
                FROM fix1_shadow_test.batches ORDER BY retrieved_at
            ''').fetchall(), [
                (original.raw_sha256, original.retrieved_at),
                (recaptured.raw_sha256, recaptured.retrieved_at),
            ])
            self.assertEqual(conn.execute('''
                SELECT p.period_start_utc, p.price, b.raw_sha256
                FROM fix1_shadow_test.prices AS p
                JOIN fix1_shadow_test.batches AS b ON b.id = p.batch_id
                ORDER BY p.period_start_utc
            ''').fetchall(), [(period, price, original.raw_sha256) for period, price in original.rows])

    def test_november_15_replay_quarantines_an_archived_url_for_the_wrong_session(self):
        manifest = NOV_15_FIXTURES / 'manifest.json'
        with self.isolated_connection() as conn:
            self.assertEqual(ingest_manifest(manifest, conn=conn)['inserted_periods'], 24)
            conn.execute('UPDATE fix1_shadow_test.batches SET source_url = %s',
                         ('https://tge.pl/energia-elektryczna-rdn?dateShow=13-11-2026',))
            prices_query = ('SELECT period_start_utc, price, batch_id FROM fix1_shadow_test.prices '
                            'ORDER BY period_start_utc')
            stored_prices = conn.execute(prices_query).fetchall()

            for _ in range(2):
                report = ingest_manifest(manifest, conn=conn)
                self.assertEqual(report['days_by_status'], {'QUARANTINE': 1})
                self.assertEqual(report['inserted_periods'], 0)
                self.assertEqual(conn.execute(prices_query).fetchall(), stored_prices)
            self.assertEqual(conn.execute('SELECT reason FROM fix1_shadow_test.quarantine').fetchall(),
                             [('Existing price batch has invalid Fix1 source URL',)])

    def test_november_15_replay_quarantines_a_malformed_archived_reference_hash(self):
        manifest = NOV_15_FIXTURES / 'manifest.json'
        with self.isolated_connection() as conn:
            self.assertEqual(ingest_manifest(manifest, conn=conn)['inserted_periods'], 24)
            conn.execute('UPDATE fix1_shadow_test.batches SET reference_sha256 = %s', ('g' * 64,))
            prices_query = ('SELECT period_start_utc, price, batch_id FROM fix1_shadow_test.prices '
                            'ORDER BY period_start_utc')
            stored_prices = conn.execute(prices_query).fetchall()

            for _ in range(2):
                report = ingest_manifest(manifest, conn=conn)
                self.assertEqual(report['days_by_status'], {'QUARANTINE': 1})
                self.assertEqual(report['inserted_periods'], 0)
                self.assertEqual(conn.execute(prices_query).fetchall(), stored_prices)
            self.assertEqual(
                conn.execute('SELECT reason FROM fix1_shadow_test.quarantine').fetchall(),
                [('Existing price batch has invalid Fix1 reference SHA-256',)],
            )
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.batches').fetchone(), (2,))

    def test_november_15_replay_quarantines_an_unsupported_archived_parser_version(self):
        manifest = NOV_15_FIXTURES / 'manifest.json'
        with self.isolated_connection() as conn:
            self.assertEqual(ingest_manifest(manifest, conn=conn)['inserted_periods'], 24)
            self.assertEqual(ingest_manifest(manifest, conn=conn)['days_by_status'], {'REPLAY': 1})
            conn.execute('UPDATE fix1_shadow_test.batches SET parser_version = %s', ('unknown-test',))
            prices_query = ('SELECT period_start_utc, price, batch_id FROM fix1_shadow_test.prices '
                            'ORDER BY period_start_utc')
            stored_prices = conn.execute(prices_query).fetchall()

            for _ in range(2):
                report = ingest_manifest(manifest, conn=conn)
                self.assertEqual(report['days_by_status'], {'QUARANTINE': 1})
                self.assertEqual(report['inserted_periods'], 0)
                self.assertEqual(conn.execute(prices_query).fetchall(), stored_prices)
            self.assertEqual(
                conn.execute('SELECT reason FROM fix1_shadow_test.quarantine').fetchall(),
                [('Existing price batch has unsupported Fix1 parser version',)],
            )

    def test_november_15_replay_reports_nonfinite_archived_prices(self):
        manifest = NOV_15_FIXTURES / 'manifest.json'
        day = date(2026, 11, 15)
        with self.isolated_connection() as conn:
            self.assertEqual(ingest_manifest(manifest, conn=conn)['inserted_periods'], 24)
            period, *_ = expected_periods(day)
            for price in ('NaN', 'Infinity', '-Infinity'):
                with self.subTest(price=price), conn.transaction(force_rollback=True):
                    conn.execute('UPDATE fix1_shadow_test.prices SET price = %s::numeric '
                                 'WHERE period_start_utc = %s', (price, period))
                    query = ('SELECT period_start_utc, price::text, batch_id '
                             'FROM fix1_shadow_test.prices ORDER BY period_start_utc')
                    stored = conn.execute(query).fetchall()
                    for _ in range(2):
                        report = ingest_manifest(manifest, conn=conn)
                        self.assertEqual(report['days_by_status'], {'QUARANTINE': 1})
                        self.assertEqual(report['inserted_periods'], 0)
                        self.assertEqual(conn.execute(query).fetchall(), stored)
                    self.assertEqual(
                        conn.execute('SELECT reason FROM fix1_shadow_test.quarantine').fetchall(),
                        [('Existing delivery day has nonfinite Fix1 prices',)],
                    )
            self.assertEqual(ingest_manifest(manifest, conn=conn)['days_by_status'], {'REPLAY': 1})

    def test_november_15_exact_price_survives_archive_and_scoring(self):
        manifest = NOV_15_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        raw, reference, _ = load_session(entry, NOV_15_FIXTURES)
        row_start = raw.index(b'2026-11-15_H01')
        row_end = raw.index(b'</tr>', row_start)
        row = raw[row_start:row_end]
        self.assertIn(b'763,14', row)
        price = Decimal('9007199254740992.0001')
        raw = raw[:row_start] + row.replace(b'763,14', b'9007199254740992,0001', 1) + raw[row_end:]
        reference = reference.replace(b'H01,763.14,', b'H01,9007199254740992.0001,', 1)
        day = date.fromisoformat(entry['delivery_date'])
        capture = datetime.fromisoformat(entry['retrieved_at_utc'])
        batch = prepare_batch(
            raw=raw, day=day.isoformat(), source_url=entry['url'], retrieved_at=capture,
            reference_rows=read_reference_csv(reference, day.isoformat()),
            reference_sha256=sha256(reference).hexdigest(),
        )
        cutoff = cutoff_for_day(day)
        inputs = dict(day=day, forecast=batch.rows, forecast_id='exact-fix1-test',
                      model_version='synthetic-test', forecast_status='SHADOW', issued_at=cutoff,
                      publication_ts=cutoff - timedelta(minutes=1), retrieved_at=cutoff,
                      evaluated_at=capture)

        with self.isolated_connection() as conn:
            initialize_scores(conn)
            self.assertEqual(archive_batch(conn, batch, raw), 'INSERT')
            self.assertEqual(archive_batch(conn, batch, raw), 'REPLAY')
            stored, = conn.execute('SELECT price FROM fix1_shadow_test.prices WHERE period_start_utc = %s',
                                   (batch.rows[0][0],)).fetchone()
            self.assertEqual(stored, price)

            status, score = score_archived_day(conn, **inputs)
            self.assertEqual((status, score.mae, score.bias), ('INSERT', 0, 0))
            self.assertEqual(score_archived_day(conn, **inputs, reference_csv=reference), ('REPLAY', score))

    def test_november_15_nonfinite_archived_prices_block_score_replay(self):
        manifest = NOV_15_FIXTURES / 'manifest.json'
        day = date(2026, 11, 15)
        cutoff = cutoff_for_day(day)
        periods = expected_periods(day)
        inputs = dict(day=day, forecast=[(period, Decimal('600')) for period in periods],
                      forecast_id='november-15-constant-600-test', model_version='synthetic-test',
                      forecast_status='SHADOW', issued_at=cutoff,
                      publication_ts=cutoff - timedelta(minutes=1), retrieved_at=cutoff)

        with self.isolated_connection() as conn:
            initialize_scores(conn)
            self.assertEqual(ingest_manifest(manifest, conn=conn)['inserted_periods'], 24)
            decision, score = score_archived_day(conn, **inputs)
            self.assertEqual(decision, 'INSERT')
            query = 'SELECT input_sha256, payload FROM fix1_shadow_test.scores'
            saved, = conn.execute(query).fetchall()

            for price in ('NaN', 'Infinity', '-Infinity'):
                with self.subTest(price=price), conn.transaction(force_rollback=True):
                    conn.execute('UPDATE fix1_shadow_test.prices SET price = %s::numeric '
                                 'WHERE period_start_utc = %s', (price, periods[0]))
                    with patch('shadow_postgres_scores.parse_complete_standard_day') as parser:
                        with self.assertRaisesRegex(ValueError, 'nonfinite Fix1 prices'):
                            score_archived_day(conn, **inputs)
                        parser.assert_not_called()
                    self.assertEqual(conn.execute(query).fetchall(), [saved])

            self.assertEqual(score_archived_day(conn, **inputs), ('REPLAY', score))

    def test_november_14_scoring_rejects_evaluation_before_issuance_without_a_lock(self):
        manifest = NOV_14_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        _, reference, _ = load_session(entry, NOV_14_FIXTURES)
        day = date.fromisoformat(entry['delivery_date'])
        cutoff = cutoff_for_day(day)
        captured = datetime.fromisoformat(entry['retrieved_at_utc'])
        forecast = [(period, Decimal('600')) for period in expected_periods(day)]
        inputs = dict(
            day=day, forecast_id='november-14-constant-600-test', model_version='synthetic-test',
            forecast_status='SHADOW', issued_at=cutoff,
            publication_ts=cutoff - timedelta(minutes=10), retrieved_at=cutoff,
            reference_csv=reference,
        )

        with self.isolated_connection() as conn:
            initialize_scores(conn)
            self.assertEqual(ingest_manifest(manifest, conn=conn)['inserted_periods'], 24)
            with patch('shadow_postgres_scores._lock_delivery_day') as lock:
                with self.assertRaisesRegex(ValueError, 'Evaluation time is before forecast issuance'):
                    score_archived_day(conn, **inputs, forecast=forecast,
                                       evaluated_at=cutoff - timedelta(seconds=1))
                lock.assert_not_called()
            count = 'SELECT count(*) FROM fix1_shadow_test.scores'
            self.assertEqual(conn.execute(count).fetchone(), (0,))
            decision, score = score_archived_day(conn, **inputs, forecast=forecast, evaluated_at=captured)
            self.assertEqual((decision, score.periods), ('INSERT', 24))
            self.assertEqual(
                score_archived_day(conn, **inputs, forecast=reversed(forecast), evaluated_at=captured),
                ('REPLAY', score),
            )
            self.assertEqual(conn.execute(count).fetchone(), (1,))

    def test_november_12_score_rejects_late_inputs_before_reading_prices_or_locking(self):
        day = date(2026, 11, 12)
        cutoff = cutoff_for_day(day)
        manifest = NOV_12_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        _, reference, _ = load_session(entry, NOV_12_FIXTURES)
        inputs = dict(
            day=day, forecast_id='november-12-constant-1000-test',
            model_version='synthetic-test', forecast_status='SHADOW',
            issued_at=cutoff, publication_ts=cutoff - timedelta(minutes=1),
            retrieved_at=cutoff, reference_csv=reference,
        )
        forecast = [(period, Decimal(1000)) for period in expected_periods(day)]

        def unreadable_forecast():
            self.fail('Late forecast prices were read')
            yield

        with self.isolated_connection() as conn:
            initialize_scores(conn)
            self.assertEqual(ingest_manifest(manifest, conn=conn)['inserted_periods'], 24)
            status, score = score_archived_day(conn, **inputs, forecast=iter(forecast))
            self.assertEqual(status, 'INSERT')
            expected = ('790.971666666667', '44.077655362379', '48.864095598316', '-618.717500000000')
            for metric, value in zip(('mae', 'mape_pct', 'wape_pct', 'bias'), expected, strict=True):
                self.assertEqual(getattr(score, metric).quantize(Decimal('1e-12')), Decimal(value))
            query = 'SELECT input_sha256, payload FROM fix1_shadow_test.scores'
            stored = conn.execute(query).fetchall()

            for field in ('issued_at', 'publication_ts', 'retrieved_at'):
                with self.subTest(field=field), patch('shadow_postgres_scores._lock_delivery_day') as lock:
                    late = {**inputs, field: cutoff + timedelta(seconds=1)}
                    with self.assertRaisesRegex(ValueError, 'unavailable before D-1 09:50'):
                        score_archived_day(conn, **late, forecast=unreadable_forecast())
                    lock.assert_not_called()
            self.assertEqual(conn.execute(query).fetchall(), stored)
            self.assertEqual(score_archived_day(conn, **inputs, forecast=iter(forecast)), ('REPLAY', score))

    def test_november_13_score_checks_forecast_periods_before_locking(self):
        day = date(2026, 11, 13)
        cutoff = cutoff_for_day(day)
        manifest = NOV_13_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        _, reference, _ = load_session(entry, NOV_13_FIXTURES)
        inputs = dict(
            day=day, forecast_id='november-13-complete-periods-test',
            model_version='synthetic-test', forecast_status='SHADOW',
            issued_at=cutoff, publication_ts=cutoff, retrieved_at=cutoff,
            reference_csv=reference,
        )
        forecast = [(period, Decimal(1000)) for period in expected_periods(day)]
        wrong_period = (forecast[0][0] - timedelta(hours=1), Decimal(1000))

        with self.isolated_connection() as conn:
            initialize_scores(conn)
            self.assertEqual(ingest_manifest(manifest, conn=conn)['inserted_periods'], 24)
            status, score = score_archived_day(conn, **inputs, forecast=iter(forecast))
            self.assertEqual(status, 'INSERT')
            self.assertEqual(score.periods, 24)
            query = 'SELECT input_sha256, payload FROM fix1_shadow_test.scores'
            stored = conn.execute(query).fetchall()

            for label, incomplete in (
                ('missing_period', forecast[1:]),
                ('wrong_period', [wrong_period, *forecast[1:]]),
            ):
                with self.subTest(label=label), patch('shadow_postgres_scores._lock_delivery_day') as lock:
                    with self.assertRaisesRegex(ValueError, 'Incomplete or mismatched delivery periods'):
                        score_archived_day(conn, **inputs, forecast=iter(incomplete))
                    lock.assert_not_called()
            self.assertEqual(conn.execute(query).fetchall(), stored)
            self.assertEqual(score_archived_day(conn, **inputs, forecast=reversed(forecast)), ('REPLAY', score))

    def check_manifest_archive_and_replay(self, directory):
        manifest_path = directory / 'manifest.json'
        entry, = json.loads(manifest_path.read_text())['entries']
        raw, _, rows = load_session(entry, directory)
        day = date.fromisoformat(entry['delivery_date'])
        expected = list(zip(expected_periods(day), (Decimal(row['price_pln_mwh']) for row in rows)))

        with self.isolated_connection() as conn:
            for status, inserted in (('INSERT', 24), ('REPLAY', 0)):
                report = ingest_manifest(manifest_path, conn=conn)
                self.assertEqual(report['days_by_status'], {status: 1})
                self.assertEqual((report['periods'], report['inserted_periods']), (24, inserted))
                self.assertEqual(conn.execute('''
                    SELECT period_start_utc, price FROM fix1_shadow_test.prices
                    WHERE delivery_date = %s ORDER BY period_start_utc
                ''', (day,)).fetchall(), expected)
                self.assertEqual(conn.execute('''
                    SELECT delivery_date, raw_sha256, reference_sha256, source_url, retrieved_at, raw_html
                    FROM fix1_shadow_test.batches
                ''').fetchall(), [(
                    day, entry['raw_sha256'], entry['reference_sha256'], entry['url'],
                    datetime.fromisoformat(entry['retrieved_at_utc']), raw,
                )])
                self.assertEqual(conn.execute(
                    'SELECT count(*) FROM fix1_shadow_test.quarantine'
                ).fetchone(), (0,))

    def test_total_price_loss_quarantines_an_existing_delivery_day(self):
        manifest = NOV_10_FIXTURES / 'manifest.json'
        with self.isolated_connection() as conn:
            self.assertEqual(ingest_manifest(manifest, conn=conn)['inserted_periods'], 24)
            batch_query = 'SELECT id, raw_sha256, reference_sha256, retrieved_at FROM fix1_shadow_test.batches'
            accepted, = conn.execute(batch_query).fetchall()
            conn.execute('DELETE FROM fix1_shadow_test.prices')

            quarantine_query = 'SELECT batch_id, reason, created_at FROM fix1_shadow_test.quarantine'
            self.assertEqual(conn.execute(quarantine_query).fetchall(), [])
            report = ingest_manifest(manifest, conn=conn)
            self.assertEqual(report['days_by_status'], {'QUARANTINE': 1})
            self.assertEqual(report['inserted_periods'], 0)
            quarantine, = conn.execute(quarantine_query).fetchall()
            self.assertEqual(quarantine[:2], (accepted[0], 'Archived batch exists but delivery day has no prices'))

            self.assertEqual(ingest_manifest(manifest, conn=conn)['days_by_status'], {'QUARANTINE': 1})
            self.assertEqual(conn.execute(quarantine_query).fetchall(), [quarantine])
            self.assertEqual(conn.execute(batch_query).fetchall(), [accepted])
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.prices').fetchone(), (0,))

    def test_quarantined_day_cannot_be_repopulated_with_another_capture_hash(self):
        manifest = NOV_10_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        raw, reference, _ = load_session(entry, NOV_10_FIXTURES)
        alternate_reference = reference.replace(b'\n', b'\r\n')
        alternate_hash = sha256(alternate_reference).hexdigest()
        self.assertNotEqual(alternate_hash, entry['reference_sha256'])
        alternate = prepare_session(
            {**entry, 'reference_sha256': alternate_hash}, raw,
            read_reference_csv(alternate_reference, entry['delivery_date']),
        )
        with self.isolated_connection() as conn:
            self.assertEqual(ingest_manifest(manifest, conn=conn)['inserted_periods'], 24)
            conn.execute('''
                DELETE FROM fix1_shadow_test.prices
                WHERE period_start_utc = (SELECT min(period_start_utc) FROM fix1_shadow_test.prices)
            ''')
            report = ingest_manifest(manifest, conn=conn)
            self.assertEqual(report['days_by_status'], {'QUARANTINE': 1})
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.prices').fetchone(), (23,))

            quarantine_query = 'SELECT batch_id, reason, created_at FROM fix1_shadow_test.quarantine'
            quarantine = conn.execute(quarantine_query).fetchall()
            self.assertEqual(len(quarantine), 1)
            conn.execute('DELETE FROM fix1_shadow_test.prices')
            for _ in range(2):
                report = ingest_manifest(manifest, conn=conn)
                self.assertEqual(report['days_by_status'], {'QUARANTINE': 1})
                self.assertEqual(report['inserted_periods'], 0)
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.prices').fetchone(), (0,))
            self.assertEqual(conn.execute(quarantine_query).fetchall(), quarantine)
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.batches').fetchone(), (1,))

            for _ in range(2):
                self.assertEqual(archive_batch(conn, alternate, raw), 'QUARANTINE')
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.prices').fetchone(), (0,))
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.batches').fetchone(), (2,))
            self.assertEqual(conn.execute(quarantine_query + ' ORDER BY batch_id').fetchall()[0], quarantine[0])
            self.assertEqual(conn.execute(
                """SELECT q.reason FROM fix1_shadow_test.quarantine AS q
                   JOIN fix1_shadow_test.batches AS b ON b.id = q.batch_id
                   WHERE b.reference_sha256 = %s""",
                (alternate_hash,),
            ).fetchall(), [('Delivery day is already quarantined',)])

    def test_archive_and_replay_three_sessions_with_original_evidence(self):
        manifest = json.loads((FIXTURES / 'manifest.json').read_text())
        with self.isolated_connection() as conn:
            for entry in manifest['entries']:
                with self.subTest(delivery_date=entry['delivery_date']):
                    raw, _, rows = load_session(entry)
                    reference_rows = [(row['hour'], row['price_pln_mwh']) for row in rows]
                    batch = prepare_session(entry, raw, reference_rows)
                    self.assertEqual(archive_batch(conn, batch, raw), 'INSERT')
                    self.assertEqual(archive_batch(conn, batch, raw), 'REPLAY')

                    stored = conn.execute('''
                        SELECT price.period_start_utc, price.price
                        FROM fix1_shadow_test.prices AS price
                        JOIN fix1_shadow_test.batches AS batch ON batch.id = price.batch_id
                        WHERE price.delivery_date = %s AND batch.delivery_date = %s
                        ORDER BY price.period_start_utc
                    ''', (batch.delivery_date, batch.delivery_date)).fetchall()
                    self.assertEqual(stored, list(batch.rows))

                    evidence = conn.execute('''
                        SELECT raw_sha256, reference_sha256, source_url, retrieved_at, raw_html
                        FROM fix1_shadow_test.batches WHERE delivery_date = %s
                    ''', (batch.delivery_date,)).fetchall()
                    self.assertEqual(evidence, [(
                        entry['raw_sha256'], entry['reference_sha256'], entry['url'],
                        datetime.fromisoformat(entry['retrieved_at_utc']), raw,
                    )])

            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.prices').fetchone(), (72,))
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.quarantine').fetchone(), (0,))

    def test_conflicting_revision_is_quarantined_once_with_its_own_evidence(self):
        from lxml import html

        entry = json.loads((FIXTURES / 'manifest.json').read_text())['entries'][0]
        day = entry['delivery_date']
        raw, reference, rows = load_session(entry)
        original = prepare_session(entry, raw, [(row['hour'], row['price_pln_mwh']) for row in rows])

        # Simulate a revision by changing only the first Fixing I price.
        page = html.fromstring(raw)
        cells = page.xpath(
            '//table[@id="rdn"]/tbody/tr[normalize-space(td[1])=$period]/td[3]',
            period=f'{day}_H01',
        )
        self.assertEqual(len(cells), 1)
        revised_price = Decimal(rows[0]['price_pln_mwh']) + Decimal('0.01')
        cells[0].text = format(revised_price, 'f').replace('.', ',')
        revised_raw = html.tostring(page, encoding='utf-8')
        old_row = f"{day},H01,{rows[0]['price_pln_mwh']}\n".encode()
        self.assertEqual(reference.count(old_row), 1)
        revised_reference = reference.replace(old_row, f'{day},H01,{revised_price}\n'.encode())
        revised_rows = list(csv.DictReader(revised_reference.decode('utf-8').splitlines()))
        revised = prepare_session(
            {**entry, 'reference_sha256': sha256(revised_reference).hexdigest()},
            revised_raw,
            [(row['hour'], row['price_pln_mwh']) for row in revised_rows],
        )
        self.assertEqual(revised.rows, ((original.rows[0][0], revised_price), *original.rows[1:]))

        with self.isolated_connection() as conn:
            self.assertEqual(archive_batch(conn, original, raw), 'INSERT')
            initialize_scores(conn)
            cutoff = cutoff_for_day(original.delivery_date)
            inputs = dict(
                day=original.delivery_date, forecast=original.rows,
                forecast_id='before-conflicting-revision', model_version='synthetic-test',
                forecast_status='SHADOW', issued_at=cutoff,
                publication_ts=cutoff - timedelta(minutes=10), retrieved_at=cutoff,
                reference_csv=reference,
            )
            self.assertEqual(score_archived_day(conn, **inputs)[0], 'INSERT')
            score_query = 'SELECT input_sha256, payload FROM fix1_shadow_test.scores'
            original_score = conn.execute(score_query).fetchall()
            with connect_test_database(DSN) as scorer, ThreadPoolExecutor(max_workers=1) as pool:
                scorer.execute("SET statement_timeout = '10s'")
                scorer_pid = scorer.info.backend_pid
                with conn.transaction():
                    self.assertEqual(archive_batch(conn, revised, revised_raw), 'QUARANTINE')
                    pending = pool.submit(score_archived_day, scorer, **inputs)
                    deadline = monotonic() + 5
                    while conn.info.backend_pid not in conn.execute(
                        'SELECT pg_blocking_pids(%s)', (scorer_pid,),
                    ).fetchone()[0]:
                        self.assertFalse(pending.done(), 'Scoring completed before quarantine committed')
                        self.assertLess(monotonic(), deadline, 'Scoring did not wait for the archive lock')
                        sleep(0.02)
                with self.assertRaisesRegex(ValueError, 'Quarantined delivery day'):
                    pending.result(timeout=5)
            self.assertEqual(conn.execute(score_query).fetchall(), original_score)
            self.assertEqual(archive_batch(conn, revised, revised_raw), 'QUARANTINE')
            self.assertEqual(archive_batch(conn, original, raw), 'QUARANTINE')

            stored = conn.execute('''
                SELECT p.period_start_utc, p.price, b.raw_sha256
                FROM fix1_shadow_test.prices AS p
                JOIN fix1_shadow_test.batches AS b ON b.id = p.batch_id
                ORDER BY p.period_start_utc
            ''').fetchall()
            self.assertEqual(stored, [(period, price, original.raw_sha256) for period, price in original.rows])

            quarantined = conn.execute('''
                SELECT b.raw_sha256, b.reference_sha256, b.raw_html
                FROM fix1_shadow_test.quarantine AS q
                JOIN fix1_shadow_test.batches AS b ON b.id = q.batch_id
            ''').fetchall()
            self.assertEqual(quarantined, [(revised.raw_sha256, revised.reference_sha256, revised_raw)])
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.batches').fetchone(), (2,))

            report = ingest_manifest(FIXTURES / 'manifest.json', conn=conn)
            blocked, *new_days = report['results']
            self.assertEqual((blocked['day'], blocked['status'], blocked['scoring_blocked_by_quarantine']),
                             (day, 'QUARANTINE', True))
            self.assertEqual([(row['status'], row['scoring_blocked_by_quarantine']) for row in new_days],
                             [('INSERT', False), ('INSERT', False)])
            self.assertEqual(report['inserted_periods'], 48)
            self.assertEqual(conn.execute(score_query).fetchall(), original_score)

    def test_score_november_16_against_official_prices_at_capture_boundary(self):
        manifest = NOV_16_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        _, reference, _ = load_session(entry, NOV_16_FIXTURES)
        day = date.fromisoformat(entry['delivery_date'])
        cutoff = cutoff_for_day(day)
        captured = datetime.fromisoformat(entry['retrieved_at_utc'])
        inputs = dict(
            day=day, forecast=[(period, Decimal('650')) for period in expected_periods(day)],
            forecast_id='november-16-constant-650-test', model_version='synthetic-test',
            forecast_status='SHADOW', issued_at=cutoff, publication_ts=cutoff - timedelta(minutes=1),
            retrieved_at=cutoff, reference_csv=reference,
        )
        # Calculated independently with exact fractions from the fixture CSV.
        expected = {
            'mae': Decimal('139.040000000000'),
            'mape_pct': Decimal('21.155217276784'),
            'wape_pct': Decimal('20.915242424166'),
            'bias': Decimal('-14.778333333333'),
        }
        count = 'SELECT count(*) FROM fix1_shadow_test.scores'
        with self.isolated_connection() as conn:
            initialize_scores(conn)
            self.assertEqual(ingest_manifest(manifest, conn=conn)['inserted_periods'], 24)
            with self.assertRaisesRegex(ValueError, 'not yet available'):
                score_archived_day(conn, **inputs, evaluated_at=captured - timedelta(microseconds=1))
            self.assertEqual(conn.execute(count).fetchone(), (0,))

            status, score = score_archived_day(conn, **inputs, evaluated_at=captured)
            self.assertEqual((status, score.periods, score.mape_periods), ('INSERT', 24, 24))
            for metric, value in expected.items():
                self.assertEqual(getattr(score, metric).quantize(Decimal('1e-12')), value)
            self.assertEqual(score_archived_day(conn, **inputs, evaluated_at=captured), ('REPLAY', score))
            self.assertEqual(conn.execute(count).fetchone(), (1,))

    def test_score_november_18_matches_fraction_metrics_at_capture_boundary(self):
        manifest = NOV_18_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        _, reference, _ = load_session(entry, NOV_18_FIXTURES)
        day = date.fromisoformat(entry['delivery_date'])
        cutoff = cutoff_for_day(day)
        captured = datetime.fromisoformat(entry['retrieved_at_utc'])
        inputs = dict(
            day=day, forecast=[(period, Decimal('650')) for period in expected_periods(day)],
            forecast_id='november-18-constant-650-test', model_version='synthetic-test',
            forecast_status='SHADOW', issued_at=cutoff, publication_ts=cutoff,
            retrieved_at=cutoff,
        )
        # Independent exact-fraction calculation from the official reference CSV.
        expected = {
            'mae': Decimal('109.965000000000'),
            'mape_pct': Decimal('14.515065147030'),
            'wape_pct': Decimal('15.519084553008'),
            'bias': Decimal('-58.579166666667'),
        }
        query = 'SELECT input_sha256, payload FROM fix1_shadow_test.scores'
        with self.isolated_connection() as conn:
            initialize_scores(conn)
            self.assertEqual(ingest_manifest(manifest, conn=conn)['inserted_periods'], 24)
            for supplied_reference in (reference, None):
                with self.subTest(reference_csv=supplied_reference is not None):
                    with self.assertRaisesRegex(ValueError, 'not yet available'):
                        score_archived_day(
                            conn, **inputs, reference_csv=supplied_reference,
                            evaluated_at=captured - timedelta(microseconds=1),
                        )
                    self.assertEqual(conn.execute(query).fetchall(), [])
            status, score = score_archived_day(
                conn, **inputs, reference_csv=reference, evaluated_at=captured,
            )
            self.assertEqual((status, score.periods, score.mape_periods), ('INSERT', 24, 24))
            for metric, value in expected.items():
                self.assertEqual(getattr(score, metric).quantize(Decimal('1e-12')), value)
            saved = conn.execute(query).fetchall()
            for supplied_reference in (reference, None):
                self.assertEqual(score_archived_day(
                    conn, **inputs, reference_csv=supplied_reference, evaluated_at=captured,
                ), ('REPLAY', score))
            self.assertEqual(conn.execute(query).fetchall(), saved)

    def test_score_november_19_ramp_forecast_matches_fraction_metrics(self):
        manifest = NOV_19_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        _, reference, _ = load_session(entry, NOV_19_FIXTURES)
        day = date.fromisoformat(entry['delivery_date'])
        cutoff = cutoff_for_day(day)
        captured = datetime.fromisoformat(entry['retrieved_at_utc'])
        inputs = dict(
            day=day, forecast=[
                (period, Decimal(600 + 10 * index))
                for index, period in enumerate(expected_periods(day))
            ],
            forecast_id='november-19-synthetic-ramp', model_version='synthetic-test',
            forecast_status='SHADOW', issued_at=cutoff, publication_ts=cutoff,
            retrieved_at=cutoff, evaluated_at=captured, reference_csv=reference,
        )
        # Independent exact-fraction calculation from the official reference CSV.
        expected = {
            'mae': Decimal('209.479583333333'),
            'mape_pct': Decimal('19.840471346131'),
            'wape_pct': Decimal('23.830031894971'),
            'bias': Decimal('-164.057083333333'),
        }
        query = 'SELECT input_sha256, payload FROM fix1_shadow_test.scores'
        with self.isolated_connection() as conn:
            initialize_scores(conn)
            self.assertEqual(ingest_manifest(manifest, conn=conn)['inserted_periods'], 24)
            for field in ('publication_ts', 'retrieved_at'):
                with self.subTest(field=field):
                    with self.assertRaisesRegex(ValueError, f'D-1 09:50: {field}(self):
        manifest = NOV_17_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        _, reference, _ = load_session(entry, NOV_17_FIXTURES)
        day = date.fromisoformat(entry['delivery_date'])
        cutoff = cutoff_for_day(day)
        captured = datetime.fromisoformat(entry['retrieved_at_utc'])
        inputs = dict(
            day=day, forecast=[(period, Decimal('650')) for period in expected_periods(day)],
            forecast_id='november-17-cutoff-test', model_version='synthetic-test',
            forecast_status='SHADOW', issued_at=cutoff, publication_ts=cutoff,
            retrieved_at=cutoff, evaluated_at=captured, reference_csv=reference,
        )
        late = cutoff + timedelta(microseconds=1)
        # Exact-fraction oracle from the reference CSV and the fixed 650 PLN/MWh input.
        expected = {
            'mae': '507.959583333333',
            'mape_pct': '36.736480386777',
            'wape_pct': '43.866780036580',
            'bias': '-507.959583333333',
        }
        with self.isolated_connection() as conn:
            initialize_scores(conn)
            self.assertEqual(ingest_manifest(manifest, conn=conn)['inserted_periods'], 24)
            for field in ('publication_ts', 'retrieved_at'):
                with self.subTest(field=field):
                    with self.assertRaisesRegex(ValueError, f'D-1 09:50: {field}$'):
                        score_archived_day(conn, **{**inputs, field: late})
            count = 'SELECT count(*) FROM fix1_shadow_test.scores'
            self.assertEqual(conn.execute(count).fetchone(), (0,))
            status, score = score_archived_day(conn, **inputs)
            self.assertEqual((status, score.periods), ('INSERT', 24))
            for metric, value in expected.items():
                self.assertEqual(getattr(score, metric).quantize(Decimal('1e-12')), Decimal(value))
            self.assertEqual(score_archived_day(conn, **inputs), ('REPLAY', score))
            self.assertEqual(conn.execute(count).fetchone(), (1,))

    def test_score_november_17_uses_accepted_capture_in_either_import_order(self):
        earlier = NOV_17_FIXTURES / 'manifest.json'
        later = NOV_17_FIXTURES / 'manifest_1726.json'
        (first_capture, _), = load_batches(earlier)
        (later_capture, _), = load_batches(later)
        day = first_capture.delivery_date
        cutoff = cutoff_for_day(day)
        inputs = dict(
            day=day, forecast=[(period, Decimal('650')) for period in expected_periods(day)],
            forecast_id='november-17-capture-order-test', model_version='synthetic-test',
            forecast_status='SHADOW', issued_at=cutoff,
            publication_ts=cutoff, retrieved_at=cutoff,
        )
        query = 'SELECT input_sha256, payload FROM fix1_shadow_test.scores'
        for first, second, accepted in (
            (earlier, later, first_capture),
            (later, earlier, later_capture),
        ):
            with self.subTest(first=first.name), self.isolated_connection() as conn:
                initialize_scores(conn)
                self.assertEqual(ingest_manifest(first, conn=conn)['days_by_status'], {'INSERT': 1})
                self.assertEqual(ingest_manifest(second, conn=conn)['days_by_status'], {'REPLAY': 1})
                with self.assertRaisesRegex(ValueError, 'not yet available'):
                    score_archived_day(
                        conn, **inputs, evaluated_at=accepted.retrieved_at - timedelta(microseconds=1),
                    )
                self.assertEqual(conn.execute(query).fetchall(), [])

                status, score = score_archived_day(conn, **inputs, evaluated_at=accepted.retrieved_at)
                self.assertEqual((status, score.periods), ('INSERT', 24))
                saved, = conn.execute(query).fetchall()
                self.assertEqual(saved[1]['actual_captured_at_utc'], accepted.retrieved_at.isoformat())
                self.assertEqual(score_archived_day(
                    conn, **inputs, evaluated_at=later_capture.retrieved_at,
                ), ('REPLAY', score))
                self.assertEqual(conn.execute(query).fetchall(), [saved])

    def test_score_november_17_binds_the_recorded_fix1_capture_time(self):
        manifest = NOV_17_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        day = date.fromisoformat(entry['delivery_date'])
        cutoff = cutoff_for_day(day)
        captured = datetime.fromisoformat(entry['retrieved_at_utc'])
        inputs = dict(
            day=day, forecast=[(period, Decimal('650')) for period in expected_periods(day)],
            forecast_id='november-17-constant-650-test', model_version='synthetic-test',
            forecast_status='SHADOW', issued_at=cutoff, publication_ts=cutoff - timedelta(minutes=1),
            retrieved_at=cutoff, evaluated_at=captured + timedelta(seconds=1),
        )
        query = 'SELECT input_sha256, payload FROM fix1_shadow_test.scores'
        with self.isolated_connection() as conn:
            initialize_scores(conn)
            self.assertEqual(ingest_manifest(manifest, conn=conn)['inserted_periods'], 24)
            status, score = score_archived_day(conn, **inputs)
            self.assertEqual((status, score.periods), ('INSERT', 24))
            saved = conn.execute(query).fetchone()
            self.assertEqual(saved[1]['actual_captured_at_utc'], captured.isoformat())
            with conn.transaction(force_rollback=True):
                conn.execute(
                    "UPDATE fix1_shadow_test.batches SET retrieved_at = retrieved_at + INTERVAL '1 second'"
                )
                with self.assertRaisesRegex(ValueError, 'input fingerprint differs'):
                    score_archived_day(conn, **inputs)
                self.assertEqual(conn.execute(query).fetchone(), saved)
            self.assertEqual(score_archived_day(conn, **inputs), ('REPLAY', score))

    def test_score_monday_manifest_at_capture_time_against_csv_metrics(self):
        manifest_path = NOV_9_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest_path.read_text())['entries']
        day = date.fromisoformat(entry['delivery_date'])
        cutoff = cutoff_for_day(day)
        captured = datetime.fromisoformat(entry['retrieved_at_utc'])
        reference = (NOV_9_FIXTURES / entry['reference_file']).read_bytes()
        inputs = dict(
            day=day,
            forecast=[(period, Decimal('1500')) for period in expected_periods(day)],
            forecast_id='monday-constant-1500-test',
            model_version='synthetic-test',
            forecast_status='SHADOW',
            issued_at=cutoff,
            publication_ts=cutoff - timedelta(minutes=10),
            retrieved_at=cutoff,
            reference_csv=reference,
        )
        # Exact-fraction oracle calculated directly from the reference CSV.
        expected = {
            'mae': '638.960833333333',
            'mape_pct': '47.705932238053',
            'wape_pct': '41.476845813844',
            'bias': '-40.524166666667',
        }
        with self.isolated_connection() as conn:
            initialize_scores(conn)
            self.assertEqual(ingest_manifest(manifest_path, conn=conn)['inserted_periods'], 24)
            with self.assertRaisesRegex(ValueError, 'not yet available'):
                score_archived_day(conn, **inputs, evaluated_at=captured - timedelta(seconds=1))
            count = 'SELECT count(*) FROM fix1_shadow_test.scores'
            self.assertEqual(conn.execute(count).fetchone(), (0,))
            decision, score = score_archived_day(conn, **inputs, evaluated_at=captured)
            self.assertEqual(decision, 'INSERT')
            self.assertEqual((score.periods, score.mape_periods), (24, 24))
            for metric, value in expected.items():
                with self.subTest(metric=metric):
                    self.assertEqual(getattr(score, metric).quantize(Decimal('1e-12')), Decimal(value))
            self.assertEqual(
                score_archived_day(conn, **inputs, evaluated_at=captured), ('REPLAY', score),
            )
            self.assertEqual(conn.execute(count).fetchone(), (1,))

    def test_score_tuesday_manifest_rejects_shifted_utc_even_with_matching_hash(self):
        day = date(2026, 11, 10)
        manifest = NOV_10_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        _, reference, rows = load_session(entry, NOV_10_FIXTURES)
        captured = datetime.fromisoformat(entry['retrieved_at_utc'])
        cutoff = cutoff_for_day(day)
        inputs = dict(
            day=day,
            forecast=[
                (period, Decimal(600 + 25 * index))
                for index, period in enumerate(expected_periods(day))
            ],
            forecast_id='tuesday-synthetic-ramp-test',
            model_version='synthetic-test',
            forecast_status='SHADOW',
            issued_at=cutoff,
            publication_ts=cutoff - timedelta(minutes=10),
            retrieved_at=cutoff,
            evaluated_at=captured,
        )
        # Independent exact-fraction calculation against the official CSV prices.
        expected = {
            'mae': '1057.189583333333',
            'mape_pct': '48.998826332563',
            'wape_pct': '55.239844629875',
            'bias': '-1026.317083333333',
        }
        original = rows[0]['period_start_utc'].encode()
        shifted = rows[1]['period_start_utc'].encode()
        changed_reference = reference.replace(original, shifted, 1)
        self.assertNotEqual(changed_reference, reference)

        with self.isolated_connection() as conn:
            initialize_scores(conn)
            self.assertEqual(ingest_manifest(manifest, conn=conn)['inserted_periods'], 24)
            decision, score = score_archived_day(conn, **inputs, reference_csv=reference)
            self.assertEqual(decision, 'INSERT')
            for metric, value in expected.items():
                with self.subTest(metric=metric):
                    self.assertEqual(getattr(score, metric).quantize(Decimal('1e-12')), Decimal(value))
            query = 'SELECT input_sha256, payload FROM fix1_shadow_test.scores'
            stored = conn.execute(query).fetchall()
            self.assertEqual(len(stored), 1)

            # Update only the archived hash in the disposable test DB.
            conn.execute(
                'UPDATE fix1_shadow_test.batches SET reference_sha256 = %s',
                (sha256(changed_reference).hexdigest(),),
            )
            with self.assertRaisesRegex(ValueError, 'Fix1 reference UTC period does not match H01'):
                score_archived_day(conn, **inputs, reference_csv=changed_reference)
            self.assertEqual(conn.execute(query).fetchall(), stored)

    def test_score_fixed_forecast_against_archived_official_prices(self):
        entry = json.loads((FIXTURES / 'manifest.json').read_text())['entries'][0]
        raw, reference, rows = load_session(entry)
        batch = prepare_session(entry, raw, [(row['hour'], row['price_pln_mwh']) for row in rows])
        # Synthetic forecast and timestamps. Only actual prices come from TGE.
        cutoff = datetime(2026, 11, 3, 8, 50, tzinfo=timezone.utc)
        inputs = dict(
            day=batch.delivery_date,
            forecast=[(period, Decimal('1000')) for period, _ in batch.rows],
            forecast_id='constant-1000-official-actual-test',
            model_version='synthetic-test',
            forecast_status='SHADOW',
            issued_at=cutoff,
            publication_ts=cutoff - timedelta(minutes=10),
            retrieved_at=cutoff,
            reference_csv=reference,
        )
        # Independently calculated from the reference CSV using exact fractions.
        expected = {
            'mae': Decimal('368.701666666667'),
            'mape_pct': Decimal('22.311982605236'),
            'wape_pct': Decimal('27.201755896023'),
            'bias': Decimal('-355.433333333333'),
        }
        precision = Decimal('0.000000000001')
        with self.isolated_connection() as conn:
            initialize_scores(conn)
            self.assertEqual(archive_batch(conn, batch, raw), 'INSERT')
            decision, score = score_archived_day(conn, **inputs)
            self.assertEqual(decision, 'INSERT')
            self.assertEqual(score_archived_day(conn, **inputs), ('REPLAY', score))

            query = '''
                SELECT s.payload, b.raw_sha256
                FROM fix1_shadow_test.scores AS s
                JOIN fix1_shadow_test.batches AS b ON b.id = s.batch_id
            '''
            stored = conn.execute(query).fetchall()
            self.assertEqual(len(stored), 1)
            payload, raw_hash = stored[0]
            self.assertEqual(raw_hash, entry['raw_sha256'])
            self.assertEqual((payload['periods'], payload['mape_periods']), (24, 24))
            for metric, value in expected.items():
                with self.subTest(metric=metric):
                    self.assertEqual(Decimal(payload[metric]).quantize(precision), value)

            after_cutoff = cutoff + timedelta(seconds=1)
            with self.assertRaisesRegex(ValueError, 'unavailable before D-1 09:50'):
                score_archived_day(conn, **{**inputs, 'issued_at': after_cutoff, 'retrieved_at': after_cutoff})
            self.assertEqual(conn.execute(query).fetchall(), stored)

            changed_reference = reference.replace(b'H01,983.00', b'H01,983.01', 1)
            self.assertNotEqual(changed_reference, reference)
            with self.assertRaisesRegex(ValueError, 'Archived reference CSV hash mismatch'):
                score_archived_day(conn, **{**inputs, 'reference_csv': changed_reference})

            conn.execute(
                'UPDATE fix1_shadow_test.batches SET reference_sha256 = %s',
                (sha256(changed_reference).hexdigest(),),
            )
            with self.assertRaisesRegex(ValueError, 'Official reference mismatch.*H01'):
                score_archived_day(conn, **{**inputs, 'reference_csv': changed_reference})
            self.assertEqual(conn.execute(query).fetchall(), stored)

    def test_concurrent_conflicting_captures_preserve_one_complete_day(self):
        from lxml import html

        (original, raw), = load_batches(NOV_17_FIXTURES / 'manifest.json')
        page = html.fromstring(raw)
        cells = page.xpath(
            '//table[@id="rdn"]/tbody/tr[normalize-space(td[1])=$period]/td[3]',
            period='2026-11-17_H01',
        )
        self.assertEqual(len(cells), 1)
        # A synthetic conflicting revision of the official capture.
        revised_price = original.rows[0][1] + Decimal('0.01')
        cells[0].text = format(revised_price, 'f').replace('.', ',')
        revised_raw = html.tostring(page, encoding='utf-8')
        reference_rows = extract_reference(revised_raw, '2026-11-17')
        reference = '\n'.join(f'{label},{price}' for label, price in reference_rows).encode()
        revised = prepare_batch(
            raw=revised_raw, day='2026-11-17', source_url=original.source_url,
            retrieved_at=original.retrieved_at,
            reference_rows=reference_rows, reference_sha256=sha256(reference).hexdigest(),
        )
        candidates = ((original, raw), (revised, revised_raw))
        start = Barrier(2, timeout=10)

        def import_candidate(candidate):
            batch, evidence = candidate
            with connect_test_database(DSN) as worker:
                start.wait()
                return archive_batch(worker, batch, evidence)

        with self.isolated_connection() as conn:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(import_candidate, candidates))
            self.assertCountEqual(results, ['INSERT', 'QUARANTINE'])
            winner = candidates[results.index('INSERT')][0]
            loser = candidates[results.index('QUARANTINE')][0]
            query = '''
                SELECT p.period_start_utc, p.price, b.raw_sha256
                FROM fix1_shadow_test.prices p
                JOIN fix1_shadow_test.batches b ON b.id = p.batch_id
                ORDER BY p.period_start_utc
            '''
            stored = conn.execute(query).fetchall()
            self.assertEqual(stored, [(period, price, winner.raw_sha256) for period, price in winner.rows])
            self.assertEqual(conn.execute(
                'SELECT raw_sha256, raw_html FROM fix1_shadow_test.batches ORDER BY raw_sha256'
            ).fetchall(), sorted((batch.raw_sha256, evidence) for batch, evidence in candidates))
            quarantine = conn.execute('''
                SELECT b.raw_sha256, q.reason FROM fix1_shadow_test.quarantine q
                JOIN fix1_shadow_test.batches b ON b.id = q.batch_id
            ''').fetchall()
            self.assertEqual(quarantine, [(loser.raw_sha256, 'Existing prices differ or day is partial')])
            for batch, evidence in candidates:
                self.assertEqual(archive_batch(conn, batch, evidence), 'QUARANTINE')
            self.assertEqual(conn.execute(query).fetchall(), stored)
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.batches').fetchone(), (2,))
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.quarantine').fetchone(), (1,))

    def test_concurrent_saturday_imports_write_one_official_batch(self):
        manifest = json.loads((NOV_7_FIXTURES / 'manifest.json').read_text())
        entry, = manifest['entries']
        raw, _, rows = load_session(entry, NOV_7_FIXTURES)
        reference_rows = [(row['hour'], row['price_pln_mwh']) for row in rows]
        batch = prepare_session(entry, raw, reference_rows)
        start = Barrier(4, timeout=10)

        def import_batch(_):
            with connect_test_database(DSN) as worker_conn:
                start.wait()
                return archive_batch(worker_conn, batch, raw)

        with self.isolated_connection() as conn:
            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertCountEqual(
                    list(pool.map(import_batch, range(4))),
                    ['INSERT', 'REPLAY', 'REPLAY', 'REPLAY'],
                )
            evidence = conn.execute('''
                SELECT id, delivery_date, raw_sha256, reference_sha256, raw_html
                FROM fix1_shadow_test.batches
            ''').fetchall()
            self.assertEqual(len(evidence), 1)
            batch_id, *stored_evidence = evidence[0]
            self.assertEqual(stored_evidence, [
                batch.delivery_date, entry['raw_sha256'], entry['reference_sha256'], raw,
            ])
            prices = conn.execute('''
                SELECT period_start_utc, price, batch_id FROM fix1_shadow_test.prices
                ORDER BY period_start_utc
            ''').fetchall()
            self.assertEqual(prices, [(period, price, batch_id) for period, price in batch.rows])
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.quarantine').fetchone(), (0,))
):
                        score_archived_day(conn, **{**inputs, field: cutoff + timedelta(microseconds=1)})
            with self.assertRaisesRegex(ValueError, 'not yet available'):
                score_archived_day(conn, **{**inputs, 'evaluated_at': captured - timedelta(microseconds=1)})
            self.assertEqual(conn.execute(query).fetchall(), [])
            status, score = score_archived_day(conn, **inputs)
            self.assertEqual((status, score.periods, score.mape_periods), ('INSERT', 24, 24))
            for metric, value in expected.items():
                self.assertEqual(getattr(score, metric).quantize(Decimal('1e-12')), value)
            saved = conn.execute(query).fetchall()
            self.assertEqual(score_archived_day(conn, **inputs), ('REPLAY', score))
            self.assertEqual(
                score_archived_day(conn, **{**inputs, 'reference_csv': None}), ('REPLAY', score),
            )
            self.assertEqual(conn.execute(query).fetchall(), saved)

    def test_score_november_17_rejects_late_forecast_inputs_and_matches_reference_metrics(self):
        manifest = NOV_17_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        _, reference, _ = load_session(entry, NOV_17_FIXTURES)
        day = date.fromisoformat(entry['delivery_date'])
        cutoff = cutoff_for_day(day)
        captured = datetime.fromisoformat(entry['retrieved_at_utc'])
        inputs = dict(
            day=day, forecast=[(period, Decimal('650')) for period in expected_periods(day)],
            forecast_id='november-17-cutoff-test', model_version='synthetic-test',
            forecast_status='SHADOW', issued_at=cutoff, publication_ts=cutoff,
            retrieved_at=cutoff, evaluated_at=captured, reference_csv=reference,
        )
        late = cutoff + timedelta(microseconds=1)
        # Exact-fraction oracle from the reference CSV and the fixed 650 PLN/MWh input.
        expected = {
            'mae': '507.959583333333',
            'mape_pct': '36.736480386777',
            'wape_pct': '43.866780036580',
            'bias': '-507.959583333333',
        }
        with self.isolated_connection() as conn:
            initialize_scores(conn)
            self.assertEqual(ingest_manifest(manifest, conn=conn)['inserted_periods'], 24)
            for field in ('publication_ts', 'retrieved_at'):
                with self.subTest(field=field):
                    with self.assertRaisesRegex(ValueError, f'D-1 09:50: {field}$'):
                        score_archived_day(conn, **{**inputs, field: late})
            count = 'SELECT count(*) FROM fix1_shadow_test.scores'
            self.assertEqual(conn.execute(count).fetchone(), (0,))
            status, score = score_archived_day(conn, **inputs)
            self.assertEqual((status, score.periods), ('INSERT', 24))
            for metric, value in expected.items():
                self.assertEqual(getattr(score, metric).quantize(Decimal('1e-12')), Decimal(value))
            self.assertEqual(score_archived_day(conn, **inputs), ('REPLAY', score))
            self.assertEqual(conn.execute(count).fetchone(), (1,))

    def test_score_november_17_uses_accepted_capture_in_either_import_order(self):
        earlier = NOV_17_FIXTURES / 'manifest.json'
        later = NOV_17_FIXTURES / 'manifest_1726.json'
        (first_capture, _), = load_batches(earlier)
        (later_capture, _), = load_batches(later)
        day = first_capture.delivery_date
        cutoff = cutoff_for_day(day)
        inputs = dict(
            day=day, forecast=[(period, Decimal('650')) for period in expected_periods(day)],
            forecast_id='november-17-capture-order-test', model_version='synthetic-test',
            forecast_status='SHADOW', issued_at=cutoff,
            publication_ts=cutoff, retrieved_at=cutoff,
        )
        query = 'SELECT input_sha256, payload FROM fix1_shadow_test.scores'
        for first, second, accepted in (
            (earlier, later, first_capture),
            (later, earlier, later_capture),
        ):
            with self.subTest(first=first.name), self.isolated_connection() as conn:
                initialize_scores(conn)
                self.assertEqual(ingest_manifest(first, conn=conn)['days_by_status'], {'INSERT': 1})
                self.assertEqual(ingest_manifest(second, conn=conn)['days_by_status'], {'REPLAY': 1})
                with self.assertRaisesRegex(ValueError, 'not yet available'):
                    score_archived_day(
                        conn, **inputs, evaluated_at=accepted.retrieved_at - timedelta(microseconds=1),
                    )
                self.assertEqual(conn.execute(query).fetchall(), [])

                status, score = score_archived_day(conn, **inputs, evaluated_at=accepted.retrieved_at)
                self.assertEqual((status, score.periods), ('INSERT', 24))
                saved, = conn.execute(query).fetchall()
                self.assertEqual(saved[1]['actual_captured_at_utc'], accepted.retrieved_at.isoformat())
                self.assertEqual(score_archived_day(
                    conn, **inputs, evaluated_at=later_capture.retrieved_at,
                ), ('REPLAY', score))
                self.assertEqual(conn.execute(query).fetchall(), [saved])

    def test_score_november_17_binds_the_recorded_fix1_capture_time(self):
        manifest = NOV_17_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        day = date.fromisoformat(entry['delivery_date'])
        cutoff = cutoff_for_day(day)
        captured = datetime.fromisoformat(entry['retrieved_at_utc'])
        inputs = dict(
            day=day, forecast=[(period, Decimal('650')) for period in expected_periods(day)],
            forecast_id='november-17-constant-650-test', model_version='synthetic-test',
            forecast_status='SHADOW', issued_at=cutoff, publication_ts=cutoff - timedelta(minutes=1),
            retrieved_at=cutoff, evaluated_at=captured + timedelta(seconds=1),
        )
        query = 'SELECT input_sha256, payload FROM fix1_shadow_test.scores'
        with self.isolated_connection() as conn:
            initialize_scores(conn)
            self.assertEqual(ingest_manifest(manifest, conn=conn)['inserted_periods'], 24)
            status, score = score_archived_day(conn, **inputs)
            self.assertEqual((status, score.periods), ('INSERT', 24))
            saved = conn.execute(query).fetchone()
            self.assertEqual(saved[1]['actual_captured_at_utc'], captured.isoformat())
            with conn.transaction(force_rollback=True):
                conn.execute(
                    "UPDATE fix1_shadow_test.batches SET retrieved_at = retrieved_at + INTERVAL '1 second'"
                )
                with self.assertRaisesRegex(ValueError, 'input fingerprint differs'):
                    score_archived_day(conn, **inputs)
                self.assertEqual(conn.execute(query).fetchone(), saved)
            self.assertEqual(score_archived_day(conn, **inputs), ('REPLAY', score))

    def test_score_monday_manifest_at_capture_time_against_csv_metrics(self):
        manifest_path = NOV_9_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest_path.read_text())['entries']
        day = date.fromisoformat(entry['delivery_date'])
        cutoff = cutoff_for_day(day)
        captured = datetime.fromisoformat(entry['retrieved_at_utc'])
        reference = (NOV_9_FIXTURES / entry['reference_file']).read_bytes()
        inputs = dict(
            day=day,
            forecast=[(period, Decimal('1500')) for period in expected_periods(day)],
            forecast_id='monday-constant-1500-test',
            model_version='synthetic-test',
            forecast_status='SHADOW',
            issued_at=cutoff,
            publication_ts=cutoff - timedelta(minutes=10),
            retrieved_at=cutoff,
            reference_csv=reference,
        )
        # Exact-fraction oracle calculated directly from the reference CSV.
        expected = {
            'mae': '638.960833333333',
            'mape_pct': '47.705932238053',
            'wape_pct': '41.476845813844',
            'bias': '-40.524166666667',
        }
        with self.isolated_connection() as conn:
            initialize_scores(conn)
            self.assertEqual(ingest_manifest(manifest_path, conn=conn)['inserted_periods'], 24)
            with self.assertRaisesRegex(ValueError, 'not yet available'):
                score_archived_day(conn, **inputs, evaluated_at=captured - timedelta(seconds=1))
            count = 'SELECT count(*) FROM fix1_shadow_test.scores'
            self.assertEqual(conn.execute(count).fetchone(), (0,))
            decision, score = score_archived_day(conn, **inputs, evaluated_at=captured)
            self.assertEqual(decision, 'INSERT')
            self.assertEqual((score.periods, score.mape_periods), (24, 24))
            for metric, value in expected.items():
                with self.subTest(metric=metric):
                    self.assertEqual(getattr(score, metric).quantize(Decimal('1e-12')), Decimal(value))
            self.assertEqual(
                score_archived_day(conn, **inputs, evaluated_at=captured), ('REPLAY', score),
            )
            self.assertEqual(conn.execute(count).fetchone(), (1,))

    def test_score_tuesday_manifest_rejects_shifted_utc_even_with_matching_hash(self):
        day = date(2026, 11, 10)
        manifest = NOV_10_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        _, reference, rows = load_session(entry, NOV_10_FIXTURES)
        captured = datetime.fromisoformat(entry['retrieved_at_utc'])
        cutoff = cutoff_for_day(day)
        inputs = dict(
            day=day,
            forecast=[
                (period, Decimal(600 + 25 * index))
                for index, period in enumerate(expected_periods(day))
            ],
            forecast_id='tuesday-synthetic-ramp-test',
            model_version='synthetic-test',
            forecast_status='SHADOW',
            issued_at=cutoff,
            publication_ts=cutoff - timedelta(minutes=10),
            retrieved_at=cutoff,
            evaluated_at=captured,
        )
        # Independent exact-fraction calculation against the official CSV prices.
        expected = {
            'mae': '1057.189583333333',
            'mape_pct': '48.998826332563',
            'wape_pct': '55.239844629875',
            'bias': '-1026.317083333333',
        }
        original = rows[0]['period_start_utc'].encode()
        shifted = rows[1]['period_start_utc'].encode()
        changed_reference = reference.replace(original, shifted, 1)
        self.assertNotEqual(changed_reference, reference)

        with self.isolated_connection() as conn:
            initialize_scores(conn)
            self.assertEqual(ingest_manifest(manifest, conn=conn)['inserted_periods'], 24)
            decision, score = score_archived_day(conn, **inputs, reference_csv=reference)
            self.assertEqual(decision, 'INSERT')
            for metric, value in expected.items():
                with self.subTest(metric=metric):
                    self.assertEqual(getattr(score, metric).quantize(Decimal('1e-12')), Decimal(value))
            query = 'SELECT input_sha256, payload FROM fix1_shadow_test.scores'
            stored = conn.execute(query).fetchall()
            self.assertEqual(len(stored), 1)

            # Update only the archived hash in the disposable test DB.
            conn.execute(
                'UPDATE fix1_shadow_test.batches SET reference_sha256 = %s',
                (sha256(changed_reference).hexdigest(),),
            )
            with self.assertRaisesRegex(ValueError, 'Fix1 reference UTC period does not match H01'):
                score_archived_day(conn, **inputs, reference_csv=changed_reference)
            self.assertEqual(conn.execute(query).fetchall(), stored)

    def test_score_fixed_forecast_against_archived_official_prices(self):
        entry = json.loads((FIXTURES / 'manifest.json').read_text())['entries'][0]
        raw, reference, rows = load_session(entry)
        batch = prepare_session(entry, raw, [(row['hour'], row['price_pln_mwh']) for row in rows])
        # Synthetic forecast and timestamps. Only actual prices come from TGE.
        cutoff = datetime(2026, 11, 3, 8, 50, tzinfo=timezone.utc)
        inputs = dict(
            day=batch.delivery_date,
            forecast=[(period, Decimal('1000')) for period, _ in batch.rows],
            forecast_id='constant-1000-official-actual-test',
            model_version='synthetic-test',
            forecast_status='SHADOW',
            issued_at=cutoff,
            publication_ts=cutoff - timedelta(minutes=10),
            retrieved_at=cutoff,
            reference_csv=reference,
        )
        # Independently calculated from the reference CSV using exact fractions.
        expected = {
            'mae': Decimal('368.701666666667'),
            'mape_pct': Decimal('22.311982605236'),
            'wape_pct': Decimal('27.201755896023'),
            'bias': Decimal('-355.433333333333'),
        }
        precision = Decimal('0.000000000001')
        with self.isolated_connection() as conn:
            initialize_scores(conn)
            self.assertEqual(archive_batch(conn, batch, raw), 'INSERT')
            decision, score = score_archived_day(conn, **inputs)
            self.assertEqual(decision, 'INSERT')
            self.assertEqual(score_archived_day(conn, **inputs), ('REPLAY', score))

            query = '''
                SELECT s.payload, b.raw_sha256
                FROM fix1_shadow_test.scores AS s
                JOIN fix1_shadow_test.batches AS b ON b.id = s.batch_id
            '''
            stored = conn.execute(query).fetchall()
            self.assertEqual(len(stored), 1)
            payload, raw_hash = stored[0]
            self.assertEqual(raw_hash, entry['raw_sha256'])
            self.assertEqual((payload['periods'], payload['mape_periods']), (24, 24))
            for metric, value in expected.items():
                with self.subTest(metric=metric):
                    self.assertEqual(Decimal(payload[metric]).quantize(precision), value)

            after_cutoff = cutoff + timedelta(seconds=1)
            with self.assertRaisesRegex(ValueError, 'unavailable before D-1 09:50'):
                score_archived_day(conn, **{**inputs, 'issued_at': after_cutoff, 'retrieved_at': after_cutoff})
            self.assertEqual(conn.execute(query).fetchall(), stored)

            changed_reference = reference.replace(b'H01,983.00', b'H01,983.01', 1)
            self.assertNotEqual(changed_reference, reference)
            with self.assertRaisesRegex(ValueError, 'Archived reference CSV hash mismatch'):
                score_archived_day(conn, **{**inputs, 'reference_csv': changed_reference})

            conn.execute(
                'UPDATE fix1_shadow_test.batches SET reference_sha256 = %s',
                (sha256(changed_reference).hexdigest(),),
            )
            with self.assertRaisesRegex(ValueError, 'Official reference mismatch.*H01'):
                score_archived_day(conn, **{**inputs, 'reference_csv': changed_reference})
            self.assertEqual(conn.execute(query).fetchall(), stored)

    def test_concurrent_conflicting_captures_preserve_one_complete_day(self):
        from lxml import html

        (original, raw), = load_batches(NOV_17_FIXTURES / 'manifest.json')
        page = html.fromstring(raw)
        cells = page.xpath(
            '//table[@id="rdn"]/tbody/tr[normalize-space(td[1])=$period]/td[3]',
            period='2026-11-17_H01',
        )
        self.assertEqual(len(cells), 1)
        # A synthetic conflicting revision of the official capture.
        revised_price = original.rows[0][1] + Decimal('0.01')
        cells[0].text = format(revised_price, 'f').replace('.', ',')
        revised_raw = html.tostring(page, encoding='utf-8')
        reference_rows = extract_reference(revised_raw, '2026-11-17')
        reference = '\n'.join(f'{label},{price}' for label, price in reference_rows).encode()
        revised = prepare_batch(
            raw=revised_raw, day='2026-11-17', source_url=original.source_url,
            retrieved_at=original.retrieved_at,
            reference_rows=reference_rows, reference_sha256=sha256(reference).hexdigest(),
        )
        candidates = ((original, raw), (revised, revised_raw))
        start = Barrier(2, timeout=10)

        def import_candidate(candidate):
            batch, evidence = candidate
            with connect_test_database(DSN) as worker:
                start.wait()
                return archive_batch(worker, batch, evidence)

        with self.isolated_connection() as conn:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(import_candidate, candidates))
            self.assertCountEqual(results, ['INSERT', 'QUARANTINE'])
            winner = candidates[results.index('INSERT')][0]
            loser = candidates[results.index('QUARANTINE')][0]
            query = '''
                SELECT p.period_start_utc, p.price, b.raw_sha256
                FROM fix1_shadow_test.prices p
                JOIN fix1_shadow_test.batches b ON b.id = p.batch_id
                ORDER BY p.period_start_utc
            '''
            stored = conn.execute(query).fetchall()
            self.assertEqual(stored, [(period, price, winner.raw_sha256) for period, price in winner.rows])
            self.assertEqual(conn.execute(
                'SELECT raw_sha256, raw_html FROM fix1_shadow_test.batches ORDER BY raw_sha256'
            ).fetchall(), sorted((batch.raw_sha256, evidence) for batch, evidence in candidates))
            quarantine = conn.execute('''
                SELECT b.raw_sha256, q.reason FROM fix1_shadow_test.quarantine q
                JOIN fix1_shadow_test.batches b ON b.id = q.batch_id
            ''').fetchall()
            self.assertEqual(quarantine, [(loser.raw_sha256, 'Existing prices differ or day is partial')])
            for batch, evidence in candidates:
                self.assertEqual(archive_batch(conn, batch, evidence), 'QUARANTINE')
            self.assertEqual(conn.execute(query).fetchall(), stored)
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.batches').fetchone(), (2,))
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.quarantine').fetchone(), (1,))

    def test_concurrent_saturday_imports_write_one_official_batch(self):
        manifest = json.loads((NOV_7_FIXTURES / 'manifest.json').read_text())
        entry, = manifest['entries']
        raw, _, rows = load_session(entry, NOV_7_FIXTURES)
        reference_rows = [(row['hour'], row['price_pln_mwh']) for row in rows]
        batch = prepare_session(entry, raw, reference_rows)
        start = Barrier(4, timeout=10)

        def import_batch(_):
            with connect_test_database(DSN) as worker_conn:
                start.wait()
                return archive_batch(worker_conn, batch, raw)

        with self.isolated_connection() as conn:
            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertCountEqual(
                    list(pool.map(import_batch, range(4))),
                    ['INSERT', 'REPLAY', 'REPLAY', 'REPLAY'],
                )
            evidence = conn.execute('''
                SELECT id, delivery_date, raw_sha256, reference_sha256, raw_html
                FROM fix1_shadow_test.batches
            ''').fetchall()
            self.assertEqual(len(evidence), 1)
            batch_id, *stored_evidence = evidence[0]
            self.assertEqual(stored_evidence, [
                batch.delivery_date, entry['raw_sha256'], entry['reference_sha256'], raw,
            ])
            prices = conn.execute('''
                SELECT period_start_utc, price, batch_id FROM fix1_shadow_test.prices
                ORDER BY period_start_utc
            ''').fetchall()
            self.assertEqual(prices, [(period, price, batch_id) for period, price in batch.rows])
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.quarantine').fetchone(), (0,))
