"""The screen clip's rendered ease (routes/clip_ease.py): starts and ends at rest, never goes back."""
from routes.clip_ease import position, rest_range


def test_eases_from_rest_to_rest_and_never_goes_back():
    ts = [k / 30 for k in range(30 * 30 + 1)]
    p = [position(t, 30, 8) for t in ts]
    assert p[0] == 0 and p[-1] == 1
    assert all(b >= a for a, b in zip(p, p[1:]))
    step = [b - a for a, b in zip(p, p[1:])]
    assert step[0] < step[len(step) // 2] / 100 and step[-1] < step[len(step) // 2] / 100


def test_bigger_movements_rest_longer():
    small, big = rest_range(0.5, [30, 180]), rest_range(20, [30, 180])
    assert small[0] == 30 and small[1] < big[0] and big[1] == 180


def test_a_real_speed_video_keeps_its_frames_between_the_ramps():
    # ease_video's numbers for a 247-frame 48 fps video: the steady part lands on whole source frames at 1 a frame
    n, rate, ri, ro = 247, 48.0, 1.5, 1.75
    play = (n - 1) / rate + (ri + ro) / 2
    xs = [position(k / rate, play, ri, ro) * (n - 1) for k in range(int(round(play * rate)) + 1)]
    steady = xs[int(ri * rate) + 1:int((play - ro) * rate) - 1]
    assert all(abs(x - round(x)) < 1e-6 for x in steady)
    assert all(abs((b - a) - 1) < 1e-6 for a, b in zip(steady, steady[1:]))
    assert xs[0] == 0 and xs[-1] == n - 1
