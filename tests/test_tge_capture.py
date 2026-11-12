import csv
from datetime import date, datetime
from decimal import Decimal
import gzip
from hashlib import sha256
from http.client import IncompleteRead
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch
from urllib.error import HTTPError, URLError

from shadow_ingest_manifest import MAX_HTML_BYTES, ingest_manifest
from tools.capture_tge_validation import capture, extract_reference, write_manifest


class CaptureOutputTest(unittest.TestCase):
    def test_autumn_capture_keeps_repeated_labels_separate_and_cannot_be_imported(self):
        fixture = Path(__file__).parent / 'fixtures/tge_rdn_session_2026-10-24_captured_2026-11-02.html'
        with tempfile.TemporaryDirectory() as directory, patch('tools.capture_tge_validation.urlopen') as fetch:
            output = Path(directory)
            response = fetch.return_value.__enter__.return_value
            response.status = 200
            response.geturl.return_value = 'https://tge.pl/energia-elektryczna-rdn?dateShow=24-10-2026'
            response.read.return_value = fixture.read_bytes()

            with self.assertRaisesRegex(ValueError, 'requires a 25-hour Warsaw day'):
                capture(date(2026, 10, 26), output, autumn_25h=True)
            fetch.assert_not_called()
            entry = capture(date(2026, 10, 25), output, autumn_25h=True)
            reference = (output / entry['reference_file']).read_bytes()
            rows = list(csv.DictReader(reference.decode().splitlines()))
            self.assertEqual((entry['period_basis'], entry['hours'], len(rows)), ('TGE_LABEL_ONLY', 25, 25))
            self.assertEqual(entry['reference_sha256'], sha256(reference).hexdigest())
            self.assertEqual(
                [(row['tge_hour_label'], row['price_pln_mwh']) for row in rows[1:3]],
                [('H02', '478.69'), ('H02a', '380.01')],
            )
            self.assertNotIn('period_start_utc', rows[0])
            write_manifest(output, {'entries': [entry], 'errors': []})
            with self.assertRaisesRegex(ValueError, 'label-only evidence'):
                ingest_manifest(output / 'manifest.json', conn=object())

    def test_standard_capture_exports_utc_periods_for_official_session(self):
        stem = 'tge_delivery_2026-11-09'
        fixture = Path(__file__).parent / 'fixtures' / stem
        session, = json.loads((fixture / 'manifest.json').read_text())['entries']
        raw = gzip.decompress((fixture / session['raw_file']).read_bytes())
        with tempfile.TemporaryDirectory() as directory, patch(
            'tools.capture_tge_validation.urlopen'
        ) as fetch, patch('tools.capture_tge_validation.datetime', wraps=datetime) as clock:
            output = Path(directory)
            clock.min = datetime.min
            clock.now.return_value = datetime.fromisoformat(session['retrieved_at_utc'])
            response = fetch.return_value.__enter__.return_value
            response.status = 200
            response.geturl.return_value = session['url']
            response.read.return_value = raw
            for dst_day in (date(2026, 3, 29), date(2026, 10, 25)):
                with self.assertRaisesRegex(ValueError, 'requires a 24-hour Warsaw day'):
                    capture(dst_day, output)
            fetch.assert_not_called()

            entry = capture(date(2026, 11, 9), output)
            self.assertEqual(entry['period_basis'], 'EXPLICIT_UTC')
            reference = (output / entry['reference_file']).read_bytes()
            rows = list(csv.DictReader(reference.decode().splitlines()))
            self.assertEqual(entry['reference_sha256'], sha256(reference).hexdigest())
            self.assertEqual(
                [{key: row[key] for key in ('delivery_date', 'hour', 'price_pln_mwh')} for row in rows],
                list(csv.DictReader((fixture / session['reference_file']).read_text().splitlines())),
            )
            self.assertEqual(rows[0]['period_start_utc'], '2026-11-08T23:00:00+00:00')
            self.assertEqual(rows[-1]['period_start_utc'], '2026-11-09T22:00:00+00:00')
            write_manifest(output, {'entries': [entry], 'errors': []})
            self.assertEqual(ingest_manifest(output / 'manifest.json')['periods'], 24)

    def test_capture_preserves_existing_evidence_and_rolls_back_new_html(self):
        fixture = Path(__file__).parent / 'fixtures/tge_rdn_captured_2026-11-01.html'
        raw = fixture.read_bytes()
        day = date(2026, 11, 2)
        for extension in ('html', 'csv'):
            with self.subTest(extension=extension), tempfile.TemporaryDirectory() as directory, patch(
                'tools.capture_tge_validation.urlopen'
            ) as fetch:
                output = Path(directory)
                existing = output / f'tge_delivery_{day}.{extension}'
                existing.write_bytes(b'previous evidence')
                response = fetch.return_value.__enter__.return_value
                response.status = 200
                response.geturl.return_value = 'https://tge.pl/energia-elektryczna-rdn?dateShow=01-11-2026'
                response.read.return_value = raw

                with self.assertRaises(FileExistsError):
                    capture(day, output)

                self.assertEqual(list(output.iterdir()), [existing])
                self.assertEqual(existing.read_bytes(), b'previous evidence')

    def test_capture_rejects_redirected_session_before_reading_response(self):
        with tempfile.TemporaryDirectory() as directory, patch('tools.capture_tge_validation.urlopen') as fetch:
            output = Path(directory)
            response = fetch.return_value.__enter__.return_value
            response.status = 200
            for url in (
                'https://tge.pl/energia-elektryczna-rdn',
                'https://tge.pl/energia-elektryczna-rdn?dateShow=07-11-2026',
                'https://example.test/energia-elektryczna-rdn?dateShow=08-11-2026',
            ):
                with self.subTest(url=url):
                    response.geturl.return_value = url
                    with self.assertRaisesRegex(ValueError, 'URL differs from requested session.*2026-11-09'):
                        capture(date(2026, 11, 9), output)
            response.read.assert_not_called()
            self.assertEqual(list(output.iterdir()), [])

    def test_capture_retries_503_and_read_timeout_before_saving_official_response(self):
        day = date(2026, 11, 11)
        fixture = Path(__file__).parent / f'fixtures/tge_delivery_{day}'
        entry, = json.loads((fixture / 'manifest.json').read_text())['entries']
        raw = gzip.decompress((fixture / entry['raw_file']).read_bytes())
        with tempfile.TemporaryDirectory() as directory, patch(
            'tools.capture_tge_validation.urlopen'
        ) as fetch, patch('tools.capture_tge_validation.sleep') as sleep:
            output = Path(directory)
            response = fetch.return_value.__enter__.return_value
            response.status = 200
            response.geturl.return_value = entry['url']
            response.read.side_effect = [TimeoutError('read timed out'), raw]
            fetch.side_effect = [HTTPError(entry['url'], 503, 'unavailable', {}, None),
                                 fetch.return_value, fetch.return_value]
            saved = capture(day, output)
            self.assertEqual(fetch.call_count, 3)
            self.assertEqual(sleep.call_args_list, [call(1), call(2)])
            self.assertEqual((saved['raw_sha256'], saved['reference_sha256']),
                             (entry['raw_sha256'], entry['reference_sha256']))
            self.assertEqual(len(list(output.iterdir())), 2)

    def test_capture_bounds_transient_retries_and_preserves_empty_output_on_failure(self):
        day = date(2026, 11, 11)
        url = 'https://tge.pl/energia-elektryczna-rdn?dateShow=10-11-2026'
        cases = [
            (lambda: HTTPError(url, 429, 'rate limited', {'Retry-After': '5'}, None), [call(5), call(5)]),
            (lambda: HTTPError(url, 429, 'rate limited', {'Retry-After': '60'}, None), []),
            (lambda: HTTPError(url, 429, 'rate limited',
                               {'Retry-After': 'Tue, 10 Nov 2026 12:00:07 GMT'}, None), [call(7), call(7)]),
            (lambda: HTTPError(url, 404, 'not found', {}, None), []),
            (lambda: URLError('certificate validation failed'), []),
            (lambda: URLError(TimeoutError('connect timed out')), [call(1), call(2)]),
        ]
        for make_error, waits in cases:
            with self.subTest(error=str(make_error())), tempfile.TemporaryDirectory() as directory, patch(
                'tools.capture_tge_validation.urlopen'
            ) as fetch, patch('tools.capture_tge_validation.sleep') as sleep, patch(
                'tools.capture_tge_validation.datetime', wraps=datetime
            ) as clock:
                output = Path(directory)
                clock.min = datetime.min
                clock.now.return_value = datetime.fromisoformat('2026-11-10T12:00:00+00:00')
                fetch.side_effect = [make_error() for _ in range(len(waits) + 1)]
                with self.assertRaises(URLError):
                    capture(day, output)
                self.assertEqual(fetch.call_count, len(waits) + 1)
                self.assertEqual(sleep.call_args_list, waits)
                self.assertEqual(list(output.iterdir()), [])

    def test_short_http_body_is_never_saved_even_with_a_complete_fixing_table(self):
        day = date(2026, 11, 11)
        fixture = Path(__file__).parent / f'fixtures/tge_delivery_{day}'
        entry, = json.loads((fixture / 'manifest.json').read_text())['entries']
        raw = gzip.decompress((fixture / entry['raw_file']).read_bytes())
        shortened = raw[:raw.lower().rindex(b'</html>')]
        self.assertEqual(len(extract_reference(shortened, day.isoformat())), 24)

        for recovered in (False, True):
            with self.subTest(recovered=recovered), tempfile.TemporaryDirectory() as directory, patch(
                'tools.capture_tge_validation.urlopen'
            ) as fetch, patch('tools.capture_tge_validation.sleep') as sleep:
                output = Path(directory)
                response = fetch.return_value.__enter__.return_value
                response.status = 200
                response.geturl.return_value = entry['url']
                response.length = len(raw)
                response.read.side_effect = [shortened, shortened, raw if recovered else shortened]
                if recovered:
                    saved = capture(day, output)
                    self.assertEqual((saved['raw_sha256'], saved['reference_sha256']),
                                     (entry['raw_sha256'], entry['reference_sha256']))
                    self.assertEqual((output / saved['raw_file']).read_bytes(), raw)
                else:
                    with self.assertRaises(IncompleteRead):
                        capture(day, output)
                    self.assertEqual(list(output.iterdir()), [])
                self.assertEqual(fetch.call_count, 3)
                self.assertEqual(sleep.call_args_list, [call(1), call(2)])

    def test_capture_bounds_html_before_parsing_or_writing(self):
        with tempfile.TemporaryDirectory() as directory, patch('tools.capture_tge_validation.urlopen') as fetch:
            output = Path(directory)
            response = fetch.return_value.__enter__.return_value
            response.geturl.return_value = 'https://tge.pl/energia-elektryczna-rdn?dateShow=08-11-2026'
            response.status = 206
            with self.assertRaisesRegex(ValueError, 'HTTP 206'):
                capture(date(2026, 11, 9), output)
            response.read.assert_not_called()

            response.status = 200
            response.read.return_value = b' ' * (MAX_HTML_BYTES + 1)
            with patch('tools.capture_tge_validation.extract_reference') as extract:
                with self.assertRaisesRegex(ValueError, 'exceeds 8 MiB'):
                    capture(date(2026, 11, 9), output)
                extract.assert_not_called()
            response.read.assert_called_once_with(MAX_HTML_BYTES + 1)
            self.assertEqual(list(output.iterdir()), [])

    def test_existing_capture_is_preserved(self):
        script = Path(__file__).resolve().parents[1] / "tools/capture_tge_validation.py"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            original = {
                "manifest.json": b'{"entries":[]}',
                "tge_delivery_2026-11-02.html": b"<html>original</html>",
                "tge_delivery_2026-11-02.csv": b"original reference\n",
            }
            for name, content in original.items():
                (output / name).write_bytes(content)

            result = subprocess.run(
                [sys.executable, str(script), "--start", "2026-11-02",
                 "--days", "1", "--output", str(output)],
                capture_output=True,
                text=True,
                timeout=5,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("Output path already exists", result.stderr)
            self.assertEqual(
                {path.name: path.read_bytes() for path in output.iterdir()}, original
            )

    def test_manifest_is_published_as_complete_json(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            manifest = {"entries": [{"hours": 24}], "errors": []}
            write_manifest(output, manifest)

            path = output / "manifest.json"
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), manifest)
            self.assertEqual(list(output.iterdir()), [path])

    def test_failed_serialization_leaves_no_manifest_or_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with self.assertRaises(TypeError):
                write_manifest(output, {"entries": [object()]})
            self.assertEqual(list(output.iterdir()), [])


