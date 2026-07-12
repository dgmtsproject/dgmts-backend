from flask import Blueprint, jsonify, request
from supabase import create_client
from config import Config
from services.instrument_route_service import resolve_instrument_graph_route, instrument_has_graph_view

instrument_bp = Blueprint('instruments', __name__, url_prefix='/api/instruments')

supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)


@instrument_bp.route('/graph-route', methods=['GET'])
def get_instrument_graph_route():
    """
    Resolve the frontend graph path for one instrument.

    Query: instrument_id (required)
    """
    instrument_id = (request.args.get('instrument_id') or '').strip()
    if not instrument_id:
        return jsonify({'error': 'instrument_id is required'}), 400

    try:
        response = (
            supabase.table('instruments')
            .select('instrument_id, instrument_name, syscom_device_id, sno, project_id, is_active')
            .eq('instrument_id', instrument_id)
            .execute()
        )
        if not response.data:
            return jsonify({'error': f'No instrument found for {instrument_id}'}), 404

        instrument = response.data[0]
        route = resolve_instrument_graph_route(instrument)
        return jsonify({
            'instrument_id': instrument.get('instrument_id'),
            'project_id': instrument.get('project_id'),
            'graph_route': route,
            'has_graph_view': route is not None,
            'is_active': instrument.get('is_active'),
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@instrument_bp.route('/graph-routes', methods=['GET'])
def list_instrument_graph_routes():
    """
    List graph routes for instruments, optionally filtered by project_id.
    """
    project_id = request.args.get('project_id')

    try:
        query = (
            supabase.table('instruments')
            .select('instrument_id, instrument_name, syscom_device_id, sno, project_id, is_active')
            .order('instrument_name')
        )
        if project_id:
            query = query.eq('project_id', int(project_id))

        response = query.execute()
        instruments = response.data or []

        results = []
        for instrument in instruments:
            route = resolve_instrument_graph_route(instrument)
            if not route:
                continue
            results.append({
                'instrument_id': instrument.get('instrument_id'),
                'instrument_name': instrument.get('instrument_name'),
                'project_id': instrument.get('project_id'),
                'graph_route': route,
                'is_active': instrument.get('is_active'),
            })

        return jsonify({
            'instruments': results,
            'count': len(results),
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
