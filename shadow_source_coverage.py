"""Read-only coverage of explicit retained PK5 and Fix1 evidence; no model admission."""
from datetime import date
from shadow_manifest_audit import audit_manifests
from shadow_pk5_history_audit import audit_pk5_history
from shadow_scoring import expected_periods


def audit_source_coverage(*, pk5_groups, fix1_manifests, start_day, end_day):
    """Validate all supplied sources, then classify calendar days in the explicit scope.

    Both sources means evidence is present, not that a training window is ready.
    Source reports retain all supplied observations, including outside the scope.
    No database, network, write, timestamp inference or model evaluation.
    """
    for bound in (start_day, end_day):
        if type(bound) is not date or bound in (date.min, date.max):
            raise ValueError('Supported calendar date bounds required')
    if start_day > end_day:
        raise ValueError('Start day must not be after end day')
    pk5 = audit_pk5_history(pk5_groups)
    fix1 = audit_manifests(fix1_manifests, start_day=start_day, end_day=end_day)
    pk5_days = {item['delivery_date'] for item in pk5['days']
                if item['selected_snapshot_id'] is not None}
    fix1_days = {item['delivery_date'] for item in fix1['days']}
    kinds = ('both_source', 'pk5_only', 'fix1_only', 'neither_source')
    classified = {kind + '_days': [] for kind in kinds}
    by_hours = {
        str(hours): dict(calendar_days=0, calendar_periods=0,
                         **{kind + suffix: 0 for kind in kinds for suffix in ('_days', '_periods')})
        for hours in (23, 24, 25)
    }
    for ordinal in range(start_day.toordinal(), end_day.toordinal() + 1):
        day = date.fromordinal(ordinal)
        label = day.isoformat()
        kind = ('both_source' if label in pk5_days and label in fix1_days else
                'pk5_only' if label in pk5_days else
                'fix1_only' if label in fix1_days else 'neither_source')
        classified[kind + '_days'].append(label)
        hours = len(expected_periods(day))
        bucket = by_hours[str(hours)]
        bucket['calendar_days'] += 1
        bucket['calendar_periods'] += hours
        bucket[kind + '_days'] += 1
        bucket[kind + '_periods'] += hours
    return dict(
        read_only=True, model_readiness_assessed=False,
        scope=dict(start_day=start_day.isoformat(), end_day=end_day.isoformat(),
                   sources='provided_paths'),
        source_coverage={
            **classified, 'calendar_days': (end_day - start_day).days + 1,
            'by_hours': by_hours,
            'fix1_price_conflict_days': [
                item['delivery_date'] for item in fix1['days']
                if start_day.isoformat() <= item['delivery_date'] <= end_day.isoformat()
                and item['price_consensus'] == 'CONFLICT'
            ],
        },
        pk5_audit=pk5, fix1_audit=fix1,
    )
