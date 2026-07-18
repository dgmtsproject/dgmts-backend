"""Resolve frontend graph routes for instruments (project-agnostic).

Serial numbers (sno) are unique and preferred for Instantel / Micromate devices.
"""

from urllib.parse import quote


STATIC_INSTRUMENT_GRAPH_ROUTES = {
    'SMG1': '/background',
    'SMG-1': '/dynamic-seismograph?instrument=SMG-1',
    'SMG-2': '/anc-seismograph',
    'SMG2': '/instantel1-seismograph',
    'SMG4': '/instantel2-seismograph',
    'SMG-3': '/smg3-seismograph',
    'SMG-4': '/instantel2-seismograph',
    'TILT-142939': '/tiltmeter-142939',
    'TILT-143969': '/tiltmeter-143969',
    'Instantel 1': '/instantel1-seismograph',
    'Instantel 2': '/instantel2-seismograph',
    'UM15783': '/instantel1-seismograph',
    'UM16368': '/instantel2-seismograph',
    'ROCKSMG-1': '/rocksmg1-seismograph',
    'ROCKSMG-2': '/rocksmg2-seismograph',
    'AMTS-1': '/single-prism-with-time',
    'AMTS-2': '/single-prism-with-time',
    '13453': '/dynamic-seismograph?instrument=13453',
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
    """Return UM15783 / UM16368. Prefers unique serial (sno)."""
    if not instrument:
        return None

    from_serial = _normalize_micromate_token(instrument.get('sno'))
    if from_serial:
        return from_serial

    from_id = _normalize_micromate_token(instrument.get('instrument_id'))
    if from_id:
        return from_id

    from_name = _normalize_micromate_token(instrument.get('instrument_name'))
    if from_name:
        return from_name

    route = resolve_instrument_graph_route(instrument)
    return MICROMATE_GRAPH_ROUTE_TO_DEVICE.get(route)


def resolve_instrument_graph_route(instrument):
    """
    Return the SPA path for an instrument's graph page, or None if unsupported.

    Prefers unique serial number (sno) for Micromate devices.
    """
    if not instrument:
        return None

    instrument_id = str(instrument.get('instrument_id') or '').strip()
    instrument_name = str(instrument.get('instrument_name') or '').strip()

    micromate_from_serial = _normalize_micromate_token(instrument.get('sno'))
    if micromate_from_serial:
        return MICROMATE_DEVICE_GRAPH_ROUTES[micromate_from_serial]

    route = STATIC_INSTRUMENT_GRAPH_ROUTES.get(instrument_id)
    if route:
        return route

    micromate_from_id = _normalize_micromate_token(instrument_id)
    if micromate_from_id:
        return MICROMATE_DEVICE_GRAPH_ROUTES[micromate_from_id]

    micromate_from_name = _normalize_micromate_token(instrument_name)
    if micromate_from_name:
        return MICROMATE_DEVICE_GRAPH_ROUTES[micromate_from_name]

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


def find_micromate_instrument(device_folder):
    """
    Find the instruments row for a Micromate FTP folder (UM15783 / UM16368).

    Match order (serial is unique per client policy):
      1) sno
      2) instrument_id
      3) instrument_name
      4) legacy Instantel 1 / Instantel 2 ids
    """
    folder = str(device_folder or '').strip().upper()
    if folder not in MICROMATE_DEVICE_GRAPH_ROUTES:
        return None

    try:
        from config import Config
        from supabase import create_client

        supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

        # Prefer unique serial number
        by_sno = (
            supabase.table('instruments')
            .select('*')
            .eq('sno', folder)
            .execute()
        )
        if by_sno.data:
            return by_sno.data[0]

        # instrument_id may become the serial itself
        by_id = (
            supabase.table('instruments')
            .select('*')
            .eq('instrument_id', folder)
            .execute()
        )
        if by_id.data:
            return by_id.data[0]

        # Current Instantel rows use instrument_name = UM15783 / UM16368
        by_name = (
            supabase.table('instruments')
            .select('*')
            .eq('instrument_name', folder)
            .execute()
        )
        if by_name.data:
            return by_name.data[0]

        # Also try common local IDs (SMG2 / SMG4 / legacy Instantel names)
        if folder == 'UM15783':
            alias_ids = ['SMG2', 'Instantel 1']
        else:
            alias_ids = ['SMG4', 'SMG-4', 'Instantel 2']

        for alias in alias_ids:
            alias_resp = (
                supabase.table('instruments')
                .select('*')
                .eq('instrument_id', alias)
                .execute()
            )
            if alias_resp.data:
                row = alias_resp.data[0]
                resolved = resolve_micromate_device_folder(row)
                if resolved == folder:
                    return row

        return None
    except Exception as e:
        print(f'Error finding Micromate instrument for {folder}: {e}')
        return None