class CaptureReferenceTest(unittest.TestCase):
    def test_distinguishes_absent_delivery_from_partial_fixing_i(self):
        from lxml import html

        fixture = Path(__file__).parent / 'fixtures/tge_rdn_captured_2026-11-01.html'
        raw = fixture.read_bytes()
        self.assertEqual(len(extract_reference(raw, '2026-11-02')), 24)
        with self.assertRaisesRegex(ValueError, 'No hourly Fixing I rows.*2026-11-09'):
            extract_reference(raw, '2026-11-09')

        document = html.fromstring(raw)
        row, = document.xpath('//table[@id="rdn"]/tbody/tr[td[1]="2026-11-02_H01"]')
        row.getparent().remove(row)
        with self.assertRaisesRegex(ValueError, '2026-11-02: expected 24, found 23'):
            extract_reference(html.tostring(document), '2026-11-02')

    def test_duplicate_fixing_group_cannot_create_reference(self):
        fixture = Path(__file__).parent / "fixtures/tge_rdn_captured_2026-11-01.html"
        source = fixture.read_text(encoding="utf-8")
        self.assertEqual(len(extract_reference(source.encode(), "2026-11-02")), 24)
        html = source.replace(">Notowania ciągłe</th>", ">Fixing I</th>", 1)
        self.assertNotEqual(html, source)
        with self.assertRaisesRegex(ValueError, "one two-column Fixing I group"):
            extract_reference(html.encode(), "2026-11-02")

    def test_missing_price_cell_cannot_create_reference(self):
        from lxml import html

        fixture = Path(__file__).parent / "fixtures/tge_rdn_captured_2026-11-01.html"
        document = html.fromstring(fixture.read_text(encoding="utf-8"))
        row, = document.xpath('//table[@id="rdn"]/tbody/tr[td[1]="2026-11-02_H01"]')
        cells = row.xpath("./td")
        cells[3].text = "2492,98"
        row.remove(cells[2])
        with self.assertRaisesRegex(ValueError, "reference row does not match header"):
            extract_reference(html.tostring(document), "2026-11-02")

    def test_capture_rejects_malformed_fixing_i_price_before_writing_evidence(self):
        from lxml import html

        day = date(2026, 11, 13)
        fixture = Path(__file__).parent / 'fixtures/tge_delivery_2026-11-13/tge_delivery_2026-11-13.html.gz'
        raw = gzip.decompress(fixture.read_bytes())
        baseline = extract_reference(raw, day.isoformat())
        self.assertEqual(len(baseline), 24)
        self.assertEqual(dict(baseline)['H08'], Decimal('711.58'))
        document = html.fromstring(raw.decode('utf-8'))
        price, = document.xpath('//table[@id="rdn"]/tbody/tr[normalize-space(td[1])="2026-11-13_H08"]/td[3]')

        price.text = '1 711,58'
        grouped = extract_reference(html.tostring(document, encoding='ascii'), day.isoformat())
        self.assertEqual(dict(grouped)['H08'], Decimal('1711.58'))

        for malformed in ('71 1,58', '711,58001'):
            with self.subTest(price=malformed), tempfile.TemporaryDirectory() as directory:
                price.text = malformed
                changed = html.tostring(document, encoding='ascii')
                with patch('tools.capture_tge_validation._fetch_session', return_value=(200, changed)):
                    with self.assertRaisesRegex(ValueError, 'Invalid reference number'):
                        capture(day, Path(directory))
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_capture_rejects_changed_fixing_i_volume_header_before_writing(self):
        from lxml import html

        day = date(2026, 11, 13)
        fixture = Path(__file__).parent / 'fixtures/tge_delivery_2026-11-13/tge_delivery_2026-11-13.html.gz'
        raw = gzip.decompress(fixture.read_bytes())
        self.assertEqual(len(extract_reference(raw, day.isoformat())), 24)
        document = html.fromstring(raw.decode('utf-8'))
        volume, = document.xpath('//table[@id="rdn"]/thead/tr[2]/th[4]')
        self.assertEqual(volume.text_content().strip(), 'Wolumen [MW]')
        volume.text = 'Wolumen [MWh]'
        changed = html.tostring(document, encoding='ascii')

        with tempfile.TemporaryDirectory() as directory:
            with patch('tools.capture_tge_validation._fetch_session', return_value=(200, changed)):
                with self.assertRaisesRegex(ValueError, 'Wrong market/unit/columns'):
                    capture(day, Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_header_span_cannot_shift_fixing_i_group(self):
        from lxml import html

        fixture = Path(__file__).parent / "fixtures/tge_rdn_captured_2026-11-01.html"
        document = html.fromstring(fixture.read_text(encoding="utf-8"))
        first, = document.xpath('//table[@id="rdn"]/thead/tr[1]/th[1]')
        first.set("colspan", "2")
        with self.assertRaisesRegex(ValueError, "Unsupported reference header spans"):
            extract_reference(html.tostring(document), "2026-11-02")
