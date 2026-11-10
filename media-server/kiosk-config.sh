# media-server/kiosk-config.sh: sourced (bash) by the kiosk admin scripts. The kiosk is a deployment's hardware: its
# address, login, the URL its Chromes load and its screens are the kiosk block of deployments/<id>/deployment.yaml
# (SM_DEPLOYMENT selects the deployment; deployments/README.md). media-server/local-scripts/.env's MEDIA_SERVER_HOST /
# MEDIA_SERVER_USER still override host and user, as before.
#
#   kiosk_get KEY        prints kiosk.KEY (host, user, base_url, screens...); smconfig's reason on stderr, 1 on failure
#   kiosk_render_dir OUT renders media-server/remote-scripts/ into OUT with the deployment's values filled in
#                        (media-server/render_kiosk.py); the deploy scripts install from OUT, never the templates

_KIOSK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

_kiosk_py() {
    local py="$_KIOSK_REPO/.venv/bin/python"
    [[ -x "$py" ]] || py=python3
    PYTHONPATH="$_KIOSK_REPO${PYTHONPATH:+:$PYTHONPATH}" "$py" "$@"
}

kiosk_get() {
    local v
    v="$(_kiosk_py -m smconfig get "kiosk.$1")" || return 1
    [[ -n "$v" ]] || { echo "kiosk: the deployment's kiosk.$1 is empty" >&2; return 1; }
    printf '%s\n' "$v"
}

kiosk_render_dir() {
    _kiosk_py "$_KIOSK_REPO/media-server/render_kiosk.py" render-dir "$1" >/dev/null
}
