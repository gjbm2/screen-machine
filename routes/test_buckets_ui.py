"""test_buckets_ui.py — dev-only HTML harness for the bucket API
Mounts at /test-buckets   (blueprint name: test_buckets_bp)
"""

from __future__ import annotations

from pathlib import Path
import requests
from flask import Blueprint, render_template_string, request, redirect, url_for, jsonify
from werkzeug.utils import secure_filename

from utils.logger import info, error
from routes.bucket_api import buckets_bp

test_buckets_bp = Blueprint("test_buckets", __name__, url_prefix="/test-buckets")

# --------------------------------------------------------------------------- #
#  HTML templates                                                             #
# --------------------------------------------------------------------------- #
LIST_HTML = """<!doctype html>
<title>Buckets</title>
<h1>Buckets</h1>

<h2>Global Maintenance</h2>
<form method="post">
  <input type="hidden" name="maintenance" value="reindex_all">
  <label><input type="checkbox" name="rebuild_all_sidecars" value="true"> Rebuild all sidecars</label>
  <label><input type="checkbox" name="rebuild_all_thumbs" value="true"> Rebuild all thumbnails</label>
  <button>Reindex All Buckets</button>
</form>

<hr>

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
     <a href="{{ info_resp.raw_url }}" target="_blank">{{ published.filename }}</a>
     {% if published.published_at %}<br><small>Published at {{ published.published_at }}</small>{% endif %}
  </p>
  {% if published.thumbnail_url %}
    <img src="{{ published.thumbnail_url }}"
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
{% for file in files %}
  <li>
      {% if file.thumbnail_url %}
        <img src="{{ file.thumbnail_url }}"
             alt="thumb" style="width:64px;height:64px;vertical-align:middle;margin-right:8px;">
      {% endif %}
      [{{ file.sequence_index }}]
      <a href="/api/buckets/{{ bucket }}/raw/{{ file.filename }}" target="_blank">{{ file.filename }}</a>
      <a href="/api/buckets/{{ bucket }}/raw/{{ file.filename }}.json" target="_blank">(json)</a>
      {% if file.favorite %}★{% endif %}
      <form style="display:inline" method="post">
        <input type="hidden" name="filename" value="{{ file.filename }}">
        <button name="action" value="publish">Publish</button>
        {% if file.favorite %}
          <button name="action" value="unfavorite">Unfavorite</button>
        {% else %}
          <button name="action" value="favorite">Favorite</button>
        {% endif %}
        <button name="action" value="delete">Delete</button>
        <button name="action" value="move-up">▲</button>
        <button name="action" value="move-down">▼</button>
        <select name="move_to">
          <option value="">Move to top</option>
          {% for other_file in files %}
            {% if other_file.filename != file.filename %}
              <option value="{{ other_file.filename }}">After {{ other_file.filename }}</option>
            {% endif %}
          {% endfor %}
        </select>
        <button name="action" value="move-to">Move</button>
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
  <div>
    <button name="maintenance" value="reindex_single">Re-index</button>
    <label><input type="checkbox" name="rebuild_all_sidecars" value="true"> Rebuild all sidecars</label>
    <label><input type="checkbox" name="rebuild_all_thumbs" value="true"> Rebuild all thumbnails</label>
  </div>
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
        if "maintenance" in request.form:
            # Handle global reindex
            if request.form["maintenance"] == "reindex_all":
                rebuild_all_sidecars = request.form.get("rebuild_all_sidecars", "false").lower() == "true"
                rebuild_all_thumbs = request.form.get("rebuild_all_thumbs", "false").lower() == "true"
                url = url_for("buckets.reindex_all", _external=True)
                requests.post(url, params={
                    "rebuild_all_sidecars": str(rebuild_all_sidecars).lower(),
                    "rebuild_all_thumbs": str(rebuild_all_thumbs).lower()
                }, timeout=30)
                return redirect(request.url)
        else:
            # Handle bucket creation
            name = request.form.get("bucket_name", "").strip()
            if not name:
                error("[UI] bucket name required")
            else:
                requests.post(
                    url_for("buckets.create_bucket", _external=True),
                    json={"bucket_id": name}
                )
                return redirect(url_for(".bucket_detail", bucket=name))

    # Use API to list buckets
    buckets = requests.get(url_for("buckets.list_buckets", _external=True)).json()
    return render_template_string(LIST_HTML, buckets=buckets)


@test_buckets_bp.route("/<bucket>/", methods=["GET", "POST"])
def bucket_detail(bucket: str):
    # Get bucket details from API
    bucket_details = requests.get(
        url_for("buckets.get_bucket_complete", bucket_id=bucket, _external=True),
        timeout=5
    ).json()
    files = bucket_details.get("files", [])
    buckets = requests.get(url_for("buckets.list_buckets", _external=True)).json()
    published = bucket_details.get("published", {})

    # Maintenance (purge, reindex, extract)
    if request.method == "POST" and "maintenance" in request.form:
        job = request.form["maintenance"]
        job_endpoints = {
            "reindex_single": "reindex_single",
            "purge": "purge_bucket_endpoint",
            "extract": "extract_json"
        }
        endpoint = job_endpoints[job]
        url = url_for(f"buckets.{endpoint}", bucket_id=bucket, _external=True)
        
        if job == "purge":
            requests.delete(url, timeout=10)
        elif job == "reindex_single":
            rebuild_all_sidecars = request.form.get("rebuild_all_sidecars", "false").lower() == "true"
            rebuild_all_thumbs = request.form.get("rebuild_all_thumbs", "false").lower() == "true"
            requests.post(url, params={
                "rebuild_all_sidecars": str(rebuild_all_sidecars).lower(),
                "rebuild_all_thumbs": str(rebuild_all_thumbs).lower()
            }, timeout=30)
        else:
            requests.post(url, timeout=10)
        return redirect(request.url)

    # Upload or item actions
    if request.method == "POST" and "maintenance" not in request.form:
        if "file" in request.files:
            f = request.files["file"]
            if f.filename:
                upload_url = url_for("buckets.upload", bucket_id=bucket, _external=True)
                requests.post(upload_url, files={"file": (f.filename, f.stream)})
            return redirect(request.url)

        # Other actions
        action = request.form.get("action")
        fname = request.form.get("filename", "")
        if action == "publish":
            requests.post(url_for("buckets.publish", bucket_id=bucket,
                                filename=fname, _external=True), timeout=5)
        elif action == "favorite":
            requests.post(url_for("buckets.favorite", bucket_id=bucket,
                                filename=fname, _external=True), timeout=5)
        elif action == "unfavorite":
            requests.delete(url_for("buckets.unfavorite", bucket_id=bucket,
                                  filename=fname, _external=True), timeout=5)
        elif action == "delete":
            requests.delete(url_for("buckets.delete_file", bucket_id=bucket,
                                  filename=fname, _external=True), timeout=5)
        elif action == "move-up":
            requests.post(url_for("buckets.move_up", bucket_id=bucket,
                                filename=fname, _external=True), timeout=5)
        elif action == "move-down":
            requests.post(url_for("buckets.move_down", bucket_id=bucket,
                                filename=fname, _external=True), timeout=5)
        elif action == "move-to":
            insert_after = request.form.get("move_to", "")
            requests.post(
                url_for("buckets.move_to", bucket_id=bucket, filename=fname, _external=True),
                json={"insert_after": insert_after if insert_after else None},
                timeout=5
            )
        elif action == "copy":
            dest = request.form.get("dest_bucket")
            if dest and dest != bucket:
                response = requests.post(
                    url_for("buckets.add_image_to_new_bucket", _external=True),
                    json={
                        "source_publish_destination": bucket,
                        "target_publish_destination": dest,
                        "filename": fname
                    },
                    timeout=5
                )
                if not response.ok:
                    error(f"[UI] Failed to copy file: {response.text}")
            return redirect(request.url)
        elif action in ["publish", "favorite", "unfavorite", "delete", "move-up", "move-down", "move-to"]:
            return redirect(request.url)

    return render_template_string(
        DETAIL_HTML,
        bucket=bucket,
        buckets=buckets,
        files=files,
        published=published,
        info_resp={"raw_url": published.get("raw_url")} if published else {}
    )

