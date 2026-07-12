from config import Config
from supabase import create_client

supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)


def get_display_instrument_id(instrument) -> str:
    """Return the user-facing instrument ID (instrument_id_second) when set."""
    if not instrument:
        return ''
    if isinstance(instrument, dict):
        return instrument.get('instrument_id_second') or instrument.get('instrument_id', '')
    return str(instrument).strip()


def find_instrument_by_id_candidates(candidate_ids):
    """Return the first instruments row matching any of the given instrument_id values."""
    for candidate_id in candidate_ids:
        try:
            resp = (
                supabase.table('instruments')
                .select('*')
                .eq('instrument_id', candidate_id)
                .execute()
            )
            if resp.data:
                return resp.data[0]
        except Exception as e:
            print(f"Could not look up instrument {candidate_id}: {e}")
    return None


def is_instrument_active(instrument_id: str) -> bool:
    """Return False when instrument monitoring is turned off in the app."""
    try:
        resp = (
            supabase.table('instruments')
            .select('is_active')
            .eq('instrument_id', instrument_id)
            .execute()
        )
        if not resp.data:
            return True
        value = resp.data[0].get('is_active')
        if value is None:
            return True
        return bool(value)
    except Exception as e:
        print(f"Could not read is_active for {instrument_id}: {e}")
        return True
