import csv
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from shadow_history_audit import audit_rows,read_csv
from shadow_scoring import expected_periods

class HistoryAuditTests(unittest.TestCase):
    def rows(self,day='2026-11-03'):
        return [dict(delivery_date=day,period_start_utc=p.isoformat(),fix1='100',jwcd_mw='10000',
            actual_verified='true',actual_version='v1',jwcd_snapshot_id=day,actual_available_at=day+'T00:00:00Z',publication_ts=day+'T00:00:00Z',retrieved_at_utc=day+'T00:00:00Z',
            retrieval_completed_at=day+'T00:00:00Z') for p in expected_periods(date.fromisoformat(day))]

    def test_real_reference_days_are_not_training_ready(self):
        f=Path(__file__).parent/'fixtures'; rows=[]
        for day in ('2026-11-02','2026-11-03'): rows+=read_csv(f/f'tge_fix1_{day}_reference.csv')
        out=audit_rows(rows)
        self.assertEqual(out['model_ready_days'],0)
        self.assertEqual([d['valid_fix1_rows'] for d in out['days']],[24,24])
        self.assertTrue(all(d['complete_periods'] for d in out['days']))
        self.assertTrue(all('MISSING_OR_INVALID_JWCD' in d['issues'] for d in out['days']))

    def test_delivery_dates_without_both_day_boundaries_do_not_abort_audit(self):
        fixture = Path(__file__).parent / 'fixtures/tge_delivery_2026-11-13/tge_delivery_2026-11-13.csv'
        official = read_csv(fixture)
        baseline = audit_rows(official)
        for invalid in ('0001-01-01', '9999-12-31'):
            with self.subTest(delivery_date=invalid):
                report = audit_rows([{**official[0], 'delivery_date': invalid}, *official])
                self.assertEqual(report, {**baseline, 'invalid_date_rows': 1})
                self.assertEqual(report['days'][0]['valid_fix1_rows'], 24)

    def test_late_availability_rejected(self):
        out=audit_rows(self.rows());self.assertEqual(out['model_ready_days'],0)
        self.assertIn('AVAILABILITY_NOT_PROVEN',out['days'][0]['issues'])

    def test_valid_synthetic_day_and_no_mutation(self):
        rows=self.rows()
        for r in rows:
            r.update(publication_ts='2026-11-02T06:00:00Z',retrieved_at_utc='2026-11-02T07:00:00Z',retrieval_completed_at='2026-11-02T07:01:00Z')
        before=deepcopy(rows);self.assertEqual(audit_rows(rows)['model_ready_days'],1);self.assertEqual(rows,before)

    def test_actual_provenance_requires_explicit_verification_and_text_version(self):
        fixture = Path(__file__).parent / 'fixtures/tge_delivery_2026-11-11/tge_delivery_2026-11-11.csv'
        official = read_csv(fixture)
        rows = self.rows('2026-11-11')
        for row, price in zip(rows, official, strict=True):
            row.update(fix1=price['price_pln_mwh'], publication_ts='2026-11-10T08:00:00Z',
                       retrieved_at_utc='2026-11-10T08:30:00Z',
                       retrieval_completed_at='2026-11-10T08:50:00Z')
        rows[0]['actual_verified'] = True
        rows[1]['actual_verified'] = 'True'
        self.assertTrue(audit_rows(rows)['days'][0]['model_ready'])

        for field, value, issue in (
            ('actual_verified', 1, 'ACTUAL_NOT_VERIFIED'),
            ('actual_verified', 1.0, 'ACTUAL_NOT_VERIFIED'),
            ('actual_verified', '1', 'ACTUAL_NOT_VERIFIED'),
            ('actual_version', 42, 'MISSING_ACTUAL_VERSION'),
            ('actual_version', True, 'MISSING_ACTUAL_VERSION'),
        ):
            with self.subTest(field=field, value=value):
                changed = [{**row, field: value} for row in rows]
                day, = audit_rows(changed)['days']
                self.assertTrue(day['complete_pairs'])
                self.assertEqual(day['valid_fix1_rows'], 24)
                self.assertFalse(day['model_ready'])
                self.assertEqual(day['issues'], [issue])

    def test_duplicate_hour_not_complete(self):
        rows=self.rows(); rows[-1]=deepcopy(rows[0])
        self.assertFalse(audit_rows(rows)['days'][0]['complete_periods'])

    def test_dst_requires_utc_and_counts_25(self):
        rows=self.rows('2026-10-25');self.assertTrue(audit_rows(rows)['days'][0]['complete_periods'])
        for i,r in enumerate(rows): r.pop('period_start_utc');r['hour']=str(i+1)
        self.assertFalse(audit_rows(rows)['days'][0]['complete_periods'])

    def test_invalid_dates_and_nan(self):
        rows=self.rows();rows[0]['fix1']='NaN';rows.append({'delivery_date':'bad'})
        out=audit_rows(rows);self.assertEqual(out['invalid_date_rows'],1)
        self.assertEqual(out['days'][0]['valid_fix1_rows'],23)

    def test_comparison_coverage_requires_consecutive_ready_days(self):
        first = date(2026, 6, 1)
        rows = []
        for offset in (*range(30), 31):
            day = first + timedelta(days=offset)
            stamp = (day - timedelta(days=1)).isoformat() + 'T06:00:00Z'
            for row in self.rows(day.isoformat()):
                row.update(publication_ts=stamp, retrieved_at_utc=stamp,
                           retrieval_completed_at=stamp)
                rows.append(row)
        out = audit_rows(reversed(rows))
        self.assertEqual(out['model_ready_days'], 31)
        self.assertEqual(out['longest_model_ready_run'], 30)
        self.assertEqual(out['missing_days_for_first_comparison'],
                         {'30D': 1, '60D': 31, '90D': 61})

    def test_distinguishes_missing_invalid_and_late_availability(self):
        rows = self.rows()
        for row in rows:
            row.update(publication_ts='2026-11-02T08:00:00Z',
                       retrieved_at_utc='2026-11-02T08:45:00Z',
                       retrieval_completed_at='2026-11-02T08:50:00Z')
        del rows[0]['retrieval_completed_at']
        rows[1]['publication_ts'] = '2026-11-02T08:00:00'
        rows[2]['retrieval_completed_at'] = '2026-11-02T08:50:01Z'
        rows[3]['publication_ts'] = '2026-11-02T08:51:00Z'
        day, = audit_rows(rows)['days']
        self.assertEqual(day['availability_proven_rows'], 20)
        self.assertEqual(day['availability_missing_timestamp_rows'], 1)
        self.assertEqual(day['missing_timestamp_rows_by_field'], {
            'publication_ts': 0, 'retrieved_at': 0,
            'retrieval_completed_at': 1, 'actual_available_at': 0,
        })
        self.assertEqual(day['availability_invalid_timestamp_rows'], 2)
        self.assertEqual(day['availability_after_cutoff_rows'], 1)
        self.assertFalse(day['model_ready'])

    def test_counts_late_jwcd_fields_against_warsaw_cutoff(self):
        fixture = Path(__file__).parent / 'fixtures/tge_delivery_2026-11-13/tge_delivery_2026-11-13.csv'
        official = read_csv(fixture)
        records = []
        deliveries = (
            ('2026-03-29', '2026-03-28T08:50:00Z', '2026-03-28T08:50:01Z', 23),
            ('2026-10-25', '2026-10-24T07:50:00Z', '2026-10-24T07:50:01Z', 25),
            ('2026-11-13', '2026-11-12T08:50:00Z', '2026-11-12T08:50:01Z', 24),
        )
        for delivery, cutoff, late, count in deliveries:
            rows = self.rows(delivery)
            self.assertEqual(len(rows), count)
            for i, row in enumerate(rows):
                if delivery == '2026-11-13':
                    row['fix1'] = official[i]['price_pln_mwh']
                row.update(publication_ts=cutoff, retrieved_at_utc=cutoff,
                           retrieval_completed_at=cutoff)
            rows[0]['retrieval_completed_at'] = late
            rows[1].update(retrieved_at_utc=late, retrieval_completed_at=late)
            rows[2].update(publication_ts=late, retrieved_at_utc=late, retrieval_completed_at=late)
            rows[3]['publication_ts'] = late
            records.extend(rows)

        report = audit_rows(records)
        for day, (delivery, _, _, count) in zip(report['days'], deliveries, strict=True):
            self.assertEqual(day['day'], delivery)
            self.assertTrue(day['complete_pairs'])
            self.assertFalse(day['model_ready'])
            self.assertEqual(day['availability_proven_rows'], count - 4)
            self.assertEqual(day['availability_invalid_timestamp_rows'], 1)
            self.assertEqual(day['availability_after_cutoff_rows'], 3)
            self.assertEqual(day['after_cutoff_rows_by_field'], {
                'publication_ts': 1, 'retrieved_at': 2, 'retrieval_completed_at': 3,
            })
        self.assertEqual(report['after_cutoff_rows_by_field'], {
            'publication_ts': 3, 'retrieved_at': 6, 'retrieval_completed_at': 9,
        })

    def test_reports_overlapping_jwcd_chronology_errors_on_real_fix1_prices(self):
        fixture = Path(__file__).parent / 'fixtures/tge_delivery_2026-11-14/tge_delivery_2026-11-14.csv'
        rows = self.rows('2026-11-14')
        cutoff = '2026-11-13T08:50:00Z'
        after = '2026-11-13T08:50:01Z'
        for row, price in zip(rows, read_csv(fixture), strict=True):
            row.update(fix1=price['price_pln_mwh'], period_start_utc=price['period_start_utc'],
                       publication_ts=cutoff, retrieved_at_utc=cutoff, retrieval_completed_at=cutoff)
        self.assertTrue(audit_rows(rows)['days'][0]['model_ready'])
        rows[0]['retrieved_at_utc'] = after
        rows[1]['publication_ts'] = after
        rows[2].update(retrieved_at_utc=after, publication_ts=after)

        report = audit_rows(rows)
        day, = report['days']
        self.assertTrue(day['complete_pairs'])
        self.assertEqual(day['valid_fix1_rows'], 24)
        self.assertEqual(day['availability_invalid_timestamp_rows'], 3)
        self.assertEqual(day['availability_after_cutoff_rows'], 0)
        self.assertFalse(day['model_ready'])
        for summary in (day, report):
            self.assertEqual(summary['retrieval_start_after_completion_rows'], 2)
            self.assertEqual(summary['publication_after_completion_rows'], 2)

    def test_accepts_canonical_retrieved_at_and_rejects_conflicting_legacy_alias(self):
        rows = self.rows()
        for row in rows:
            del row['retrieved_at_utc']
            row.update(publication_ts='2026-11-02T08:00:00Z',
                       retrieved_at='2026-11-02T09:45:00+01:00',
                       retrieval_completed_at='2026-11-02T08:50:00Z')
        rows[0]['retrieved_at_utc'] = '2026-11-02T08:45:00Z'
        self.assertTrue(audit_rows(rows)['days'][0]['model_ready'])
        rows[0]['retrieved_at_utc'] = '2026-11-02T08:44:00Z'
        day, = audit_rows(rows)['days']
        self.assertEqual(day['availability_invalid_timestamp_rows'], 1)
        self.assertFalse(day['model_ready'])


    def test_falsey_retrieval_timestamp_cannot_be_hidden_by_an_alias(self):
        for delivery, cutoff in (
            ('2026-03-29', '2026-03-28T08:50:00Z'),
            ('2026-10-25', '2026-10-24T07:50:00Z'),
        ):
            rows = self.rows(delivery)
            for row in rows:
                row.update(publication_ts=cutoff, retrieved_at=cutoff, retrieved_at_utc=cutoff,
                           retrieval_completed_at=cutoff)
            baseline, = audit_rows(rows)['days']
            self.assertTrue(baseline['model_ready'])
            for field in ('retrieved_at', 'retrieved_at_utc'):
                for value in (False, 0):
                    with self.subTest(delivery=delivery, field=field, value=value):
                        changed = [{**rows[0], field: value}, *rows[1:]]
                        day, = audit_rows(changed)['days']
                        self.assertTrue(day['complete_pairs'])
                        self.assertFalse(day['model_ready'])
                        self.assertEqual(day['availability_invalid_timestamp_rows'], 1)
                        self.assertEqual(day['availability_proven_rows'], len(rows) - 1)

    def test_fix1_requires_timestamp_strictly_after_issuance(self):
        rows = self.rows()
        for row in rows:
            row.update(publication_ts='2026-11-02T08:00:00Z',
                       retrieved_at_utc='2026-11-02T08:45:00Z',
                       retrieval_completed_at='2026-11-02T08:50:00Z')
        self.assertTrue(audit_rows(rows)['days'][0]['model_ready'])
        rows[0]['actual_available_at'] = '2026-11-02T09:50:00+01:00'
        del rows[1]['actual_available_at']
        rows[2]['actual_available_at'] = '2026-11-02T09:51:00'
        rows[3]['actual_available_at'] = '2026-11-02T08:49:59Z'
        rows[4]['actual_available_at'] = '2026-11-02T08:50:01Z'
        report = audit_rows(rows)
        day, = report['days']
        self.assertEqual(day['actual_after_issuance_rows'], 20)
        self.assertEqual(day['missing_timestamp_rows_by_field']['actual_available_at'], 1)
        self.assertEqual(day['actual_availability_invalid_timestamp_rows'], 1)
        self.assertEqual(day['actual_availability_at_or_before_cutoff_rows'], 2)
        self.assertEqual(report['actual_availability_invalid_timestamp_rows'], 1)
        self.assertEqual(report['actual_availability_at_or_before_cutoff_rows'], 2)
        self.assertIn('ACTUAL_AVAILABILITY_NOT_PROVEN', day['issues'])
        self.assertFalse(day['model_ready'])


    def test_missing_or_mixed_fix1_versions_block_coverage(self):
        rows = self.rows()
        for row in rows:
            row.update(publication_ts='2026-11-02T08:00:00Z',
                       retrieved_at_utc='2026-11-02T08:45:00Z',
                       retrieval_completed_at='2026-11-02T08:50:00Z')
        self.assertTrue(audit_rows(rows)['days'][0]['model_ready'])
        rows[0]['actual_version'] = 'v2'
        del rows[1]['actual_version']
        day, = audit_rows(rows)['days']
        self.assertFalse(day['model_ready'])
        self.assertEqual(day['issues'], [
            'CONFLICTING_ACTUAL_VERSIONS', 'MISSING_ACTUAL_VERSION',
        ])

    def test_first_candidate_requires_training_prices_available_by_target_cutoff(self):
        first = date(2026, 6, 1)
        rows = []
        for offset in range(32):
            day = first + timedelta(days=offset)
            prior = (day - timedelta(days=1)).isoformat()
            for row in self.rows(day.isoformat()):
                stamp = prior + 'T06:00:00Z'
                row.update(publication_ts=stamp, retrieved_at_utc=stamp,
                           retrieval_completed_at=stamp,
                           actual_available_at=prior + 'T11:00:00Z')
                rows.append(row)
        result = audit_rows(rows)
        self.assertEqual(result['first_comparison_candidate']['30D'], '2026-07-01')
        self.assertEqual(result['comparison_candidates'],
                         {'30D': ['2026-07-01', '2026-07-02'], '60D': [], '90D': []})
        rows[0]['actual_available_at'] = '2026-06-30T07:50:00Z'
        self.assertEqual(audit_rows(rows)['comparison_blocked_by_late_fix1']['30D'], {})
        rows[0]['actual_available_at'] = '2026-06-30T07:50:01Z'
        result = audit_rows(rows)
        self.assertEqual(result['longest_model_ready_run'], 32)
        self.assertEqual(result['first_comparison_candidate']['30D'], '2026-07-02')
        self.assertEqual(result['comparison_candidates']['30D'], ['2026-07-02'])
        self.assertEqual(result['comparison_blocked_by_late_fix1'], {
            '30D': {'2026-07-01': ['2026-06-01']}, '60D': {}, '90D': {},
        })

    def test_mixed_jwcd_retrieval_windows_block_ready_day(self):
        rows = self.rows()
        for row in rows:
            row.update(publication_ts='2026-11-02T08:00:00Z',
                       retrieved_at_utc='2026-11-02T08:45:00Z',
                       retrieval_completed_at='2026-11-02T08:49:00Z')
        self.assertTrue(audit_rows(rows)['days'][0]['model_ready'])
        rows[0]['retrieval_completed_at'] = '2026-11-02T08:50:00Z'
        day, = audit_rows(rows)['days']
        self.assertEqual(day['availability_proven_rows'], 24)
        self.assertEqual(day['issues'], ['MIXED_JWCD_RETRIEVAL_WINDOWS'])
        self.assertFalse(day['model_ready'])

    def test_missing_or_mixed_snapshot_ids_block_ready_day(self):
        rows = self.rows()
        for row in rows:
            row.update(publication_ts='2026-11-02T08:00:00Z',
                       retrieved_at_utc='2026-11-02T08:45:00Z',
                       retrieval_completed_at='2026-11-02T08:49:00Z')
        rows[0]['jwcd_snapshot_id'] = 'different-snapshot'
        del rows[1]['jwcd_snapshot_id']
        day, = audit_rows(rows)['days']
        self.assertEqual(day['availability_proven_rows'], 24)
        self.assertEqual(day['issues'], [
            'MISSING_JWCD_SNAPSHOT_ID', 'MIXED_JWCD_SNAPSHOT_IDS',
        ])
        self.assertFalse(day['model_ready'])

    def test_complete_pairs_are_counted_separately_from_backtest_ready_days(self):
        rows = self.rows()
        day, = audit_rows(rows)['days']
        self.assertTrue(day['complete_pairs'])
        self.assertFalse(day['model_ready'])
        self.assertEqual(audit_rows(rows)['complete_pair_days'], 1)
        rows[0]['jwcd_mw'] = ''
        self.assertEqual(audit_rows(rows)['complete_pair_days'], 0)

    def test_archive_price_column_counts_pairs_without_proving_availability(self):
        rows = self.rows()
        for row in rows:
            row['fix1_pln_mwh'] = row.pop('fix1')
            del row['publication_ts']
        report = audit_rows(rows)
        self.assertEqual(report['complete_pair_days'], 1)
        self.assertEqual(report['model_ready_days'], 0)

    def test_partial_explicit_utc_cannot_be_completed_with_legacy_hours(self):
        rows = self.rows()
        for hour, row in enumerate(rows, 1):
            row['hour'] = f'H{hour:02d}'
        missing_period = rows[5].pop('period_start_utc')
        partial, = audit_rows(rows)['days']
        self.assertFalse(partial['complete_periods'])
        self.assertEqual(partial['missing_periods_utc'], [missing_period])
        self.assertIn('INVALID_OR_AMBIGUOUS_PERIOD', partial['issues'])

        for row in rows:
            row.pop('period_start_utc', None)
        legacy, = audit_rows(rows)['days']
        self.assertTrue(legacy['complete_periods'])

    def test_swapped_utc_periods_block_complete_pairs_when_labels_are_present(self):
        rows = self.rows()
        for hour, row in enumerate(rows, 1):
            row['hour'] = f'H{hour:02d}'
        self.assertEqual(audit_rows(rows)['complete_pair_days'], 1)
        rows[0]['period_start_utc'], rows[1]['period_start_utc'] = (
            rows[1]['period_start_utc'], rows[0]['period_start_utc']
        )
        day, = audit_rows(rows)['days']
        self.assertEqual(day['unique_periods'], 24)
        self.assertFalse(day['complete_pairs'])
        self.assertTrue(day['complete_periods'])
        self.assertIn('HOUR_UTC_MISMATCH', day['issues'])
        self.assertNotIn('INCOMPLETE_OR_DUPLICATE_PERIODS', day['issues'])

    def test_dst_coverage_lists_missing_duplicate_and_unexpected_utc_keys(self):
        for day in ('2026-03-29', '2026-10-25'):
            with self.subTest(day=day):
                rows = self.rows(day)
                first, second = (row['period_start_utc'] for row in rows[:2])
                rows[0]['period_start_utc'] = rows[2]['period_start_utc']
                rows[1]['period_start_utc'] = '2026-01-01T00:00:00Z'
                result, = audit_rows(rows)['days']
                self.assertFalse(result['complete_periods'])
                self.assertEqual(result['missing_periods_utc'], [first, second])
                self.assertEqual(result['duplicate_periods_utc'], [rows[2]['period_start_utc']])
                self.assertEqual(result['unexpected_periods_utc'], ['2026-01-01T00:00:00+00:00'])

    def test_duplicate_csv_column_is_rejected(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'history.csv'
            path.write_text(
                '# metadata\ndelivery_date,jwcd_mw,jwcd_mw\n2026-11-03,12000,13000\n',
                encoding='utf-8',
            )
            with self.assertRaisesRegex(ValueError, 'duplicate CSV columns: jwcd_mw'):
                read_csv(path)

    def test_csv_rejects_extra_or_missing_fields_but_preserves_empty_cells(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'history.csv'
            for record in ('2026-11-03,100,12000,extra', '2026-11-03,100'):
                with self.subTest(record=record):
                    path.write_text('delivery_date,fix1,jwcd_mw\n' + record + '\n', encoding='utf-8')
                    with self.assertRaisesRegex(ValueError, 'wrong field count'):
                        read_csv(path)
            path.write_text('delivery_date,fix1,jwcd_mw\n2026-11-03,100,\n', encoding='utf-8')
            self.assertEqual(read_csv(path)[0]['jwcd_mw'], '')

    def test_csv_rejects_unclosed_quoted_value(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'history.csv'
            path.write_text('delivery_date,jwcd_mw\n2026-11-03,"12000\n', encoding='utf-8')
            with self.assertRaises(csv.Error):
                read_csv(path)

    def test_price_aliases_must_agree_when_both_populated(self):
        rows = self.rows()
        rows[0]['fix1_pln_mwh'] = '100.00'
        rows[1]['fix1'] = ''
        rows[1]['fix1_pln_mwh'] = '100'
        self.assertEqual(audit_rows(rows)['complete_pair_days'], 1)
        rows[0]['fix1_pln_mwh'] = '101'
        day, = audit_rows(rows)['days']
        self.assertFalse(day['complete_pairs'])
        self.assertIn('CONFLICTING_FIX1_COLUMNS', day['issues'])
        self.assertEqual(day['conflicting_fix1_periods_utc'], [rows[0]['period_start_utc']])
