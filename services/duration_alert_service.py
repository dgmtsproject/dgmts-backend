"""
Duration-based alert service.

Fires separate emails when a threshold is exceeded continuously for
duration_seconds (configured per instrument). Uses its own threshold values
and email lists, independent of instant threshold alerts.
"""

import os
from datetime import datetime, timedelta, timezone

import pytz
import requests
from supabase import create_client

from config import Config
from services.alert_service import get_project_info, log_alert_event, _legacy_syscom_device_id
from services.email_service import send_email
from services.instrument_utils import get_display_instrument_id

supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

INSTANTEL_NODE_IDS = {
    'Instantel 1': 24252,
    'Instantel 2': 24252,
}

INSTRUMENT_TO_NODE_ID = {v: k for k, v in Config.NODE_TO_INSTRUMENT_ID.items()}


def _parse_timestamp(ts_str):
    if not ts_str:
        return None
    cleaned = ts_str.replace('Z', '+00:00') if str(ts_str).endswith('Z') else str(ts_str)
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def _is_tiltmeter(instrument_id):
    return instrument_id in ('TILT-142939', 'TILT-143969')


def _has_duration_config(instrument):
    duration_seconds = instrument.get('duration_seconds')
    if not duration_seconds or int(duration_seconds) <= 0:
        return False
    if _is_tiltmeter(instrument.get('instrument_id', '')):
        return any(
            instrument.get(col)
            for col in (
                'x_y_z_duration_alert_values',
                'x_y_z_duration_warning_values',
                'x_y_z_duration_shutdown_values',
            )
        )
    return any(
        instrument.get(col) is not None
        for col in ('duration_alert_value', 'duration_warning_value', 'duration_shutdown_value')
    )


def _reading_exceeds_threshold(reading, threshold, per_axis_thresholds=None):
    """Return list of (axis_label, value) pairs that exceed the threshold."""
    exceeded = []
    if per_axis_thresholds:
        for axis_key, axis_label in (('x', 'X'), ('y', 'Y'), ('z', 'Z')):
            val = abs(float(reading.get(f'{axis_key}_value') or 0))
            axis_threshold = per_axis_thresholds.get(axis_key)
            if axis_threshold is not None and val >= float(axis_threshold):
                exceeded.append((axis_label, val))
        return exceeded

    if threshold is None:
        return exceeded
    threshold = float(threshold)
    for axis_label, key in (('X', 'x_value'), ('Y', 'y_value'), ('Z', 'z_value')):
        val = abs(float(reading.get(key) or 0))
        if val >= threshold:
            exceeded.append((axis_label, val))
    return exceeded


def find_duration_threshold_events(readings, duration_seconds, level_configs):
    """
    Detect sustained threshold exceedances.

    level_configs: list of dicts with keys alert_type, value, per_axis (optional)
    Returns events with start/end timestamps for deduplication.
    """
    if not duration_seconds or duration_seconds <= 0 or not readings:
        return []

    duration_seconds = int(duration_seconds)
    parsed = []
    for r in readings:
        ts = _parse_timestamp(r.get('timestamp'))
        if ts is not None:
            parsed.append((ts, r))
    parsed.sort(key=lambda item: item[0])
    if not parsed:
        return []

    events = []
    for level in level_configs:
        alert_type = level['alert_type']
        threshold = level.get('value')
        per_axis = level.get('per_axis')
        if threshold is None and not per_axis:
            continue

        streak_start_ts = None
        streak_start_str = None
        streak_peak = 0.0
        streak_axes = set()
        streak_fired = False

        for ts, reading in parsed:
            exceeded = _reading_exceeds_threshold(reading, threshold, per_axis)
            if exceeded:
                if streak_start_ts is None:
                    streak_start_ts = ts
                    streak_start_str = reading['timestamp']
                    streak_peak = max(v for _, v in exceeded)
                    streak_axes = {a for a, _ in exceeded}
                    streak_fired = False
                else:
                    streak_peak = max(streak_peak, max(v for _, v in exceeded))
                    streak_axes.update(a for a, _ in exceeded)

                if not streak_fired:
                    elapsed = (ts - streak_start_ts).total_seconds()
                    if elapsed >= duration_seconds:
                        events.append({
                            'alert_type': alert_type,
                            'start_timestamp': streak_start_str,
                            'end_timestamp': reading['timestamp'],
                            'duration_seconds': duration_seconds,
                            'axes': sorted(streak_axes),
                            'peak_value': streak_peak,
                            'threshold': threshold,
                            'per_axis': per_axis,
                        })
                        streak_fired = True
            else:
                streak_start_ts = None
                streak_start_str = None
                streak_fired = False

    return events


