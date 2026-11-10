"""The deployment layer in Flask (deployments/README.md): GET /api/deployment, capability refusals, the deployment's
site files found by name, and the kiosk admin's status script built from the manifest's kiosk.screens."""
import os

import pytest

import smconfig


@pytest.fixture
def client():
    from app import app
    app.config["TESTING"] = True
    return app.test_client()


def _off(monkeypatch, *caps):
    real = smconfig.enabled
    monkeypatch.setattr(smconfig, "enabled", lambda c: False if c in caps else real(c))


# -- GET /api/deployment ------------------------------------------------------------------------------------------
def test_deployment_endpoint_shape(client):
    r = client.get("/api/deployment")
    assert r.status_code == 200
    d = r.get_json()
    assert set(d) == {"id", "name", "capabilities", "default_room", "screens", "kiosk", "wording"}
    assert d["wording"]["lights_menu"] == "Lamps, glitterball, pinspots"   # PC22's menu hint, as before
    assert d["id"] == smconfig.deployment_id() == "finsbury"
    assert d["name"] == smconfig.deployment().name
    assert set(d["capabilities"]) == set(smconfig.CAPABILITIES)
    assert all(isinstance(v, bool) for v in d["capabilities"].values())
    assert d["default_room"] == "drawing-room"
    for s in d["screens"]:
        assert set(s) == {"id", "name", "live", "mask", "art_only", "room"}
        assert isinstance(s["live"], bool) and isinstance(s["mask"], bool) and s["name"]
        assert s["art_only"] is (not s["live"])
    # finsbury: the destinations with a live block, an intensity_cfg or a room, in file order
    assert [(s["id"], s["live"], s["mask"], s["room"]) for s in d["screens"]] == [
        ("north-screen", True, True, "drawing-room"), ("south-screen", True, True, "drawing-room"),
        ("devtest", True, False, "lab")]
    assert d["kiosk"] == {"screens": [{"id": "north-screen", "name": "north", "cdp_port": 9222},
                                      {"id": "south-screen", "name": "south", "cdp_port": 9223}]}


def _fake_deployment(tmp_path, monkeypatch, manifest: dict, dests: list, rooms: dict):
    import json
    import yaml
    d = tmp_path / "deployments" / manifest["id"]
    d.mkdir(parents=True)
    (d / "deployment.yaml").write_text(yaml.safe_dump(manifest))
    (d / "publish-destinations.json").write_text(json.dumps(dests))
    (d / "rooms.json").write_text(json.dumps(rooms))
    monkeypatch.setattr(smconfig, "DEPLOYMENTS_DIR", tmp_path / "deployments")
    monkeypatch.setenv("SM_DEPLOYMENT", manifest["id"])
    monkeypatch.setattr(smconfig, "deployment", lambda: smconfig.load(manifest["id"]))


def test_deployment_lists_art_only_room_screens_and_no_kiosk(tmp_path, monkeypatch):
    # C2: an art-only screen named by a room is listed (art_only, its room); a headless or roomless plain
    # destination is not; a deployment without a kiosk says kiosk: null
    from routes.deployment_api import deployment_info
    _fake_deployment(tmp_path, monkeypatch, {"id": "probe-c2", "capabilities": {}},
                     [{"id": "vj", "name": "VJ", "live": {}}, {"id": "frame", "name": "Frame"},
                      {"id": "phone", "headless": True, "live": {}}, {"id": "loose", "name": "Loose"}],
                     {"studio": {"screens": ["vj"]}, "hall": {"screens": ["frame"]}})
    d = deployment_info()
    assert d["kiosk"] is None
    assert d["screens"] == [
        {"id": "vj", "name": "VJ", "live": True, "mask": False, "art_only": False, "room": "studio"},
        {"id": "frame", "name": "Frame", "live": False, "mask": False, "art_only": True, "room": "hall"}]


def test_deployment_endpoint_follows_the_manifest(client, monkeypatch):
    caps = dict(smconfig.deployment().capabilities, generation=False)
    monkeypatch.setattr(smconfig.Deployment, "capabilities", property(lambda self: caps))
    assert client.get("/api/deployment").get_json()["capabilities"]["generation"] is False


# -- capabilities off ---------------------------------------------------------------------------------------------
def test_generate_image_refused_when_generation_off(client, monkeypatch):
    _off(monkeypatch, "generation")
    from routes import generate_api
    called = []
    monkeypatch.setattr(generate_api, "process_generate_image_request", lambda *a, **k: called.append(1))
    r = client.post("/api/generate-image", data={"data": '{"prompt": "x"}'})
    assert r.status_code == 503
    assert "generation is off" in r.get_json()["error"]
    assert not called


def test_scheduler_generate_and_animate_refused_when_generation_off(monkeypatch):
    _off(monkeypatch, "generation")
    from routes import scheduler_handlers as h
    monkeypatch.setattr(h, "get_generation_service", lambda: pytest.fail("generation service used"))
    monkeypatch.setattr(h, "get_animation_service", lambda: pytest.fail("animation service used"))
    out = []
    from datetime import datetime
    now = datetime.now()
    assert not h.handle_generate({"input": {"prompt": "a cat"}}, {"vars": {}}, now, out, "devtest")
    assert not h.handle_animate({"input": {"prompt": "a cat"}}, {"vars": {}}, now, out, "devtest")
    assert sum("generation is off" in str(x) for x in out) == 2


