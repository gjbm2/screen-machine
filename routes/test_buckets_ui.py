"""test_buckets_ui.py — dev-only HTML harness for the bucket API
Mounts at /test-buckets   (blueprint name: test_buckets_bp)
"""

from __future__ import annotations

from pathlib import Path
import requests
from flask import Blueprint, render_template_string, request, redirect, url_for, send_from_directory
from werkzeug.utils import secure_filename

from utils.logger import info, error
from routes.bucket_api import (
    BASE_OUTPUT, ALLOWED_EXT,
    bucket_path, load_meta, save_meta,
    publish_path, unique_name,
)

test_buckets_bp = Blueprint("test_buckets", __name__, url_prefix="/test-buckets")

# --------------------------------------------------------------------------- #
#  HTML templates                                                             #
# --------------------------------------------------------------------------- #
LIST_HTML = """<!doctype html>
<title>Buckets</title>
<h1>Buckets</h1>
<ul>
{% for b in buckets %}
  <li><a href="{{ url_for('test_buckets.bucket_detail', bucket=b) }}">{{ b }}</a></li>
{% else %}
  <li>(no buckets yet)</li>
{% endfor %}
</ul>

<h2>Create new bucket</h2>
<form method="post">
  <input name="bucket_name" required placeholder="bucket-name">
  <button>Create</button>
</form>
"""

DETAIL_HTML = """<!doctype html>
<title>{{ bucket }}</title>
<h1>Bucket: {{ bucket }}</h1>
{% if published %}
  <p><strong>Currently published:</strong>
     <a href="{{ info_resp.raw_url }}" target="_blank">{{ published }}</a>
     {% if published_at %}<br><small>Published at {{ published_at }}</small>{% endif %}
  </p>
  {% if published_thumb %}
    <img src="data:image/jpeg;base64,{{ published_thumb }}"
         alt="Current preview"
         style="width:128px;height:128px;object-fit:cover;">
  {% endif %}
{% endif %}

<h2>Upload</h2>
<form method="post" enctype="multipart/form-data">
  <input type="file" name="file" required>
  <button>Upload</button>
</form>

<h2>Items</h2>
<ol>
{% for fn in sequence %}
  <li>
      <!-- thumbnail -->
      <img src="{{ url_for('test_buckets.thumbnail', bucket=bucket, filename=fn) }}"
           alt="thumb" style="width:64px;height:64px;vertical-align:middle;margin-right:8px;">
      [{{ loop.index0 }}]
      <a href="/api/buckets/{{ bucket }}/raw/{{ fn }}" target="_blank">{{ fn }}</a>
      <a href="/api/buckets/{{ bucket }}/raw/{{ fn }}.json" target="_blank">(json)</a>
      {% if fn in favorites %}★{% endif %}
      <form style="display:inline" method="post">
        <input type="hidden" name="filename" value="{{ fn }}">
        <button name="action" value="publish">Publish</button>
        {% if fn in favorites %}
          <button name="action" value="unfavorite">Unfavorite</button>
        {% else %}
          <button name="action" value="favorite">Favorite</button>
        {% endif %}
        <button name="action" value="delete">Delete</button>
        <button name="action" value="move-up">▲</button>
        <button name="action" value="move-down">▼</button>
        <select name="dest_bucket">
          {% for b in buckets if b != bucket %}<option value="{{ b }}">{{ b }}</option>{% endfor %}
        </select>
        <button name="action" value="copy">Add&nbsp;to…</button>
      </form>
  </li>
{% else %}
  <li>(bucket empty)</li>
{% endfor %}
</ol>

<hr>
<h3>Bucket maintenance</h3>
<form method="post">
  <button name="maintenance" value="purge">Purge non-favourites</button>
  <button name="maintenance" value="reindex">Re-index</button>
  <button name="maintenance" value="extract">Extract JSON</button>
</form>

<p><a href="{{ url_for('test_buckets.list_buckets') }}">← all buckets</a></p>
"""

# --------------------------------------------------------------------------- #
#  Routes                                                                     #
# --------------------------------------------------------------------------- #
@test_buckets_bp.route("/", methods=["GET", "POST"])
def list_buckets():
    if request.method == "POST":
        name = request.form.get("bucket_name", "").strip()
        if not name:
            error("[UI] bucket name required")
        else:
            bucket_path(name).mkdir(parents=True, exist_ok=True)
            save_meta(name, {"sequence": [], "favorites": []})
            return redirect(url_for(".bucket_detail", bucket=name))

    buckets = [p.name for p in BASE_OUTPUT.iterdir() if p.is_dir()]
    return render_template_string(LIST_HTML, buckets=buckets)


