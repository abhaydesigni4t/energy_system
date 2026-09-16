import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)


def _to_float(value, default=0.0):
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_as_of(as_of):
    if as_of is None:
        return date.today()
    if isinstance(as_of, datetime):
        return as_of.date()
    if isinstance(as_of, date):
        return as_of
    if isinstance(as_of, str):
        try:
            return datetime.strptime(as_of.strip(), "%Y-%m-%d").date()
        except ValueError:
            return date.today()
    return date.today()


def _coerce_date(value):
    """Best-effort coercion of a date/datetime-like value to a plain date."""
    if isinstance(value, datetime):
        return value.date()
    return value


def _completed_years(effective_date, current_date):
    years = current_date.year - effective_date.year
    if (current_date.month, current_date.day) < (effective_date.month, effective_date.day):
        years -= 1
    return max(0, years)


def get_effective_rate(base_rate, escalator_percent, effective_date, current_date):
    """
    Business rule:
      - If current_date < effective_date: no escalation has started yet;
        rate = base_rate.
      - If current_date >= effective_date: the FIRST escalation is applied
        immediately on the effective date (NOT after a full year has
        elapsed). After that, the rate compounds once more for every
        anniversary of effective_date that current_date has reached.

        i.e. escalations_applied = 1 + completed_years_since(effective_date)

    NOTE: kept at full precision here -- do NOT round to 2dp at this level,
    it silently erases the escalation on typical sub-$1 per-kWh rates.
    Round only the final dollar amount, in calculate_revenue().
    """
    ed = _coerce_date(effective_date)
    cd = _coerce_date(current_date)

    if cd < ed:
        return base_rate

    escalations = 1 + _completed_years(ed, cd)
    return base_rate * ((1 + escalator_percent / 100) ** escalations)


def calculate_years_elapsed(effective_date, as_of=None):
    if effective_date is None:
        return 0
    return _completed_years(effective_date, _coerce_as_of(as_of))


def get_escalated_cost_breakdown(device, as_of=None):
    as_of_date = _coerce_as_of(as_of)
    notes = []

    if device is None:
        return {
            'device_id': None, 'base_cost': 0.0, 'escalator_pct': 0.0,
            'effective_date': None, 'as_of_date': as_of_date,
            'years_elapsed': 0, 'escalated_cost': 0.0,
            'notes': ['No device -> cost 0'],
        }

    base_cost = _to_float(device.per_unit_cost, default=0.0)
    escalator_pct = _to_float(device.escalator, default=0.0)
    effective_date = device.effective_date

    if device.per_unit_cost is None:
        notes.append('per_unit_cost is NULL -> escalated cost 0')
        base_cost = 0.0
    if base_cost < 0:
        notes.append('per_unit_cost negative -> clamped to 0')
        base_cost = 0.0
    if device.escalator is None:
        notes.append('escalator is NULL -> treated as 0% (cost = base)')
        escalator_pct = 0.0

    if effective_date is None:
        notes.append('effective_date is NULL -> no escalation (cost = base)')
        years_elapsed = 0
        escalated_cost = base_cost
    else:
        ed = _coerce_date(effective_date)
        if as_of_date < ed:
            notes.append('current date is before effective_date -> no escalation yet (cost = base)')
            years_elapsed = 0
        else:
            years_elapsed = _completed_years(ed, as_of_date)
            if years_elapsed == 0:
                notes.append(
                    'effective_date reached -> first escalation applied immediately '
                    '(before first anniversary)'
                )
            else:
                notes.append(
                    f'effective_date reached and {years_elapsed} anniversary(ies) '
                    'completed -> escalation compounded accordingly'
                )
        try:
            escalated_cost = get_effective_rate(base_cost, escalator_pct, effective_date, as_of_date)
        except (OverflowError, ValueError):
            notes.append('escalation overflow -> fell back to base cost')
            escalated_cost = base_cost

    if escalated_cost < 0:
        escalated_cost = 0.0

    return {
        'device_id': getattr(device, 'id', None),
        'base_cost': round(base_cost, 6),
        'escalator_pct': escalator_pct,
        'effective_date': effective_date,
        'as_of_date': as_of_date,
        'years_elapsed': years_elapsed,
        'escalated_cost': round(escalated_cost, 6),   # kept precise, not 2dp
        'notes': notes,
    }


def calculate_revenue(device, energy_kwh, as_of=None, metric=''):
    """
    USD = kWh x escalated_cost, for EVERY period (Today/Week/Month/Year/
    Lifetime/custom ranges/bills). `metric` is accepted for labeling only
    and no longer changes which rate is used.
    """
    kwh = _to_float(energy_kwh, default=0.0)
    bd = get_escalated_cost_breakdown(device, as_of)
    revenue = round(kwh * bd['escalated_cost'], 2)
    return revenue