def test_alexa_404_when_off(client, monkeypatch):
    _off(monkeypatch, "alexa")
    r = client.post("/api/alexa", json={"request": {}})
    assert r.status_code == 404
    assert r.get_json() == {"error": "alexa is off in this deployment"}


def test_tv_power_off_is_a_no_op(monkeypatch):
    _off(monkeypatch, "tv_power")
    from routes import samsung_utils as su
    monkeypatch.setattr(su, "get_status", lambda *a, **k: pytest.fail("the TV was contacted"))
    for fn in (su.device_wake, su.device_sleep, su.device_standby, su.device_sync):
        assert fn("north-screen") == "no_action"


def test_kiosk_admin_off_refused(client, monkeypatch):
    _off(monkeypatch, "kiosk_admin")
    monkeypatch.setenv("ADMIN_API_TOKEN", "t")
    from routes import admin_api
    monkeypatch.setattr(admin_api, "_ssh_run", lambda *a, **k: pytest.fail("ssh attempted"))
    r = client.get("/api/admin-355729b8/status", headers={"Authorization": "Bearer t"})
    assert r.status_code == 409
    assert "kiosk_admin is off" in r.get_json()["error"]


def test_art_tags_startup_off_starts_no_threads(monkeypatch):
    _off(monkeypatch, "art_tags", "art_director")
    import routes.art_tags as at
    monkeypatch.setattr(at, "get_worker", lambda: pytest.fail("tag worker started"))
    import routes.art_tags.director as director
    monkeypatch.setattr(director, "get_director", lambda: pytest.fail("art director started"))
    at.startup()
    assert at.enqueue("north-screen", "x.jpg") is False


def test_art_tags_sweep_and_retag_refused_when_off(client, monkeypatch):
    _off(monkeypatch, "art_tags")
    import routes.art_tags as at
    monkeypatch.setattr(at, "get_worker", lambda: pytest.fail("queued into a worker that never runs"))
    for url in ("/api/art-tags/sweep", "/api/buckets/north-screen/tags/x.jpg/retag"):
        r = client.post(url)
        assert r.status_code == 409, url
        assert r.get_json() == {"error": "art_tags is off in this deployment", "capability": "art_tags"}


def test_deployment_endpoint_without_rooms_has_no_default_room(client, monkeypatch):
    monkeypatch.setattr(smconfig, "default_room", lambda dep=None: (_ for _ in ()).throw(FileNotFoundError("rooms.json")))
    r = client.get("/api/deployment")
    assert r.status_code == 200
    assert r.get_json()["default_room"] is None


# -- the deployment's site files ----------------------------------------------------------------------------------
def test_findfile_resolves_the_deployments_destinations():
    from routes.utils import SEARCH_PATHS, findfile
    assert SEARCH_PATHS[0] == str(smconfig.deployment().dir)
    want = os.path.abspath(smconfig.path("publish-destinations.json"))
    assert findfile("publish-destinations.json") == want
    assert findfile("rooms.json") == os.path.abspath(smconfig.path("rooms.json"))
    assert findfile("intensity.json") == os.path.abspath(smconfig.path("intensity.json"))
    from routes import publish_api, publisher, lightsensor
    want = str(smconfig.path("publish-destinations.json"))
    assert publish_api.PUBLISH_DESTINATIONS_FILE == publisher.PUBLISH_DESTINATIONS_FILE == want
    assert lightsensor.INTENSITY_FILE == smconfig.path("intensity.json")


def test_site_defaults_come_from_the_deployment():
    import config
    from routes import room_client, room_hints
    from routes.art_tags import director, worker
    assert (config.PORT, config.WS_PORT) == (5000, 8765)
    assert (config.INTENSITY_CFG["default_lat"], config.INTENSITY_CFG["default_lon"]) == (51.5074, -0.1278)
    assert worker.PRIORITY_BUCKETS == ("north-screen", "south-screen", "devtest")
    assert director.ROOMS_FILE == smconfig.path("rooms.json")
    assert room_hints.CONFIG == smconfig.path("room", "auto_hints.json")
    if not os.environ.get("ROOM_SERVICE_URL"):
        assert room_client.ROOM_URL == "http://127.0.0.1:8700"


def test_overlays_print_the_site_address_only_when_the_site_has_one(monkeypatch):
    """The Alexa overlays' line under the QR is manifest wording site_address (finsbury: its public hostname, as the
    templates had it); a site without one gets no line."""
    from routes import display
    from routes.utils import dict_substitute
    assert smconfig.load("finsbury").words("site_address", "") == "screen.finsburybankforsavings.com"
    assert smconfig.load("example").words("site_address", "") == ""
    assert display.site_address() == smconfig.deployment().words("site_address", "")
    for name in ("overlay_explain.html.j2", "overlay_schedule.html.j2"):
        on = dict_substitute(name, {"SCREEN_NAME": "Screen", "SITE_ADDRESS": "art.example.org"})
        off = dict_substitute(name, {"SCREEN_NAME": "Screen", "SITE_ADDRESS": ""})
        assert "\n          art.example.org\n        </div>\n" in on, name
        assert "art.example.org" not in off and "This is <span" in off and "margin-top: 1em" not in off, name


