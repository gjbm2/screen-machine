"""The pictures a generation API request gives (routes/api_v1): each is base64 (`data`, plain or a data: URI), a public
web address (`url`), or an output of one of the key's own generations (`generation` + `output`). They come out as the
generator page's uploads do ({name, image}: a JPEG, base64, routes/utils.encode_image_uploads).

A `url` is fetched only from the public internet: http(s), every address its name resolves to public, no redirects
followed, 20 MB at most. So a key can't make this server read the house network (the TVs, the router, Spectre)."""
import base64
import binascii
import io
import ipaddress
import re
import socket
from pathlib import Path
from urllib.parse import urlparse

import requests
from werkzeug.datastructures import FileStorage

MAX_IMAGES = 4
MAX_BYTES = 20 * 1024 * 1024
FETCH_S = 15
_DATA_URI = re.compile(r"^data:image/[a-z0-9.+-]+;base64,", re.I)


class Refused(ValueError):
    """A picture the request can't have: (code, message)."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _public_host(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise Refused("image_unreachable", f"{host} does not resolve") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise Refused("image_not_public", f"{host} is not on the public internet")


def fetch(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise Refused("bad_image", "an image url is http(s)://…")
    _public_host(parsed.hostname)
    try:
        r = requests.get(url, timeout=FETCH_S, stream=True, allow_redirects=False,
                         headers={"User-Agent": "screen-machine-api/1"})
    except requests.RequestException as e:
        raise Refused("image_unreachable", f"could not fetch {url}: {e.__class__.__name__}") from None
    if r.is_redirect:
        raise Refused("image_unreachable", f"{url} redirects; give the address it redirects to")
    if r.status_code != 200:
        raise Refused("image_unreachable", f"{url} answered {r.status_code}")
    out = io.BytesIO()
    for chunk in r.iter_content(64 * 1024):
        out.write(chunk)
        if out.tell() > MAX_BYTES:
            raise Refused("image_too_large", f"{url} is over {MAX_BYTES // 2**20} MB")
    return out.getvalue()


def _decode(data):
    data = _DATA_URI.sub("", data.strip(), count=1)
    if len(data) > MAX_BYTES * 4 // 3 + 4:
        raise Refused("image_too_large", f"an image is over {MAX_BYTES // 2**20} MB")
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise Refused("bad_image", "an image's data is not base64") from None


def read(items, own_output):
    """The request's `images` as [{name, image}], in order. own_output(generation_id, index) is the path of one of
    the key's own outputs, or None. Raises Refused."""
    from routes.utils import encode_image_uploads
    if items in (None, []):
        return []
    if not isinstance(items, list) or len(items) > MAX_IMAGES:
        raise Refused("bad_request", f"images is a list of up to {MAX_IMAGES}")
    files = []
    for i, item in enumerate(items):
        if isinstance(item, str):
            item = {"url": item} if item.startswith(("http://", "https://")) else {"data": item}
        if not isinstance(item, dict):
            raise Refused("bad_image", "each image is {data}, {url} or {generation, output}")
        name = re.sub(r"[^A-Za-z0-9._-]", "_", str(item.get("name") or f"image{i + 1}.jpg"))[:80]
        if item.get("data"):
            content = _decode(str(item["data"]))
        elif item.get("url"):
            content = fetch(str(item["url"]))
        elif item.get("generation"):
            path = own_output(str(item["generation"]), int(item.get("output") or 0))
            if not path:
                raise Refused("bad_image", f"no output {item.get('output') or 0} of {item['generation']} for this key")
            if Path(path).suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                raise Refused("bad_image", "only a picture (not a video) can be given back")
            content = Path(path).read_bytes()
        else:
            raise Refused("bad_image", "each image is {data}, {url} or {generation, output}")
        files.append(FileStorage(stream=io.BytesIO(content), filename=name))
    try:
        return encode_image_uploads(files, max_file_size_mb=5)
    except ValueError as e:
        raise Refused("bad_image", str(e)) from None
