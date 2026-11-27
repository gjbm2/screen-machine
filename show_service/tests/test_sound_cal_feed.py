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