def test_samsung_token_is_a_secret(monkeypatch, tmp_path):
    monkeypatch.setattr(smconfig, "SECRETS_DIR", tmp_path)
    from routes import samsung_utils as su
    p = su.token_path("north-screen-token.txt")
    assert p == str(tmp_path / "samsung" / "north-screen-token.txt")
    assert su.token_path("../../evil.txt") == str(tmp_path / "samsung" / "evil.txt")
    assert (os.stat(tmp_path / "samsung").st_mode & 0o777) == 0o700


# -- the kiosk admin's status script ------------------------------------------------------------------------------
def test_kiosk_screens_from_the_manifest():
    from routes import admin_api
    assert admin_api.kiosk_screens() == [("north-screen", "north", 9222), ("south-screen", "south", 9223)]


def test_status_script_is_unchanged_for_finsbury():
    from routes import admin_api
    assert admin_api.status_script() == STATUS_SCRIPT_BEFORE


# /status's script as it was before deployments/ (routes/admin_api.py at 68a5022d), byte for byte
STATUS_SCRIPT_BEFORE = r"""
echo "UPTIME=$(uptime -p)"
# kiosk.service is the sway kiosk (default since 2026-10-13); the X11 fallback kiosk-x11.service counts as active too
KIOSK=$(systemctl is-active kiosk.service)
[ "$KIOSK" != active ] && systemctl is-active --quiet kiosk-x11.service && KIOSK=active
echo "KIOSK=$KIOSK"

# Chrome screens. The browser's command line starts /opt/google/chrome/chrome, so anchor on a first word containing
# "chrome": an unanchored pattern also matches this ssh shell's own command line (which contains the pattern)
NORTH_PID=$(pgrep -f '^[^ ]*chrome[^ ]* .*display/north-screen' | head -1)
SOUTH_PID=$(pgrep -f '^[^ ]*chrome[^ ]* .*display/south-screen' | head -1)
echo "NORTH_CHROME=${NORTH_PID:+running}"
echo "SOUTH_CHROME=${SOUTH_PID:+running}"
[ -z "$NORTH_PID" ] && echo "NORTH_CHROME=stopped"
[ -z "$SOUTH_PID" ] && echo "SOUTH_CHROME=stopped"

# Current URLs from Chrome command lines
NORTH_CMD=$(ps -p "$NORTH_PID" -o args= 2>/dev/null || true)
SOUTH_CMD=$(ps -p "$SOUTH_PID" -o args= 2>/dev/null || true)
NORTH_URL=$(echo "$NORTH_CMD" | grep -oP 'https?://\S+' | tail -1)
SOUTH_URL=$(echo "$SOUTH_CMD" | grep -oP 'https?://\S+' | tail -1)
echo "NORTH_URL=${NORTH_URL:-unknown}"
echo "SOUTH_URL=${SOUTH_URL:-unknown}"

# Displays: one "<output> connected ..." line per connected output, from sway (the Wayland kiosk, default since
# 2026-10-13; every socket is tried, a crashed sway can leave a stale one), else Xorg (kiosk-x11 fallback: one X
# screen per TV, :0.0 north, :0.1 south; on a single-screen X, :0.1 just fails), else the kernel's connector
# status when no display server answers
displays() {
    for s in /run/user/$(id -u)/sway-ipc.*.sock; do
        out=$(SWAYSOCK=$s timeout 3 swaymsg -t get_outputs -r 2>/dev/null | jq -r '.[] | "\(.name) connected "
            + (if .active then "\(.current_mode.width)x\(.current_mode.height)@\(.current_mode.refresh / 1000)Hz" else "(inactive)" end)' 2>/dev/null)
        [ -n "$out" ] && { echo "$out"; return; }
    done
    out=$({ DISPLAY=:0.0 xrandr --query; DISPLAY=:0.1 xrandr --query; } 2>/dev/null | grep ' connected')
    [ -n "$out" ] && { echo "$out"; return; }
    for f in /sys/class/drm/card*-*/status; do
        [ "$(cat "$f" 2>/dev/null)" = connected ] || continue
        n=${f%/status}; n=${n##*/}
        echo "${n#card*-} connected ($(cat "${f%/status}/enabled" 2>/dev/null))"
    done
}
displays | while read line; do echo "DISPLAY_LINE=$line"; done

# Resources
echo "MEM_PERCENT=$(free | grep Mem | awk '{printf "%.0f", ($3/$2)*100}')"
echo "DISK_PERCENT=$(df -h / | tail -1 | awk '{print $5}' | tr -d '%')"
echo "LOAD=$(uptime | awk -F'load average:' '{print $2}' | cut -d, -f1 | tr -d ' ')"
"""
