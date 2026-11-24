"""A published video's picture is a same-origin URL, so an https page can load it (owner, 24 Nov: broken picture)."""
from routes import publisher


def test_new_video_pictures_are_same_origin():
    assert publisher._video_frame_url("/output/south-screen.mp4") == "/api/generate/jpg_from_mp4?file=/output/south-screen.mp4"


def test_saved_absolute_urls_are_read_as_their_path():
    old = "http://95.141.21.170:5000/api/generate/jpg_from_mp4?file=/output/south-screen.mp4"
    assert publisher._same_origin(old) == "/api/generate/jpg_from_mp4?file=/output/south-screen.mp4"
    for keep in ("/output/north-screen.jpg", "https://cdn.example/x.jpg", None):
        assert publisher._same_origin(keep) == keep