def _duration_level_configs(instrument):
    instrument_id = instrument.get('instrument_id', '')
    configs = []
    if _is_tiltmeter(instrument_id):
        mapping = [
            ('duration_shutdown', 'x_y_z_duration_shutdown_values'),
            ('duration_warning', 'x_y_z_duration_warning_values'),
            ('duration_alert', 'x_y_z_duration_alert_values'),
        ]
        for alert_type, col in mapping:
            per_axis = instrument.get(col)
            if per_axis and any(per_axis.get(k) is not None for k in ('x', 'y', 'z')):
                configs.append({'alert_type': alert_type, 'value': None, 'per_axis': per_axis})
    else:
        mapping = [
            ('duration_shutdown', 'duration_shutdown_value'),
            ('duration_warning', 'duration_warning_value'),
            ('duration_alert', 'duration_alert_value'),
        ]
        for alert_type, col in mapping:
            val = instrument.get(col)
            if val is not None:
                configs.append({'alert_type': alert_type, 'value': val, 'per_axis': None})
    return configs


def _emails_for_duration_event(instrument, alert_type):
    email_map = {
        'duration_shutdown': instrument.get('duration_shutdown_emails') or [],
        'duration_warning': instrument.get('duration_warning_emails') or [],
        'duration_alert': instrument.get('duration_alert_emails') or [],
    }
    return [e for e in email_map.get(alert_type, []) if e]


def _already_sent_duration_alert(instrument_id, node_id, alert_type, start_timestamp):
    resp = (
        supabase.table('sent_alerts')
        .select('id')
        .eq('instrument_id', instrument_id)
        .eq('node_id', node_id)
        .eq('alert_type', alert_type)
        .eq('timestamp', start_timestamp)
        .execute()
    )
    return bool(resp.data)


def _fetch_syscom_readings(instrument, window_hours=6):
    instrument_id = str(instrument.get('instrument_id', '')).strip()
    device_raw = instrument.get('syscom_device_id')
    if device_raw is None:
        device_raw = _legacy_syscom_device_id(instrument_id)
    if device_raw is None:
        return [], None

    device_id = int(device_raw)
    api_key = os.environ.get('SYSCOM_API_KEY') or Config.SYSCOM_API_KEY
    if not api_key:
        return [], device_id

    est_tz = pytz.timezone('US/Eastern')
    now_est = datetime.now(timezone.utc).astimezone(est_tz)
    start_est = now_est - timedelta(hours=window_hours)
    url = (
        f"https://scs.syscom-instruments.com/public-api/v1/records/background/{device_id}/data"
        f"?start={start_est.strftime('%Y-%m-%dT%H:%M:%S')}"
        f"&end={now_est.strftime('%Y-%m-%dT%H:%M:%S')}"
    )
    response = requests.get(url, headers={'x-scs-api-key': api_key}, timeout=60)
    if response.status_code not in (200, 204):
        log_alert_event(
            'ERROR',
            f'Duration alert: Syscom fetch failed {response.status_code}',
            instrument_id,
        )
        return [], device_id
    if response.status_code == 204:
        return [], device_id

    readings = []
    for entry in response.json().get('data', []):
        readings.append({
            'timestamp': entry[0],
            'x_value': abs(float(entry[1])),
            'y_value': abs(float(entry[2])),
            'z_value': abs(float(entry[3])),
        })
    return readings, device_id


