"""Small chronological temperature models; no network or protected-data loader.

Predictions use prior published labels. Distribution residuals are one-step
prediction errors, never residuals fitted to the current scored observation.
"""
from datetime import datetime, timezone
import math
import numpy as np

FAMILIES = ('climatology', 'hrrr_bias', 'gefs_bias', 'equal_blend_bias',
            'cloud_residual_ridge', 'cloud_wind_disagreement_ridge')
FEATURE_KEYS = {'hrrr':'hrrr_sparse_max_f', 'gefs':'gefs_max_mean_f',
                'cloud':'hrrr_cloud_mean_pct', 'u':'hrrr_u_mean_m_s',
                'v':'hrrr_v_mean_m_s', 'disagreement':'model_abs_disagreement_f'}


def utc(value):
    t = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if t.tzinfo is None:
        raise ValueError('timezone required')
    return t.astimezone(timezone.utc)


def _number(row, name):
    value = row[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'invalid feature {name}')
    return float(value)


def _baseline(row, family, names):
    if family == 'hrrr_bias':
        return _number(row, names['hrrr'])
    if family == 'gefs_bias':
        return _number(row, names['gefs'])
    if family == 'climatology':
        return 0.
    return (_number(row, names['hrrr']) + _number(row, names['gefs'])) / 2


def _fit_predict(rows, outcomes, current, family, names):
    target = np.asarray([y - _baseline(row, family, names) for row, y in zip(rows, outcomes)])
    base = _baseline(current, family, names)
    columns = (['cloud'] if family == 'cloud_residual_ridge' else
               ['cloud','u','v','disagreement'] if family == 'cloud_wind_disagreement_ridge' else [])
    if not columns:
        return float(base + target.mean()), {'base_f':base,'bias_f':float(target.mean()),
            'feature_coefficients':{},'feature_centers':{},'feature_scales':{}}
    matrix = np.asarray([[_number(row,names[c]) for c in columns] for row in rows])
    center, scale = matrix.mean(axis=0), matrix.std(axis=0)
    scale = np.where(scale > 1e-12, scale, 1.)
    standardized = (matrix-center)/scale
    beta = np.linalg.solve(standardized.T@standardized + 10.*np.eye(len(columns)),
                           standardized.T@(target-target.mean()))
    point = (np.asarray([_number(current,names[c]) for c in columns])-center)/scale
    return float(base + target.mean() + point@beta), {'base_f':base,
        'residual_intercept_f':float(target.mean()-center@(beta/scale)),
        'ridge_alpha':10.,
        'feature_coefficients':{names[c]:float(v) for c,v in zip(columns,beta/scale)},
        'feature_centers':{names[c]:float(v) for c,v in zip(columns,center)},
        'feature_scales':{names[c]:float(v) for c,v in zip(columns,scale)}}


