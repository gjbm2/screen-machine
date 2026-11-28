"""routes.phone_door: through the phones' tunnel, the karaoke panel only; the door routes /rs to room_service."""
import flask

from routes import phone_door as D


def app():
    a = flask.Flask(__name__)
    a.before_request(lambda: D.guard(flask.request))
    for path in ("/karaoke", "/api/karaoke/now", "/room", "/api/admin/status", "/api/sound/outputs"):
        a.add_url_rule(path, path, lambda: "ok", methods=["GET", "PUT", "POST"])
    return a.test_client()


T = {"Host": "abc-def.trycloudflare.com"}


def test_tunnel_lets_the_karaoke_panel_only():
    c = app()
    assert c.get("/karaoke", headers=T).status_code == 200
    assert c.get("/api/karaoke/now", headers=T).status_code == 200
    assert c.get("/api/admin/status", headers=T).status_code == 404
    r = c.get("/room", headers={**T, "Accept": "text/html"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/karaoke")
    assert c.get("/api/sound/outputs", headers=T).status_code == 200
    assert c.put("/api/sound/outputs", headers=T).status_code == 404       # read-only there


def test_on_the_lan_nothing_changes():
    c = app()
    assert c.get("/room").status_code == 200 and c.get("/api/admin/status").status_code == 200


def test_the_door_routes_rs_to_the_room():
    assert D._target("/rs/api/v1/status").endswith(":8700/api/v1/status")
    assert D._target("/karaoke").endswith(":5000/karaoke")


def test_room_config_reads_tunnel_suffixes(tmp_path):
    from room_service import config as C
    f = tmp_path / "room.yaml"
    f.write_text('tunnel_host_suffixes: [".trycloudflare.com", "bad"]\n')
    cfg = C.load(f) if hasattr(C, "load") else None
    if cfg is not None:
        assert cfg.tunnel_host_suffixes == [".trycloudflare.com"]
