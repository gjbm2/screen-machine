from flask import Blueprint, jsonify
from routes.manage_jobs import cancel_all_jobs as cancel_jobs
from utils.logger import log_to_console, info, error, warning, debug

generate_api = Blueprint('generate_api', __name__, url_prefix="/api/generate")


@generate_api.route('/cancel_all_jobs', methods=['GET', 'POST'])
def cancel_all_jobs():
    try:
        cancelled = cancel_jobs()
        return jsonify({
            "success": True,
            "cancelled": cancelled,
            "message": f"Cancelled {cancelled} jobs"
        })
    except Exception as e:
        error(f"Error cancelling jobs: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
