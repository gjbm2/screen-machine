"""Drop-to-key is time-based (0.5 s of frames) with a 2 s grace for a new viewer whose decoder is warming up."""
from stream_service import media
from stream_service.media import MediaClient, MediaHub


def _client(hub):
    c = MediaClient(None, "test", None)
    c.announced = True
    hub.clients[c.id] = c
    return c


def _feed(hub, n, start=0, key_every=60):
    for i in range(start, start + n):
        hub.on_frame(i, i % key_every == 0, b"x")


def test_new_viewer_is_not_dropped_while_its_decoder_warms_up():
    hub = MediaHub("s-t", fps=60)
    c = _client(hub)
    _feed(hub, 100)                   # a stalled new viewer: nothing drained for 100 frames (1.7 s)
    assert c.dropped == 0 and len(c.media) == 100


def test_after_the_grace_a_viewer_drops_at_half_a_second(monkeypatch):
    hub = MediaHub("s-t", fps=60)
    c = _client(hub)
    _feed(hub, 1)
    c.first_key_at -= media.GRACE_S + 1         # past the grace
    c.media.clear()
    _feed(hub, 40, start=1)
    assert c.dropped > 0 and hub.max_queue(c) == 30


def test_the_floor_holds_at_low_fps():
    hub = MediaHub("s-t", fps=10)
    c = _client(hub)
    assert hub.max_queue(c) == media.MAX_QUEUE
