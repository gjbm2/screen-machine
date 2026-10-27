"""Art tags: the HTTP routes (contract §2; docs/ART_TAGS.md). Registered under the app's /api prefix, open like the
other bucket routes."""
from __future__ import annotations

import os
import re

from flask import Blueprint, abort, jsonify, request

import routes.art_tags as art_tags
from routes.art_tags import lexicon, sidecar

art_tags_api = Blueprint("art_tags", __name__)

_NAME_RX = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\- ]{0,127}$")
WORDS_MAX = 20               # per add or remove list


def _media(bucket: str, filename: str):
    """The bucket file's path; 404 unless it is a media file directly inside output/<bucket>/."""
    if not _NAME_RX.match(bucket or "") or ".." in bucket or not _NAME_RX.match(filename or "") or ".." in filename:
        abort(404, "no such file")
    if os.path.splitext(filename)[1].lower() not in sidecar.MEDIA_EXT:
        abort(404, "no such file")
    root = os.path.realpath(sidecar.output_base() / bucket)
    p = sidecar.media_path(bucket, filename)
    real = os.path.realpath(p)
    if os.path.dirname(real) != root or not os.path.isfile(real):
        abort(404, "no such file")
    return p


def _words(v):
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > WORDS_MAX or not all(isinstance(x, str) for x in v):
        abort(400, f"add/remove: lists of at most {WORDS_MAX} words")
    return v


@art_tags_api.route("/art-tags/lexicon", methods=["GET"])
def get_lexicon():
    try:
        lex = lexicon.load()
    except ValueError as e:
        return jsonify({"error": str(e)}), 503
    return jsonify({"version": lex["version"],
                    "facets": {f: [{"word": w, "desc": d} for w, d in lex["facets"][f].items()]
                               for f in lexicon.FACETS}})


@art_tags_api.route("/art-tags/status", methods=["GET"])
def get_status():
    return jsonify(art_tags.get_worker().snapshot())


@art_tags_api.route("/art-tags/sweep", methods=["POST"])
def post_sweep():
    return jsonify({"queued": art_tags.get_worker().sweep()})


@art_tags_api.route("/buckets/<bucket>/tags/<filename>", methods=["POST"])
def post_tags(bucket, filename):
    p = _media(bucket, filename)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        abort(400, 'body: {"add": [...], "remove": [...]}')
    add, remove = _words(body.get("add")), _words(body.get("remove"))
    try:
        d = sidecar.update(p, lambda d: sidecar.apply_edits(d, add, remove))
    except ValueError:
        abort(409, "the picture's sidecar isn't readable JSON")
    return jsonify({"tags": d.get("tags") or [], "tagging": d.get("tagging") or {}})


@art_tags_api.route("/buckets/<bucket>/tags/<filename>/retag", methods=["POST"])
def post_retag(bucket, filename):
    _media(bucket, filename)
    w = art_tags.get_worker()
    queued = w.enqueue(bucket, filename, front=True, retag=True) or (bucket, filename) in w.queued
    return jsonify({"queued": bool(queued)})       # false only for a bucket that is never tagged (_recent)
