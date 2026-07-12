"""Resolve frontend graph routes for instruments (project-agnostic)."""

from urllib.parse import quote


STATIC_INSTRUMENT_GRAPH_ROUTES = {
    'SMG1': '/background',
    'SMG-1': '/dynamic-seismograph?instrument=SMG-1',
    'SMG-2': '/anc-seismograph',
    'SMG-3': '/smg3-seismograph',
    'TILT-142939': '/tiltmeter-142939',
    'TILT-143969': '/tiltmeter-143969',
    'TILTMETER-30846': '/tiltmeter-30846',
    'Instantel 1': '/instantel1-seismograph',
    'Instantel 2': '/instantel2-seismograph',
    'ROCKSMG-1': '/rocksmg1-seismograph',
    'ROCKSMG-2': '/rocksmg2-seismograph',
    'AMTS-1': '/single-prism-with-time',
    'AMTS-2': '/single-prism-with-time',
}

SERIAL_NUMBER_GRAPH_ROUTES = {
    'UM15783': '/instantel1-seismograph',
    'UM16368': '/instantel2-seismograph',
}


def resolve_instrument_graph_route(instrument):
    """
    Return the SPA path for an instrument's graph page, or None if unsupported.

    ``instrument`` is a dict-like row with instrument_id, instrument_name,
    syscom_device_id, and optional sno (serial number).
    """
    if not instrument:
        return None

    instrument_id = str(instrument.get('instrument_id') or '').strip()
    instrument_name = str(instrument.get('instrument_name') or '').strip()
    serial_number = str(instrument.get('sno') or '').strip().upper()

    route = STATIC_INSTRUMENT_GRAPH_ROUTES.get(instrument_id)
    if route:
        return route

    if serial_number in SERIAL_NUMBER_GRAPH_ROUTES:
        return SERIAL_NUMBER_GRAPH_ROUTES[serial_number]

    name_lower = instrument_name.lower()
    if name_lower in ('instantel 1', 'instantel-1'):
        return '/instantel1-seismograph'
    if name_lower in ('instantel 2', 'instantel-2'):
        return '/instantel2-seismograph'

    if instrument.get('syscom_device_id') is not None:
        encoded_id = quote(instrument_id, safe='')
        return f'/dynamic-seismograph?instrument={encoded_id}'

    if 'TILT' in instrument_id:
        suffix = instrument_id.split('-', 1)[-1] if '-' in instrument_id else None
        if suffix:
            return f'/tiltmeter-{suffix}'

    if instrument_name == 'Tiltmeter' and instrument_id:
        return '/tiltmeter'

    return None


def instrument_has_graph_view(instrument):
    return resolve_instrument_graph_route(instrument) is not None