def _fetch_tiltmeter_readings(instrument_id, window_hours=24):
    node_id = INSTRUMENT_TO_NODE_ID.get(instrument_id)
    if node_id is None:
        return [], None

    since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    resp = (
        supabase.table('sensor_readings')
        .select('*')
        .eq('node_id', node_id)
        .gte('timestamp', since)
        .order('timestamp', desc=False)
        .execute()
    )
    readings = []
    for row in resp.data or []:
        readings.append({
            'timestamp': row['timestamp'],
            'x_value': abs(float(row.get('x_value') or 0)),
            'y_value': abs(float(row.get('y_value') or 0)),
            'z_value': abs(float(row.get('z_value') or 0)),
        })
    return readings, node_id


def _fetch_instantel_readings(instrument_id, window_minutes=720):
    node_id = INSTANTEL_NODE_IDS.get(instrument_id)
    if instrument_id == 'Instantel 1':
        url = 'https://imsite.dullesgeotechnical.com/api/micromate/readings'
        response = requests.get(url, timeout=60)
        if response.status_code != 200:
            return [], node_id
        raw = response.json().get('MicromateReadings', [])
        readings = []
        for row in raw:
            readings.append({
                'timestamp': row.get('Time'),
                'x_value': abs(float(row.get('Longitudinal') or 0)),
                'y_value': abs(float(row.get('Transverse') or 0)),
                'z_value': abs(float(row.get('Vertical') or 0)),
            })
        return readings, node_id

    if instrument_id == 'Instantel 2':
        est_tz = pytz.timezone('US/Eastern')
        now_est = datetime.now(timezone.utc).astimezone(est_tz)
        start_est = now_est - timedelta(minutes=window_minutes)
        from_date = start_est.strftime('%Y-%m-%d %H:%M:%S')
        to_date = now_est.strftime('%Y-%m-%d %H:%M:%S')
        url = (
            'https://imsite.dullesgeotechnical.com/api/micromate/UM16368/readings'
            f'?fromdatetime={from_date}&todatetime={to_date}'
        )
        response = requests.get(url, timeout=60)
        if response.status_code != 200:
            return [], node_id
        raw = response.json().get('UM16368Readings', [])
        readings = []
        for row in raw:
            readings.append({
                'timestamp': row.get('Time'),
                'x_value': abs(float(row.get('Longitudinal_PPV') or 0)),
                'y_value': abs(float(row.get('Transverse_PPV') or 0)),
                'z_value': abs(float(row.get('Vertical_PPV') or 0)),
            })
        return readings, node_id

    return [], node_id


def _fetch_readings_for_instrument(instrument):
    instrument_id = instrument.get('instrument_id', '')
    if _is_tiltmeter(instrument_id):
        return _fetch_tiltmeter_readings(instrument_id)
    if instrument_id in INSTANTEL_NODE_IDS:
        return _fetch_instantel_readings(instrument_id)
    return _fetch_syscom_readings(instrument)


def _format_event_time(ts_str):
    ts = _parse_timestamp(ts_str)
    if not ts:
        return ts_str
    est = pytz.timezone('US/Eastern')
    if ts.tzinfo is None:
        ts = est.localize(ts)
    return ts.astimezone(est).strftime('%m-%d-%Y %I:%M:%S %p EST')


