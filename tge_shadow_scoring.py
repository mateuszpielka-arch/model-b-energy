"""Pure SHADOW scoring, never writes to PostgreSQL or modifies FROZEN."""
from math import isfinite

HOURS = set(range(1, 25))


def score_verified(forecast: dict[int, float], actual: dict[int, float], reference: dict[int, float]):
    """Only score 24 matching, independently verified hourly observations."""
    if set(forecast) != HOURS or set(actual) != HOURS or set(reference) != HOURS:
        raise ValueError('Expected 24 unique hours in forecast, actual and reference')
    if not all(isfinite(v) for values in (forecast, actual, reference) for v in values.values()):
        raise ValueError('Non-finite price')
    if any(abs(actual[h] - reference[h]) > 0.005 for h in HOURS):
        raise ValueError('Actuals do not match independent TGE reference')
    if any(actual[h] == 0 for h in HOURS):
        raise ValueError('MAPE undefined for zero actual price')
    def metrics(hours):
        errors = [forecast[h] - actual[h] for h in hours]
        a = [actual[h] for h in hours]
        return {
            'n': len(errors),
            'mae_pln_mwh': sum(abs(e) for e in errors) / len(errors),
            'mape_pct': 100 * sum(abs(e / v) for e, v in zip(errors, a)) / len(errors),
            'wape_pct': 100 * sum(abs(e) for e in errors) / sum(abs(v) for v in a),
            'bias_pln_mwh': sum(errors) / len(errors),
        }
    return {'global': metrics(range(1, 25)),
            'H11-H16': metrics(range(11, 17)),
            'H18-H22': metrics(range(18, 23))}