@test_buckets_bp.route("/<bucket>/", methods=["GET", "POST"])
def bucket_detail(bucket: str):
    # Fetch the unified published-info JSON
    info_resp = requests.get(
        url_for("buckets.get_published", bucket=bucket, _external=True),
        timeout=5
    ).json()
    published = info_resp.get("published")
    published_at = info_resp.get("published_at")
    published_thumb = info_resp.get("thumbnail")

    meta = load_meta(bucket)
    seq = meta.get("sequence", [])
    favs = set(meta.get("favorites", []))
    buckets = [p.name for p in BASE_OUTPUT.iterdir() if p.is_dir()]

    # Maintenance (purge, reindex, extract)
    if request.method == "POST" and "maintenance" in request.form:
        job = request.form["maintenance"]
        endpoint, method = {
            "purge": ("purge_bucket", "DELETE"),
            "reindex": ("reindex_bucket", "POST"),
            "extract": ("extract_json", "POST"),
        }[job]
        url = url_for(f"buckets.{endpoint}", bucket=bucket, _external=True)
        if method == "DELETE":
            requests.delete(url, timeout=10)
        else:
            requests.post(url, timeout=10)
        info(f"[UI] ran maintenance: {job}")
        return redirect(request.url)

    # Upload or item actions
    if request.method == "POST" and "maintenance" not in request.form:
        if "file" in request.files:
            f = request.files["file"]
            if f.filename and Path(f.filename).suffix.lower() in ALLOWED_EXT:
                try:
                    upload_url = url_for("buckets.upload", bucket=bucket, _external=True)
                    resp = requests.post(upload_url, files={"file": (f.filename, f.stream)})
                    if resp.status_code != 200:
                        error(f"[UI] Upload failed: {resp.status_code}")
                except Exception as e:
                    error(f"[UI] Upload proxy failed: {e}")
            else:
                error("[UI] invalid file type")
            return redirect(request.url)

        # Other actions
        action = request.form.get("action")
        fname = request.form.get("filename", "")
        if not fname:
            error("[UI] missing filename")
            return redirect(request.url)

        try:
            if action == "publish":
                requests.post(url_for("buckets.publish", bucket=bucket,
                                      filename=fname, _external=True), timeout=5)

            elif action == "favorite":
                requests.post(url_for("buckets.favorite", bucket=bucket,
                                      filename=fname, _external=True), timeout=5)

            elif action == "unfavorite":
                requests.delete(url_for("buckets.unfavorite", bucket=bucket,
                                        filename=fname, _external=True), timeout=5)

            elif action == "delete":
                requests.delete(url_for("buckets.delete", bucket=bucket,
                                        filename=fname, _external=True), timeout=5)

            elif action == "move-up":
                requests.post(url_for("buckets.move_up", bucket=bucket,
                                      filename=fname, _external=True), timeout=5)

            elif action == "move-down":
                requests.post(url_for("buckets.move_down", bucket=bucket,
                                      filename=fname, _external=True), timeout=5)

            elif action == "copy":
                dest = request.form.get("dest_bucket")
                if dest and dest != bucket:
                    requests.post(url_for("buckets.move", _external=True),
                                  json={
                                      "source_bucket": bucket,
                                      "dest_bucket": dest,
                                      "filename": fname,
                                      "copy": True
                                  }, timeout=5)

            else:
                error(f"[UI] unknown action: {action}")

        except Exception as e:
            error(f"[UI] Action '{action}' failed: {e}")

        return redirect(request.url)

    return render_template_string(
        DETAIL_HTML,
        bucket=bucket,
        sequence=seq,
        favorites=favs,
        published=published,
        published_at=published_at,
        buckets=buckets,
        info_resp=info_resp,
        published_thumb=published_thumb
    )

@test_buckets_bp.route("/<bucket>/thumbnail/<filename>")
def thumbnail(bucket: str, filename: str):
    """Serve the 256×256 thumbnail for a given media filename."""
    thumb_dir = bucket_path(bucket) / "thumbnails"
    thumb_name = f"{Path(filename).stem}.jpg"
    return send_from_directory(thumb_dir, thumb_name)