def _create_duration_email_body(instrument, events, project_name):
    instrument_id = instrument.get('instrument_id', '')
    display_id = get_display_instrument_id(instrument)
    display_name = instrument.get('instrument_name') or display_id
    location = instrument.get('instrument_location') or 'N/A'

    event_html = ''
    for event in events:
        axes = ', '.join(event['axes'])
        threshold_desc = event.get('per_axis') or event.get('threshold')
        level = event['alert_type'].replace('duration_', '').title()
        event_html += f"""
        <div class="alert-item">
            <p><strong>{level} — duration achieved ({event['duration_seconds']}s)</strong></p>
            <p>Axes: {axes} | Peak value: {event['peak_value']:.6f}</p>
            <p>Threshold: {threshold_desc}</p>
            <p>Started: {_format_event_time(event['start_timestamp'])}</p>
            <p>Duration met at: {_format_event_time(event['end_timestamp'])}</p>
        </div>
        """

    return f"""
    <html>
    <body style="font-family: Arial, sans-serif; background:#f5f5f5; padding:20px;">
        <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:8px;padding:24px;">
            <h2 style="color:#0056d2;">Duration Threshold Alert</h2>
            <p><strong>Project:</strong> {project_name}</p>
            <p><strong>Instrument:</strong> {display_name} ({display_id})</p>
            <p><strong>Location:</strong> {location}</p>
            <hr/>
            {event_html}
            <p style="color:#666;font-size:12px;">
                This email was sent because vibration remained above the duration threshold
                for the configured number of seconds. Instant threshold alerts are separate.
            </p>
        </div>
    </body>
    </html>
    """


def check_duration_alerts_for_instrument(instrument):
    """Evaluate and send duration alerts for one instrument row."""
    if not _has_duration_config(instrument):
        return

    instrument_id = instrument.get('instrument_id', '')
    duration_seconds = int(instrument.get('duration_seconds'))
    level_configs = _duration_level_configs(instrument)
    if not level_configs:
        return

    readings, node_id = _fetch_readings_for_instrument(instrument)
    if not readings or node_id is None:
        print(f'[duration] No readings for {instrument_id}')
        return

    events = find_duration_threshold_events(readings, duration_seconds, level_configs)
    if not events:
        return

    pending_by_type = {}
    for event in events:
        if _already_sent_duration_alert(
            instrument_id, node_id, event['alert_type'], event['start_timestamp']
        ):
            continue
        pending_by_type.setdefault(event['alert_type'], []).append(event)

    if not pending_by_type:
        return

    project_name = 'Unknown Project'
    try:
        info = get_project_info(instrument_id)
        if info:
            project_name = info.get('project_name', project_name)
    except Exception as e:
        print(f'[duration] project info error for {instrument_id}: {e}')

    est_tz = pytz.timezone('US/Eastern')
    now_est = datetime.now(timezone.utc).astimezone(est_tz).strftime('%m-%d-%Y %I:%M %p EST')
    display_id = get_display_instrument_id(instrument)

    for alert_type, type_events in pending_by_type.items():
        emails = _emails_for_duration_event(instrument, alert_type)
        if not emails:
            print(f'[duration] No emails configured for {instrument_id} {alert_type}')
            continue

        level_label = alert_type.replace('duration_', '').title()
        subject = f'⏱ Duration {level_label} Alert — {display_id} — {now_est}'
        body = _create_duration_email_body(instrument, type_events, project_name)

        if send_email(','.join(emails), subject, body):
            for event in type_events:
                resp = supabase.table('sent_alerts').insert({
                    'instrument_id': instrument_id,
                    'node_id': node_id,
                    'timestamp': event['start_timestamp'],
                    'alert_type': event['alert_type'],
                }).execute()
                if resp.data:
                    log_alert_event(
                        'DURATION_ALERT',
                        f"Duration {level_label} alert sent (sustained {event['duration_seconds']}s)",
                        instrument_id,
                        resp.data[0]['id'],
                    )
            print(f'[duration] Sent {alert_type} for {instrument_id} to {len(emails)} recipient(s)')
        else:
            log_alert_event('SEND EMAIL_FAILED', f'Duration alert email failed for {alert_type}', instrument_id)


def check_and_send_all_duration_alerts():
    """Scheduled job: check all instruments with duration alert configuration."""
    print('Checking duration-based alerts...')
    try:
        resp = supabase.table('instruments').select('*').execute()
        for instrument in resp.data or []:
            if _has_duration_config(instrument):
                try:
                    check_duration_alerts_for_instrument(instrument)
                except Exception as e:
                    iid = instrument.get('instrument_id', 'unknown')
                    print(f'[duration] Error for {iid}: {e}')
                    log_alert_event('ERROR', f'Duration alert check failed: {e}', iid)
    except Exception as e:
        print(f'[duration] Failed to load instruments: {e}')
