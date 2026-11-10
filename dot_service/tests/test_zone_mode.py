"""dot_service and mode zones: a tag whose room has its own modes reads that zone's mode; every other tag (and every
tag on a deployment without zones) reads the site's status.mode, as before."""
import asyncio

from dot_service import __main__ as dot

SITE_MODES = {"modes": {"normal": {"label": "Normal"}, "party": {"label": "Party"}}}
LAB_MODES = {"modes": {"gallery": {"label": "Normal"}, "live-test": {"label": "Live test (devtest)"}}}


def _status(zones=None):
    st = {
        "mode": {"current": "normal", "target": "party"},
        "rooms": [{"id": "front", "name": "Front", "screens": ["fs"]},
                  {"id": "back", "name": "Back", "screens": ["bs"], "own_modes": True}],
        "screens": {},
        "subsystems": {},
    }
    if zones is not None:
        st["zones"] = zones
    return st


def test_mode_words_as_before():
    assert dot.mode_words({"current": "normal", "target": "party"}, SITE_MODES) == ("Party", "Normal")
    assert dot.mode_words({"current": "normal", "target": None}, SITE_MODES) == ("Normal", "")
    assert dot.mode_words({}, {}) == ("", "")


def test_zone_of():
    assert dot.zone_of(_status(), "back") is None                   # no zones key: the site's mode
    z = {"room": "back", "mode": {"current": "live-test"}}
    assert dot.zone_of(_status([z]), "back") is z
    assert dot.zone_of(_status([z]), "front") is None


class _Svc(dot.DotService):
    def __init__(self, zone_modes):
        super().__init__()
        self.modes = SITE_MODES
        self.asked: list[str] = []
        self._zone_modes = zone_modes

    async def soft(self, url, default=None):
        self.asked.append(url)
        return self._zone_modes


def _card(svc, room, status, auto_on=True):
    tag = dot.Tag({"device": "d1", "room": room})
    src = {"status": status, "auto": {"on": auto_on}, "player": {}, "show": {}, "card": {}, "look": {},
           "listening": {}}
    return asyncio.run(svc.card_for(tag, src))


def test_without_zones_every_tag_reads_the_site_and_asks_nothing_more():
    svc = _Svc(LAB_MODES)
    c = _card(svc, "back", _status())
    assert (c.mode, c.changing_from, c.auto) == ("Party", "Normal", True)
    assert svc.asked == []


def test_a_zone_tag_reads_its_zone_and_has_no_auto():
    zones = [{"room": "back", "name": "Back", "screens": ["bs"], "mode": {"current": "gallery", "target": "live-test"}}]
    svc = _Svc(LAB_MODES)
    c = _card(svc, "back", _status(zones))
    assert (c.mode, c.changing_from, c.auto) == ("Live test (devtest)", "Normal", False)
    assert svc.asked == [f"{dot.ROOM}/api/v1/modes?room=back"]
    # the site room's tag on the same deployment: the site's mode and Auto, as before
    c2 = _card(svc, "front", _status(zones))
    assert (c2.mode, c2.changing_from, c2.auto) == ("Party", "Normal", True)
