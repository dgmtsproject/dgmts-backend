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

MICROMATE_DEVICE_GRAPH_ROUTES = {
    'UM15783': '/instantel1-seismograph',
    'UM16368': '/instantel2-seismograph',
}

MICROMATE_GRAPH_ROUTE_TO_DEVICE = {
    '/instantel1-seismograph': 'UM15783',
    '/instantel2-seismograph': 'UM16368',
}


def _normalize_micromate_token(value):
    token = str(value or '').strip().upper()
    return token if token in MICROMATE_DEVICE_GRAPH_ROUTES else None


def resolve_micromate_device_folder(instrument):
    """Return UM15783 / UM16368 when the row represents an Instantel Micromate device."""
    if not instrument:
        return None

    from_name = _normalize_micromate_token(instrument.get('instrument_name'))
    if from_name:
        return from_name

    from_serial = _normalize_micromate_token(instrument.get('sno'))
    if from_serial:
        return from_serial

    route = resolve_instrument_graph_route(instrument)
    return MICROMATE_GRAPH_ROUTE_TO_DEVICE.get(route)


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

    route = STATIC_INSTRUMENT_GRAPH_ROUTES.get(instrument_id)
    if route:
        return route

    micromate_from_name = _normalize_micromate_token(instrument_name)
    if micromate_from_name:
        return MICROMATE_DEVICE_GRAPH_ROUTES[micromate_from_name]

    name_lower = instrument_name.lower()
    if name_lower in ('instantel 1', 'instantel-1'):
        return '/instantel1-seismograph'
    if name_lower in ('instantel 2', 'instantel-2'):
        return '/instantel2-seismograph'

    micromate_from_serial = _normalize_micromate_token(instrument.get('sno'))
    if micromate_from_serial:
        return MICROMATE_DEVICE_GRAPH_ROUTES[micromate_from_serial]

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


def find_micromate_instrument(device_folder):
    """
    Find the instruments row for a Micromate FTP folder (UM15783 / UM16368).

    Prefers instrument_name match over sno because clients sometimes copy the wrong sno.
    Falls back to legacy instrument_id values (Instantel 1 / Instantel 2).
    """
    folder = str(device_folder or '').strip().upper()
    if folder not in MICROMATE_DEVICE_GRAPH_ROUTES:
        return None

    try:
        from config import Config
        from supabase import create_client

        supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
        response = (
            supabase.table('instruments')
            .select('*')
            .or_(f'instrument_name.eq.{folder},sno.eq.{folder}')
            .execute()
        )
        rows = response.data or []
        if not rows:
            legacy_id = 'Instantel 1' if folder == 'UM15783' else 'Instantel 2'
            legacy_resp = (
                supabase.table('instruments')
                .select('*')
                .eq('instrument_id', legacy_id)
                .execute()
            )
            rows = legacy_resp.data or []

        if not rows:
            return None

        for row in rows:
            if str(row.get('instrument_name') or '').strip().upper() == folder:
                return row

        for row in rows:
            if str(row.get('sno') or '').strip().upper() == folder:
                return row

        return rows[0]
    except Exception as e:
        print(f'Error finding Micromate instrument for {folder}: {e}')
        return None
