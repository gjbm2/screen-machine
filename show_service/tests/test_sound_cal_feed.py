from show_service.sound_cal_feed import SoundCalFeed


def test_the_mic_is_wanted_while_a_speaker_is_measured():
    replies = [{"state": "idle"}, {"state": "running"}, {"state": "done"}]
    t = [0.0]
    f = SoundCalFeed("http://127.0.0.1:5000", fetch=lambda url: replies.pop(0), clock=lambda: t[0])
    woke = []
    f.add_on_change(lambda: woke.append(1))
    assert f.url == "http://127.0.0.1:5000/api/sound/calibrate"
    f.poll_once()
    assert not f.running()
    f.poll_once()
    assert f.running() and woke == [1]
    t[0] = 10.0                                   # Flask stopped answering long ago: not running
    assert not f.running()
    f.poll_once()
    assert not f.running() and woke == [1, 1]


def test_the_runs_own_mic_is_claimed_while_it_runs():
    replies = [{"state": "running", "mic": "room-mic"}, {"state": "running", "mic": "room-mic"},
               {"state": "running", "mic": "pc22-mic"}, {"state": "done", "mic": "pc22-mic"}]
    f = SoundCalFeed("http://127.0.0.1:5000", fetch=lambda url: replies.pop(0), clock=lambda: 0.0)
    claims = []
    f.set_claim(lambda stream, on: claims.append((stream, on)))
    for _ in range(4):
        f.poll_once()
    assert claims == [("room-mic", True), ("room-mic", False), ("pc22-mic", True), ("pc22-mic", False)]