def forecast_sequence(features, label_records, family, feature_names=None):
    """Return 44 scored forecasts after a fixed 20-date prefix for a 64-date input.

Generic length >=21 permits meaningful synthetic tests. A delayed publication
can cause fail-closed readiness, never silently remove a scored date.
"""
    if family not in FAMILIES:
        raise ValueError('unsupported model family')
    names = FEATURE_KEYS if feature_names is None else feature_names
    dates = [row['climate_date'] for row in features]
    if dates != sorted(set(dates)) or len(dates) < 21:
        raise ValueError('chronological unique input with warmup required')
    labels = {row['climate_date']:row for row in label_records}
    if len(labels) != len(label_records) or set(labels) != set(dates):
        raise ValueError('exact development label scope required')
    for date in dates:
        _number(labels[date], 'reported_high_f')
        utc(labels[date]['issued_at'])
    predictions, output = {}, []
    for i, current in enumerate(features):
        date = current['climate_date']
        decision = utc(current['decision_at'])
        if decision.date().isoformat() != date or (decision.hour, decision.minute, decision.second, decision.microsecond) != (18,0,0,0):
            raise ValueError('exact18UTC decision date/time required')
        prior = [row for row in features[:i]
                 if utc(labels[row['climate_date']]['issued_at']) < decision]
        if len(prior) < 10:
            if i >= 20:
                raise ValueError('insufficient published training history')
            continue
        outcomes = [labels[row['climate_date']]['reported_high_f'] for row in prior]
        location, fit_metadata = _fit_predict(prior, outcomes, current, family, names)
        residuals = [labels[row['climate_date']]['reported_high_f']-predictions[row['climate_date']]
                     for row in prior if row['climate_date'] in predictions]
        predictions[date] = location
        if i < 20:
            continue
        if len(prior) < 20 or len(residuals) < 10:
            raise ValueError('common scored-date readiness failed; do not drop dates')
        forecast = {'climate_date':date, 'decision_at':current['decision_at'],
                       'family':family, 'location_f':location,
                       'prequential_residuals_f':[float(v) for v in residuals],
                       'kernel_sigma_f':1., 'training_dates':[row['climate_date'] for row in prior],
                       'residual_dates':[row['climate_date'] for row in prior if row['climate_date'] in predictions],
                       'training_count':len(prior), 'residual_count':len(residuals),
                       'fit_metadata':fit_metadata,
                       'predictive_mean_f':location+sum(residuals)/len(residuals)}
        forecast['interval_80_f'] = [quantile(forecast,.1),quantile(forecast,.9)]
        forecast['interval_95_f'] = [quantile(forecast,.025),quantile(forecast,.975)]
        output.append(forecast)
    return output


def predictive_cdf(forecast, value):
    return sum(.5*(1+math.erf((value-forecast['location_f']-error)/math.sqrt(2)))
               for error in forecast['prequential_residuals_f'])/len(forecast['prequential_residuals_f'])


def quantile(forecast, probability):
    lo = forecast['location_f']+min(forecast['prequential_residuals_f'])-12.
    hi = forecast['location_f']+max(forecast['prequential_residuals_f'])+12.
    for _ in range(60):
        midpoint = (lo+hi)/2
        if predictive_cdf(forecast,midpoint)<probability:
            lo = midpoint
        else:
            hi = midpoint
    return (lo+hi)/2


def bracket_probabilities(forecast, contracts):
    """Map a continuous predictive distribution to rounded integer-F brackets."""
    if not forecast['prequential_residuals_f'] or forecast['kernel_sigma_f'] != 1.:
        raise ValueError('registered residual distribution required')
    def integer(row, key):
        value = row[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value != int(value):
            raise ValueError('integer settlement bound required')
        return int(value)
    intervals = []
    for row in contracts:
        strike = row['strike_type']
        if strike == 'less':
            intervals.append((-math.inf, integer(row,'cap_strike')-1))
        elif strike == 'greater':
            intervals.append((integer(row,'floor_strike')+1, math.inf))
        elif strike == 'between':
            intervals.append((integer(row,'floor_strike'),integer(row,'cap_strike')))
        else:
            raise ValueError('unknown settlement interval')
    intervals.sort()
    if (len(intervals)<2 or intervals[0][0] != -math.inf or intervals[-1][1] != math.inf or
        any(lo>hi for lo,hi in intervals) or
        any(not math.isfinite(left[1]) or not math.isfinite(right[0]) or right[0] != left[1]+1
            for left,right in zip(intervals,intervals[1:]))):
        raise ValueError('integer intervals do not form a disjoint exhaustive partition')
    def cdf(x):
        return predictive_cdf(forecast,x)
    values = {}
    for row in contracts:
        strike = row['strike_type']
        if strike == 'less':
            value = cdf(float(row['cap_strike'])-.5)
        elif strike == 'greater':
            value = 1-cdf(float(row['floor_strike'])+.5)
        elif strike == 'between':
            value = cdf(float(row['cap_strike'])+.5)-cdf(float(row['floor_strike'])-.5)
        else:
            raise ValueError('unknown settlement interval')
        ticker = row['market_ticker']
        if ticker in values or value < -1e-12:
            raise ValueError('invalid or duplicate bracket')
        values[ticker] = max(0., value)
    if abs(sum(values.values())-1) > 1e-8:
        raise ValueError('brackets do not partition predictive distribution')
    return values
