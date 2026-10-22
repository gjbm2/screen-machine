#!/usr/bin/env python3
"""Stage the ragtime / stride / novelty shelf of the piano MIDI library.

One best take per title, chosen in this order of take:
    performance (live MIDI-keyboard capture) > transcription (of a recording) > roll > sequenced.

Sources (keys used in WORKS below):
  rtp     Ragtime Press: Sue Keller's live weighted-keyboard performances (www.rtpress.com, Wayback for 404s)
  roache  John Roache's Ragtime MIDI Library (johnroachemusic.com via Wayback; the mono "_m" file of each zip)
  pcr     PianoCoRe v1.0 (SyMuPe): canonical titles over PERiScoPe / GiantMIDI transcriptions; one file
          range-read out of the Zenodo raw-midi zip.  "pcra" = the same index, Aria-MIDI rows only.
  gm      GiantMIDI-Piano v1.2 (ByteDance): YouTube transcriptions; one file range-read out of the Drive zip
  kdf     Kunstderfuge (direct /-/mid.files/ paths): Alessandro Simonetto's real-time Joplin performances
          ('me/...(c)simonetto') and Terry Smythe's piano-roll scans ('rolls/...')
  rtm     Colin D. MacDonald's ragtimemusic.com Joplin sequences (gap fillers only)

Output (an assembler step cleans the files and builds the catalogue):
  ~/.local/share/screen-machine/midi-library/_staging/ragtime/{files/*.mid, manifest.json, SOURCES.md}
Downloads are cached under ~/.local/share/screen-machine/midi-library/_cache/ragtime/, so a re-run is
resumable and fetches only what is missing.  Polite: >= 1.2 s between requests to a small site, 75 s to the
Wayback Machine (which is only used in a second pass, and waited out for 5 minutes when it answers 429),
retries with backoff, a browser User-Agent.

Run:  nice -n 19 ~/.venvs/sm-musiclib/bin/python -I tools/music_library/fetch_ragtime.py [--only SUBSTR] [--dry-run]
Needs mido (and pyarrow only if the PianoCoRe index has to be rebuilt).
"""
import argparse
import csv
import io
import json
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib

ROOT = os.path.expanduser('~/.local/share/screen-machine/midi-library')
KEY = 'ragtime'
STAGE = os.path.join(ROOT, '_staging', KEY)
FILES = os.path.join(STAGE, 'files')
CACHE = os.path.join(ROOT, '_cache', KEY)

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/130.0 Safari/537.36')
HOST_DELAY = {'web.archive.org': 75.0, 'drive.usercontent.google.com': 0.3, 'zenodo.org': 0.5,
              'huggingface.co': 0.3, 'cas-bridge.xethub.hf.co': 0.3}
DEFAULT_DELAY = 1.2
BYTES = {'n': 0}
_last = {}


class NotFound(Exception):
    pass


class Deferred(Exception):
    """A Wayback fetch postponed to the second pass (the archive rate-limits hard, so everything else goes first)."""


class WaybackDown(Exception):
    """The Wayback Machine kept this IP blocked (HTTP 429) for longer than --wayback-wait-min."""


WAYBACK = {'enabled': False, 'down': False, 'blocked_since': None, 'give_up_s': 3600.0}
WAYBACK_FALLBACK = []  # works staged with a lesser take because the archive was blocked; re-run later


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------------------------- http
def _wait(host):
    d = HOST_DELAY.get(host, DEFAULT_DELAY)
    t = _last.get(host, 0) + d - time.time()
    if t > 0:
        time.sleep(t)
    _last[host] = time.time()


def http(url, rng=None, tries=7, timeout=90):
    """GET url (optionally a byte range). Returns (status, bytes, headers). Raises NotFound on 404/410."""
    host = urllib.parse.urlsplit(url).hostname or ''
    if host == 'web.archive.org':
        tries = max(tries, 14)  # its per-IP block lasts minutes; wait it out rather than demote the take
    hdr = {'User-Agent': UA, 'Accept': '*/*'}
    if rng:
        hdr['Range'] = 'bytes=%d-%d' % rng
    delay = 5.0
    for i in range(tries):
        if host == 'web.archive.org' and WAYBACK['down']:
            raise WaybackDown(url)
        _wait(host)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=timeout) as r:
                data = r.read()
                BYTES['n'] += len(data)
                if host == 'web.archive.org':
                    WAYBACK['blocked_since'] = None
                return r.status, data, r.headers
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                raise NotFound(url)
            if e.code in (429, 500, 502, 503, 504, 520, 522, 524) and i < tries - 1:
                ra = e.headers.get('Retry-After') if e.headers else None
                w = float(ra) if ra and ra.isdigit() else delay
                if e.code == 429 and host == 'web.archive.org':
                    w = 300.0  # blocked per IP; retrying sooner only extends the block
                    WAYBACK['blocked_since'] = WAYBACK['blocked_since'] or time.time()
                    if time.time() - WAYBACK['blocked_since'] > WAYBACK['give_up_s']:
                        WAYBACK['down'] = True
                        log('  Wayback Machine still blocking this IP; falling back to other takes for now')
                        raise WaybackDown(url)
                log(f'  HTTP {e.code} {url[:90]} ; retry in {w:.0f}s')
                time.sleep(min(w, 300))
                delay = min(delay * 2, 180)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            if i < tries - 1:
                log(f'  {type(e).__name__} {e} {url[:90]} ; retry in {delay:.0f}s')
                time.sleep(delay)
                delay = min(delay * 2, 180)
                continue
            raise
    raise RuntimeError('unreachable')


def cpath(*parts):
    p = os.path.join(CACHE, *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def safe_name(s):
    return re.sub(r'[^A-Za-z0-9._-]+', '_', s).strip('_')


def write_atomic(path, data):
    tmp = path + '.part'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def cached_get(url, path, validate=None):
    """Download url to path once. A cached '.404' marker remembers a missing file."""
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return open(path, 'rb').read()
    if os.path.exists(path + '.404'):
        raise NotFound(url)
    try:
        _, data, _ = http(url)
    except NotFound:
        open(path + '.404', 'w').close()
        raise
    if validate and not validate(data):
        raise ValueError(f'bad content from {url}')
    write_atomic(path, data)
    return data


def is_midi(b):
    return b[:4] == b'MThd' or (b[:4] == b'RIFF' and b[8:12] == b'RMID')


def is_zip(b):
    return b[:2] == b'PK'


# ------------------------------------------------------------------------------------------ wayback
def cdx(prefix, name):
    """Wayback CDX rows (original, timestamp, status, length) for a URL prefix; cached as JSON."""
    p = cpath('wayback', f'cdx_{name}.json')
    if os.path.exists(p):
        return json.load(open(p))
    if not WAYBACK['enabled']:
        raise Deferred(prefix)
    q = urllib.parse.urlencode({'url': prefix, 'matchType': 'prefix', 'output': 'json',
                                'fl': 'original,timestamp,statuscode,length', 'limit': '20000'})
    _, data, _ = http('http://web.archive.org/cdx/search/cdx?' + q)
    rows = json.loads(data.decode('utf-8') or '[]')
    rows = [r for r in rows[1:]] if rows and rows[0][0] == 'original' else rows
    json.dump(rows, open(p, 'w'))
    return rows


def wayback_fetch(original, timestamps, path, validate):
    """Fetch one archived file, trying each capture until one validates (Wayback sometimes serves an
    empty body or a 'Temporarily Offline' page)."""
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return open(path, 'rb').read()
    if not WAYBACK['enabled']:
        raise Deferred(original)
    last = None
    for ts in timestamps:
        url = f'http://web.archive.org/web/{ts}id_/{original}'
        for attempt in range(3):
            try:
                _, data, _ = http(url)
            except NotFound as e:
                last = e
                break
            except WaybackDown:
                raise
            except Exception as e:  # noqa: BLE001 - keep trying other captures
                last = e
                break
            if data and validate(data):
                write_atomic(path, data)
                return data
            last = ValueError(f'invalid body ({len(data)} B) {url}')
            if b'Temporarily Offline' in data[:2000]:
                log('  wayback offline; waiting 60s')
                time.sleep(60)
                continue
            break
    raise NotFound(f'{original}: {last}')


# ------------------------------------------------------------------------------- remote zip by range
def rz_size(url):
    _, _, h = http(url, rng=(0, 0))
    return int(h['Content-Range'].split('/')[-1])


def rz_central(url, cache_json, name_filter=None):
    """Read a remote zip's central directory with Range requests -> [(name, method, csize, usize, offset)].
    The (optionally filtered) listing is cached so a re-run does not fetch the directory again."""
    if os.path.exists(cache_json):
        return json.load(open(cache_json))
    n = rz_size(url)
    tail_len = min(n, 65536 + 22)
    _, tail, _ = http(url, rng=(n - tail_len, n - 1))
    i = tail.rfind(b'PK\x05\x06')
    cd_entries = struct.unpack('<H', tail[i + 10:i + 12])[0]
    cd_size, cd_off = struct.unpack('<II', tail[i + 12:i + 20])
    if cd_off == 0xFFFFFFFF or cd_entries == 0xFFFF or cd_size == 0xFFFFFFFF:
        j = tail.rfind(b'PK\x06\x07')
        z64off = struct.unpack('<Q', tail[j + 8:j + 16])[0]
        _, rec, _ = http(url, rng=(z64off, z64off + 55))
        cd_size, cd_off = struct.unpack('<QQ', rec[40:56])
    log(f'  reading zip directory: {cd_size / 1e6:.1f} MB')
    chunks = []
    pos = cd_off
    while pos < cd_off + cd_size:
        end = min(cd_off + cd_size, pos + 16 * 1024 * 1024) - 1
        _, part, _ = http(url, rng=(pos, end), timeout=300)
        chunks.append(part)
        pos = end + 1
    cd = b''.join(chunks)
    out = []
    p = 0
    while p < len(cd) and cd[p:p + 4] == b'PK\x01\x02':
        meth, = struct.unpack('<H', cd[p + 10:p + 12])
        csz, usz = struct.unpack('<II', cd[p + 20:p + 28])
        nl, el, cl = struct.unpack('<HHH', cd[p + 28:p + 34])
        lho, = struct.unpack('<I', cd[p + 42:p + 46])
        name = cd[p + 46:p + 46 + nl].decode('utf-8', 'replace')
        extra = cd[p + 46 + nl:p + 46 + nl + el]
        if 0xFFFFFFFF in (csz, usz, lho):
            q = 0
            while q + 4 <= len(extra):
                hid, hl = struct.unpack('<HH', extra[q:q + 4])
                if hid == 1:
                    vals = list(struct.unpack('<' + 'Q' * (hl // 8), extra[q + 4:q + 4 + (hl // 8) * 8]))
                    k = 0
                    if usz == 0xFFFFFFFF:
                        usz = vals[k]; k += 1
                    if csz == 0xFFFFFFFF:
                        csz = vals[k]; k += 1
                    if lho == 0xFFFFFFFF:
                        lho = vals[k]; k += 1
                q += 4 + hl
        if name_filter is None or name_filter(name):
            out.append((name, meth, csz, usz, lho))
        p += 46 + nl + el + cl
    json.dump(out, open(cache_json, 'w'))
    return out


def rz_get(url, entry, path):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return open(path, 'rb').read()
    name, meth, csz, usz, lho = entry
    _, hdr, _ = http(url, rng=(lho, lho + 29))
    nl, el = struct.unpack('<HH', hdr[26:30])
    start = lho + 30 + nl + el
    _, data, _ = http(url, rng=(start, start + csz - 1))
    if meth == 8:
        data = zlib.decompress(data, -15)
    elif meth != 0:
        raise ValueError(f'zip method {meth} for {name}')
    write_atomic(path, data)
    return data


# --------------------------------------------------------------------- PKWARE "implode" (method 6)
class _Bits:
    def __init__(self, b):
        self.b, self.p, self.acc, self.n = b, 0, 0, 0

    def get(self, k):
        while self.n < k:
            byte = self.b[self.p] if self.p < len(self.b) else 0
            self.p += 1
            self.acc |= byte << self.n
            self.n += 8
        v = self.acc & ((1 << k) - 1)
        self.acc >>= k
        self.n -= k
        return v


def _sf_tree(data, pos, nsym):
    """Read one Shannon-Fano tree; returns (decode dict {(len, code): sym}, new pos)."""
    nbytes = data[pos] + 1
    pos += 1
    lens = []
    for _ in range(nbytes):
        b = data[pos]
        pos += 1
        lens += [(b & 15) + 1] * ((b >> 4) + 1)
    if len(lens) != nsym:
        raise ValueError('implode: bad tree')
    # PKWARE APPNOTE 5.3.6: stable-sort by bit length, walk from the longest, 16-bit codes, then
    # bit-reverse (the stream is read least-significant bit first).
    order = sorted(range(nsym), key=lambda s: lens[s])
    codes = {}
    code, inc, last = 0, 0, 0
    for s in reversed(order):
        code += inc
        if lens[s] != last:
            last = lens[s]
            inc = 1 << (16 - last)
        rev = int(format(code & 0xFFFF, '016b')[::-1], 2)
        codes[(lens[s], rev & ((1 << lens[s]) - 1))] = s
    return codes, pos, max(lens)


def _sf_decode(bits, codes, maxlen):
    v = 0
    for ln in range(1, maxlen + 1):
        v |= bits.get(1) << (ln - 1)
        s = codes.get((ln, v))
        if s is not None:
            return s
    raise ValueError('implode: bad code')


def explode(data, flags, usize):
    """Decompress a zip member stored with method 6 (Implode)."""
    big_dict = bool(flags & 2)
    lit_tree = bool(flags & 4)
    pos = 0
    if lit_tree:
        lt, pos, lmax = _sf_tree(data, pos, 256)
    ln_t, pos, lnmax = _sf_tree(data, pos, 64)
    ds_t, pos, dmax = _sf_tree(data, pos, 64)
    bits = _Bits(data[pos:])
    dbits = 7 if big_dict else 6
    minlen = 3 if lit_tree else 2
    out = bytearray()
    while len(out) < usize:
        if bits.get(1):
            if lit_tree:
                out.append(_sf_decode(bits, lt, lmax))
            else:
                out.append(bits.get(8))
        else:
            low = bits.get(dbits)
            dist = (_sf_decode(bits, ds_t, dmax) << dbits | low) + 1
            ln = _sf_decode(bits, ln_t, lnmax)
            if ln == 63:
                ln += bits.get(8)
            ln += minlen
            for _ in range(ln):
                out.append(out[-dist] if dist <= len(out) else 0)
    return bytes(out[:usize])


def unzip_members(zbytes):
    """{name: bytes} for every member, including Implode-compressed ones."""
    out = {}
    try:
        zf = zipfile.ZipFile(io.BytesIO(zbytes))
    except zipfile.BadZipFile:
        return out
    for zi in zf.infolist():
        if zi.is_dir():
            continue
        if zi.compress_type == 6:
            off = zi.header_offset
            nl, el = struct.unpack('<HH', zbytes[off + 26:off + 30])
            raw = zbytes[off + 30 + nl + el: off + 30 + nl + el + zi.compress_size]
            data = explode(raw, zi.flag_bits, zi.file_size)
            if zlib.crc32(data) & 0xFFFFFFFF != zi.CRC:
                log(f'  implode CRC mismatch for {zi.filename}')
                continue
            out[zi.filename] = data
        else:
            try:
                out[zi.filename] = zf.read(zi)
            except (NotImplementedError, zipfile.BadZipFile, RuntimeError) as e:
                log(f'  cannot extract {zi.filename}: {e}')
    return out


# ---------------------------------------------------------------------------------------- sources
def src_rtp(key):
    """Sue Keller / Ragtime Press. key = site path like 'mid/mud/maple_sk.mid'."""
    path = cpath('rtp', safe_name(key))
    url = 'https://www.rtpress.com/' + urllib.parse.quote(key)
    try:
        data = cached_get(url, path, validate=is_midi)
        ref = url
    except (NotFound, ValueError):
        rows = cdx('rtpress.com/mid/', 'rtpress')
        want = urllib.parse.unquote(key).lower()
        caps = [r for r in rows if r[2] == '200' and
                urllib.parse.unquote(urllib.parse.urlsplit(r[0]).path).lstrip('/').lower() == want]
        if not caps:
            raise NotFound(key)
        caps.sort(key=lambda r: r[1])
        data = wayback_fetch(caps[0][0], [c[1] for c in caps], path, is_midi)
        ref = f'http://web.archive.org/web/{caps[0][1]}/{caps[0][0]}'
    return data, {'source': 'Ragtime Press (Sue Keller)', 'source_ref': ref, 'performer': 'Sue Keller',
                  'licence': 'Copyright (c) Ragtime Press / Sue Keller, all rights reserved',
                  'take': 'performance'}


def src_roache(key):
    """John Roache via Wayback. key = zip stem under /midiragger/, e.g. 'mapleaf'."""
    rows = cdx('johnroachemusic.com/midiragger/', 'roache')
    caps = [r for r in rows if r[2] == '200' and r[0].lower().endswith('/midiragger/' + key.lower() + '.zip')]
    if not caps:
        raise NotFound(key)
    caps.sort(key=lambda r: r[1])
    zpath = cpath('roache', key + '.zip')
    zb = wayback_fetch(caps[0][0], [c[1] for c in caps], zpath, is_zip)
    members = unzip_members(zb)
    mids = {n: b for n, b in members.items() if n.lower().endswith('.mid') and is_midi(b)}
    if not mids:
        raise NotFound(f'roache {key}: no readable MIDI in zip')
    # each zip holds a stereo version (pan on every note) and a mono '_m' version; take the mono one
    pick = sorted(mids, key=lambda n: (not re.search(r'_m\.mid$', n, re.I), bool(re.search(r'_d\.mid$', n, re.I)), n))[0]
    txt = next((members[n] for n in members if n.lower().endswith('.txt')), b'')
    return mids[pick], {'source': "John Roache's Ragtime MIDI Library", 'performer': 'John Roache',
                        'source_ref': f'http://web.archive.org/web/{caps[0][1]}/{caps[0][0]}#{pick}',
                        'licence': 'Performance (c) 1995-1999 John E. Roache; non-commercial distribution '
                                   'only, in the original ZIP with its text file',
                        'take': 'performance', '_note': txt.decode('latin-1')[:4000]}


GM_ZIP = 'https://drive.usercontent.google.com/download?id=1BDEPaEWFEB2ADquS1VYp5iLZYVngw799&export=download&confirm=t'


def src_gm(key):
    """GiantMIDI-Piano v1.2 (midis_v1.2.zip). key = YouTube id."""
    ents = rz_central(GM_ZIP, cpath('giantmidi', 'midis_v1.2_listing.json'))
    ent = next((e for e in ents if e[0].endswith(', ' + key + '.mid')), None)
    if not ent:
        raise NotFound(f'giantmidi {key}')
    data = rz_get(GM_ZIP, ent, cpath('giantmidi', key + '.mid'))
    return data, {'source': 'GiantMIDI-Piano v1.2 (ByteDance)',
                  'source_ref': f'{os.path.basename(ent[0])} (https://www.youtube.com/watch?v={key})',
                  'licence': 'CC BY 4.0', 'take': 'transcription', 'performer': None}


# PianoCoRe ------------------------------------------------------------------------------------------
PCR_ZIP = 'https://zenodo.org/api/records/19186016/files/PianoCoRe-1.0-raw-midi.zip/content'
PCR_PARQUETS = [f'train-{i:05d}-of-00016' for i in range(16)] + ['test-00000-of-00002', 'test-00001-of-00002']
PCR_COLS = ['composer', 'composition', 'movement', 'performance_id', 'performance_dataset', 'performer',
            'quality_label', 'prob_high_quality', 'is_duplicate', 'tier_a_star', 'performance_duration',
            'performance_midi_path']
PCR_COMPOSERS = {'Scott Joplin': 'Joplin,_Scott', 'James Scott': 'Scott,_James',
                 'Charles L. Johnson': 'Johnson,_Charles_Leslie', 'James P. Johnson': 'Johnson,_James_P.',
                 'George Botsford': 'Botsford,_George', 'George Gershwin': 'Gershwin,_George',
                 'Robert Hampton': 'Hampton,_Robert', 'Jelly Roll Morton': 'Morton,_Jelly_Roll',
                 'Tom Turpin': 'Turpin,_Tom', 'Fats Waller': 'Waller,_Fats', 'Felix Arndt': 'Arndt,_Felix',
                 'Ernesto Nazareth': 'Nazareth,_Ernesto', 'Scott Joplin & Louis Chauvin': 'Joplin,_Scott',
                 'Scott Joplin & Scott Hayden': 'Joplin,_Scott', 'Scott Joplin & Arthur Marshall': 'Joplin,_Scott'}
_pcr_rows = None


def pcr_index():
    """PianoCoRe metadata rows for the composers above (cached CSV, built by Range-reading the metadata
    columns of the 18 Hugging Face parquet files, about 15 MB)."""
    global _pcr_rows
    if _pcr_rows is not None:
        return _pcr_rows
    p = cpath('pianocore', 'ragtime_rows.csv')
    if not os.path.exists(p):
        import pyarrow.parquet as pq  # only needed to rebuild the index
        comps = set(PCR_COMPOSERS.values())
        rows = []
        for name in PCR_PARQUETS:
            url = f'https://huggingface.co/datasets/SyMuPe/PianoCoRe/resolve/main/data/{name}.parquet'
            f = _HttpFile(url)
            t = pq.ParquetFile(f).read(columns=PCR_COLS)
            cols = {c: t.column(c).to_pylist() for c in PCR_COLS}
            for i in range(t.num_rows):
                if cols['composer'][i] in comps:
                    rows.append({c: cols[c][i] for c in PCR_COLS})
            log(f'  pianocore {name}: {len(rows)} rows so far')
        with open(p + '.part', 'w', newline='', encoding='utf-8') as fh:
            w = csv.DictWriter(fh, PCR_COLS)
            w.writeheader()
            w.writerows(rows)
        os.replace(p + '.part', p)
    _pcr_rows = list(csv.DictReader(open(p, encoding='utf-8')))
    return _pcr_rows


class _HttpFile(io.RawIOBase):
    def __init__(self, url):
        n = rz_size(url)
        self.url, self.size, self.pos = url, n, 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else (self.pos + off if whence == 1 else self.size + off)
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        if n == 0 or self.pos >= self.size:
            return b''
        end = min(self.size, self.pos + n) - 1
        _, data, _ = http(self.url, rng=(self.pos, end), timeout=300)
        self.pos += len(data)
        return data

    def readinto(self, b):
        d = self.read(len(b))
        b[:len(d)] = d
        return len(d)


def _tb(v):
    return str(v).strip().lower() in ('true', '1')


GOOD_PERFORMERS = ('rifkin', 'arpin', 'morath', 'hyman', 'dyson', 'colombo', 'peskanov', 'connell', 'zimerman',
                   'bolcom', 'albright', 'kitamura', 'pinto', 'hamelin', 'brier', 'tichenor', 'egan', 'biller',
                   'ammons', 'hall', 'barton', 'jarrett', 'gibbons', 'andre previn', 'previn', 'labeque')
BAD_PERFORMERS = ('band', 'orchestra', 'ensemble', 'quartet', 'trio', 'sound', 'relax', 'sleep', 'music for',
                  'lullaby', 'karaoke', 'kids', 'baby', 'study', 'spa', 'meditation')


def pcr_pick(composer, composition, movement='', mode='pcr'):
    """Best PianoCoRe row for a work: high-quality, not a duplicate; mode 'pcr' = PERiScoPe or GiantMIDI
    rows (Transkun V2 / Kong transcriptions with full velocity), 'pcra' = Aria-MIDI rows."""
    rows = [r for r in pcr_index() if r['composer'] == composer and r['composition'] == composition
            and (r['movement'] or '') == (movement or '') and r['quality_label'] == 'high quality'
            and not _tb(r['is_duplicate'])]
    if mode == 'pcr':
        rows = [r for r in rows if r['performance_dataset'] in ('PERiScoPe', 'GiantMIDI-Piano')]
    else:
        rows = [r for r in rows if r['performance_dataset'] == 'Aria-MIDI']
    if not rows:
        return None
    durs = sorted(float(r['performance_duration'] or 0) for r in rows)
    med = durs[len(durs) // 2]
    comp_name = composer.split(',_')[0].lower()

    def score(r):
        perf = (r['performer'] or '').strip().lower()
        d = float(r['performance_duration'] or 0)
        s = float(r['prob_high_quality'] or 0)
        s += 0.3 if _tb(r['tier_a_star']) else 0
        s += 0.15 if r['performance_dataset'] == 'PERiScoPe' else 0
        s += 0.25 if any(g in perf for g in GOOD_PERFORMERS) else 0
        s -= 0.6 if any(b in perf for b in BAD_PERFORMERS) else 0
        s -= 0.1 if (not perf or comp_name in perf) else 0
        s -= 0.2 if '_mini' in r['performance_midi_path'] else 0
        s -= 0.5 if med and not (0.6 * med <= d <= 1.6 * med) else 0
        s -= 0.5 if d < 45 else 0
        return s
    rows.sort(key=score, reverse=True)
    return rows[0]


def src_pcr(key, work, mode='pcr'):
    if '|' in key:
        composer, rest = key.split('|', 1)
    else:
        composer, rest = PCR_COMPOSERS[work['composer']], key
    composition, _, movement = rest.partition('#')
    r = pcr_pick(composer, composition, movement, mode)
    if not r:
        raise NotFound(f'pianocore {composer}/{composition} ({mode})')
    mp = r['performance_midi_path']
    ents = rz_central(PCR_ZIP, cpath('pianocore', 'raw_zip_listing_ragtime.json'),
                      name_filter=lambda n: any(c + '/' in n for c in set(PCR_COMPOSERS.values())))
    ent = next((e for e in ents if e[0].endswith(mp)), None)
    if not ent:
        raise NotFound(f'pianocore zip has no {mp}')
    data = rz_get(PCR_ZIP, ent, cpath('pianocore', safe_name(mp)))
    perf = (r['performer'] or '').strip() or None
    if perf and composer.split(',_')[0].lower() in perf.lower():
        perf = None  # streaming 'artist' credit = the composer's name, not the pianist
    ds = r['performance_dataset']
    return data, {'source': f'PianoCoRe v1.0 ({ds})',
                  'source_ref': f"{r['performance_id']} ({mp})",
                  'licence': 'CC BY-NC-SA 4.0', 'take': 'transcription', 'performer': perf}


KDF = 'https://kunstderfuge.com/-/mid.files/'


def src_kdf(key):
    data = cached_get(KDF + urllib.parse.quote(key, safe="/()&'_-."), cpath('kdf', safe_name(key)),
                      validate=is_midi)
    ref = 'https://kunstderfuge.com/-/midi.asp?file=' + key
    if '(c)simonetto' in key:  # Alessandro Simonetto's real-time keyboard performances (text: 'performance')
        return data, {'source': 'Kunstderfuge (Alessandro Simonetto performances)', 'source_ref': ref,
                      'licence': '(c) Alessandro Simonetto / onclassical; all rights reserved, no commercial use',
                      'take': 'performance', 'performer': 'Alessandro Simonetto'}
    return data, {'source': 'Kunstderfuge (Terry Smythe piano-roll scans)', 'source_ref': ref,
                  'licence': 'Terry Smythe roll scan: may not be used for commercial purposes',
                  'take': 'roll', 'performer': None}


def src_rtm(key):
    url = 'https://ragtimemusic.com/midifile/' + urllib.parse.quote(key)
    data = cached_get(url, cpath('rtm', safe_name(key)), validate=is_midi)
    return data, {'source': "Colin D. MacDonald's ragtimemusic.com", 'source_ref': url,
                  'licence': 'Copyright 1994-2001 Colin D. MacDonald', 'take': 'sequenced',
                  'performer': 'Colin D. MacDonald (sequenced)'}


TAKE_ORDER = {'performance': 0, 'transcription': 1, 'roll': 2, 'sequenced': 3, 'arrangement': 3}


def fetch_candidate(src, key, work):
    if src == 'rtp':
        return src_rtp(key)
    if src == 'roache':
        return src_roache(key)
    if src == 'gm':
        return src_gm(key)
    if src in ('pcr', 'pcra'):
        return src_pcr(key, work, src)
    if src == 'kdf':
        return src_kdf(key)
    if src == 'rtm':
        return src_rtm(key)
    raise ValueError(src)


# ------------------------------------------------------------------------------------------- stats
def midi_stats(data):
    import mido
    mid = mido.MidiFile(file=io.BytesIO(data), clip=True)
    notes, vels, cc64, chans, progs, texts = 0, set(), 0, set(), set(), []
    for tr in mid.tracks:
        for m in tr:
            if m.type == 'note_on' and m.velocity > 0:
                notes += 1
                vels.add(m.velocity)
                chans.add(m.channel)
            elif m.type == 'control_change' and m.control == 64:
                cc64 += 1
            elif m.type == 'program_change':
                progs.add(m.program)
            elif m.type in ('track_name', 'text', 'copyright') and len(texts) < 12:
                texts.append(str(getattr(m, 'name', '') or getattr(m, 'text', '')).strip())
    return {'notes': notes, 'duration_s': round(mid.length, 1), 'distinct_vel': len(vels),
            'cc64': cc64, 'channels': sorted(chans), 'programs': sorted(progs), 'type': mid.type,
            'tracks': len(mid.tracks), 'texts': [t for t in texts if t]}


def acceptable(st, take):
    """Minimum bar for a staged file; a 'performance' with flat velocity is really a sequence."""
    if st['notes'] < 16 or st['duration_s'] < 25 or st['duration_s'] > 1500:
        return False, 'too short/long'
    if any(p >= 8 for p in st['programs']):
        return False, f"non-piano programs {st['programs']}"
    if take == 'performance' and st['distinct_vel'] < 12:
        return False, 'flat velocity'
    return True, ''


def prior_take(src, key):
    if src in ('rtp', 'roache') or (src == 'kdf' and '(c)simonetto' in key):
        return 'performance'
    if src in ('pcr', 'gm', 'pcra'):
        return 'transcription'
    if src == 'kdf':
        return 'roll'
    return 'sequenced'


SUB_ORDER = {'pcr': 0, 'gm': 1, 'pcra': 2}  # transcriptions: Transkun/Kong full velocity before Aria's 16 levels

# ------------------------------------------------------------------------------------------ works
# rank | title | composer | year | genre[/era] | tags | candidates
# candidates: src:key[@Performer_Name] (underscores in the performer become spaces, %20 in a key is a space).
# The best take is the first that fetches and passes the checks, in take order
# (performance > transcription > roll > sequenced) and then in the order listed.
WORKS = r"""
# --- Scott Joplin -------------------------------------------------------------------------------------
1.00 | Maple Leaf Rag | Scott Joplin | 1899 | ragtime | rag, classic rag | rtp:mid/mud/maple_sk.mid roache:mapleaf kdf:me/joplin_maple_leaf_rag_(c)simonetto.mid pcr:Maple_Leaf_Rag gm:pMAtL7n_-rc
1.00 | The Entertainer | Scott Joplin | 1902 | ragtime | rag, classic rag | kdf:me/joplin_entertainer_(c)simonetto.mid pcr:The_Entertainer gm:fPmruHc4S9Q rtm:entertnr.mid
0.85 | Elite Syncopations | Scott Joplin | 1902 | ragtime | rag, classic rag | pcr:Elite_Syncopations gm:A44QDL-K-bY rtm:elitesyn.mid
0.85 | The Easy Winners | Scott Joplin | 1901 | ragtime | rag, classic rag | pcr:The_Easy_Winners rtm:ezwnnrs.mid
0.85 | Solace | Scott Joplin | 1909 | ragtime | rag, tango, mexican serenade | rtp:mid/rts/solac_sk.mid roache:solace pcr:Solace gm:oLNORRrRyMQ
0.80 | Bethena | Scott Joplin | 1905 | ragtime | waltz, concert waltz | roache:bethena pcr:Bethena gm:SskkUtimyLI
0.80 | Pine Apple Rag | Scott Joplin | 1908 | ragtime | rag, classic rag | rtp:mid/rtref/Pineapple%20Rag.mid pcr:Pine_Apple_Rag gm:nhQvqK67Iwc
0.75 | Gladiolus Rag | Scott Joplin | 1907 | ragtime | rag, classic rag | rtp:mid/got/gladi_sk.mid pcr:Gladiolus_Rag rtm:gladiols.mid
0.70 | Weeping Willow | Scott Joplin | 1903 | ragtime | rag, ragtime two-step | pcr:Weeping_Willow gm:hrhgvBd5puo
0.70 | Peacherine Rag | Scott Joplin | 1901 | ragtime | rag, classic rag | kdf:me/joplin_peacherine_rag_(c)simonetto.mid pcr:Peacherine_Rag gm:Z8c8eFXNolg
0.70 | The Ragtime Dance | Scott Joplin | 1906 | ragtime | rag, stop-time | kdf:me/joplin_ragtime_dance_(c)simonetto.mid pcr:The_Ragtime_Dance gm:unGmMmD8kPQ
0.65 | The Cascades | Scott Joplin | 1904 | ragtime | rag, classic rag | rtp:mid/nol/casca_sk.mid roache:cascades kdf:me/joplin_cascades_(c)simonetto.mid pcr:The_Cascades
0.65 | Magnetic Rag | Scott Joplin | 1914 | ragtime | rag, classic rag | pcr:Magnetic_Rag rtm:magnetic.mid
0.60 | Euphonic Sounds | Scott Joplin | 1909 | ragtime | rag, syncopated novelty | pcr:Euphonic_Sounds gm:DXg3b35T1iw
0.60 | Heliotrope Bouquet | Scott Joplin | 1907 | ragtime | rag, slow drag, with Louis Chauvin | rtp:mid/misc/helio_sk.mid roache:helio pcr:Heliotrope_Bouquet
0.60 | Original Rags | Scott Joplin | 1899 | ragtime | rag, classic rag | kdf:me/joplin_original_rags_(c)simonetto.mid pcr:Original_Rags
0.60 | Fig Leaf Rag | Scott Joplin | 1908 | ragtime | rag, classic rag | kdf:me/joplin_fig_leaf_rag_(c)simonetto.mid pcr:Fig_Leaf_Rag gm:akfySZPU9yI
0.60 | Swipesy | Scott Joplin | 1900 | ragtime | cakewalk, with Arthur Marshall | kdf:me/joplin_swipesy_(c)simonetto.mid rtm:swipesy.mid
0.60 | Sunflower Slow Drag | Scott Joplin | 1901 | ragtime | slow drag, with Scott Hayden | pcr:Sunflower_Slow_Drag rtm:sunflwrd.mid
0.55 | Wall Street Rag | Scott Joplin | 1909 | ragtime | rag, classic rag | roache:wallst pcr:Wall_Street_Rag gm:nA766ansgNc
0.55 | Stoptime Rag | Scott Joplin | 1910 | ragtime | rag, stop-time | pcr:Stoptime_Rag gm:rpPrsiFpCms
0.55 | Rose Leaf Rag | Scott Joplin | 1907 | ragtime | rag, classic rag | pcr:Rose_Leaf_Rag gm:aKarM4nPY-4@Cory_Hall
0.55 | The Chrysanthemum | Scott Joplin | 1904 | ragtime | rag, afro-american intermezzo | pcr:The_Chrysanthemum
0.50 | Sugar Cane | Scott Joplin | 1908 | ragtime | rag, classic rag | rtp:mid/misc/sugar_sk.mid pcr:Sugar_Cane
0.50 | Eugenia | Scott Joplin | 1906 | ragtime | rag, classic rag | rtp:mid/rtref/Eugenia.mid pcr:Eugenia
0.50 | Paragon Rag | Scott Joplin | 1909 | ragtime | rag, classic rag | pcr:Paragon_Rag
0.50 | Palm Leaf Rag | Scott Joplin | 1903 | ragtime | rag, slow drag | pcr:Palm_Leaf_Rag
0.50 | Country Club | Scott Joplin | 1909 | ragtime | rag, ragtime two-step | kdf:me/joplin_country_club_(c)simonetto.mid pcr:Country_Club
0.50 | Scott Joplin's New Rag | Scott Joplin | 1912 | ragtime | rag, classic rag | pcr:New_Rag gm:C0vGk5MzVFI
0.50 | Something Doing | Scott Joplin | 1903 | ragtime | cakewalk, with Scott Hayden | pcr:Something_Doing
0.50 | The Strenuous Life | Scott Joplin | 1902 | ragtime | rag, march | pcr:The_Strenuous_Life
0.50 | A Real Slow Drag | Scott Joplin | 1911 | ragtime | slow drag, treemonisha | pcr:A_Real_Slow_Drag
0.50 | The Great Crush Collision March | Scott Joplin | 1896 | ragtime | march | rtp:mid/kel/crush_sk.mid pcr:Great_Crush_Collision
0.45 | Leola | Scott Joplin | 1905 | ragtime | rag, classic rag | pcr:Leola gm:ajxis8zj7Ow
0.45 | The Nonpareil | Scott Joplin | 1907 | ragtime | rag, classic rag | pcr:The_Nonpareil
0.45 | The Favorite | Scott Joplin | 1904 | ragtime | rag, ragtime two-step | pcr:The_Favorite
0.45 | Felicity Rag | Scott Joplin | 1911 | ragtime | rag, with Scott Hayden | pcr:Felicity_Rag
0.45 | A Breeze from Alabama | Scott Joplin | 1902 | ragtime | rag, ragtime two-step | pcr:A_Breeze_from_Alabama
0.45 | Cleopha | Scott Joplin | 1902 | ragtime | march, two-step | pcr:Cleopha
0.45 | The Sycamore | Scott Joplin | 1904 | ragtime | rag, concert rag | pcr:The_Sycamore
0.45 | Search-Light Rag | Scott Joplin | 1907 | ragtime | rag, classic rag | rtm:srchlite.mid
0.40 | Kismet Rag | Scott Joplin | 1913 | ragtime | rag, with Scott Hayden | pcr:Kismet_Rag
0.40 | Lily Queen | Scott Joplin | 1907 | ragtime | rag, ragtime two-step, with Arthur Marshall | pcr:Lily_Queen
0.40 | Reflection Rag | Scott Joplin | 1917 | ragtime | rag, syncopated musings | pcr:Reflection_Rag gm:dlZzuM0uCpc
0.40 | Pleasant Moments | Scott Joplin | 1909 | ragtime | waltz, ragtime waltz | pcr:Pleasant_Moments
0.40 | Bink's Waltz | Scott Joplin | 1905 | ragtime | waltz | pcr:Binks'_Waltz
0.40 | March Majestic | Scott Joplin | 1902 | ragtime | march | pcr:March_Majestic
0.35 | Silver Swan Rag | Scott Joplin | 1914 | ragtime | rag | gm:rFd9nXMkBK8@Cory_Hall
0.35 | Augustan Club Waltz | Scott Joplin | 1901 | ragtime | waltz | pcr:Augustan_Club_Waltz
0.35 | Harmony Club Waltz | Scott Joplin | 1896 | ragtime | waltz | pcr:Harmony_Club_Waltz
0.30 | The Rosebud March | Scott Joplin | 1905 | ragtime | march, two-step | pcr:Rosebud
0.30 | Antoinette | Scott Joplin | 1906 | ragtime | march, two-step | gm:ATKwTONjgUs@Cory_Hall
0.30 | Combination March | Scott Joplin | 1896 | ragtime | march | gm:SXYMyBJwzz0
# --- James Scott --------------------------------------------------------------------------------------
0.65 | Frog Legs Rag | James Scott | 1906 | ragtime | rag, classic rag | gm:wu4JUWwI5WQ
0.60 | Grace and Beauty | James Scott | 1909 | ragtime | rag, classic rag | gm:ONJ35Mnbmlc
0.55 | Climax Rag | James Scott | 1914 | ragtime | rag, classic rag | roache:climax gm:qKhoF2YkYvU
0.50 | Great Scott Rag | James Scott | 1909 | ragtime | rag, classic rag | pcr:Great_Scott_Rag gm:00_oagzyHhk
0.50 | Hilarity Rag | James Scott | 1910 | ragtime | rag, classic rag | pcr:Hilarity_Rag gm:Sx-0R_Zotxw
0.45 | Ophelia Rag | James Scott | 1910 | ragtime | rag, classic rag | pcr:Ophelia_Rag gm:Jw5DOZNoyY0
0.45 | Evergreen Rag | James Scott | 1915 | ragtime | rag, classic rag | pcr:Evergreen_Rag gm:Wd_W1Vs4Oc0
0.45 | Sunburst Rag | James Scott | 1909 | ragtime | rag, classic rag | gm:Q-u3nja4OGE@Cory_Hall pcr:Sunburst_Rag
0.45 | Kansas City Rag | James Scott | 1907 | ragtime | rag, classic rag | gm:F_32EltCZ8Q
0.45 | Ragtime Oriole | James Scott | 1911 | ragtime | rag, classic rag | gm:-mrroUcivUs
0.40 | New Era Rag | James Scott | 1919 | ragtime | rag, classic rag | rtp:mid/kel/newer_sk.mid gm:q0ITYYqEjZg
0.40 | Quality | James Scott | 1911 | ragtime | rag, classic rag | rtp:mid/misc/quali_sk.mid gm:EPGYex_3hWY
0.40 | Honeymoon Rag | James Scott | 1916 | ragtime | rag, classic rag | rtp:mid/got/honey_sk.mid gm:m9D_xUw5HX8
0.40 | Don't Jazz Me Rag | James Scott | 1921 | ragtime | rag, classic rag | gm:Ivk7W1KkjFk
0.40 | Victory Rag | James Scott | 1921 | ragtime | rag, classic rag | gm:SBhGH0e2Ci4
# --- Joseph Lamb --------------------------------------------------------------------------------------
0.60 | American Beauty Rag | Joseph Lamb | 1913 | ragtime | rag, classic rag | roache:ambeauty gm:OcHkpDZwGCY
0.55 | Ragtime Nightingale | Joseph Lamb | 1915 | ragtime | rag, classic rag | rtp:mid/mud/rtnit_sk.mid
0.45 | Top Liner Rag | Joseph Lamb | 1916 | ragtime | rag, classic rag | roache:topliner
0.45 | Sensation | Joseph Lamb | 1908 | ragtime | rag, classic rag, arranged by Scott Joplin | gm:A_VlghFmsfs
0.45 | Ethiopia Rag | Joseph Lamb | 1909 | ragtime | rag, classic rag | gm:x1syJ0cls3E
0.45 | Excelsior Rag | Joseph Lamb | 1909 | ragtime | rag, classic rag | gm:pLiqQ_UAX4I
0.45 | Champagne Rag | Joseph Lamb | 1910 | ragtime | rag, march and two-step | gm:5IBmTfQfNXs@Cory_Hall
0.45 | Cleopatra Rag | Joseph Lamb | 1915 | ragtime | rag, classic rag | gm:nVLHq5AUFlY
0.45 | Patricia Rag | Joseph Lamb | 1916 | ragtime | rag, classic rag | gm:8IPvKWIsFhQ kdf:rolls/ragtime/lamb_patricia_rag_(1916)_s_(nc)smythe.mid@Joseph_Lamb
0.40 | Ragged Rapids Rag | Joseph Lamb | null | ragtime | rag | rtp:mid/lamb/Ragged%20Rapids.mid
0.40 | Alaskan Rag | Joseph Lamb | 1959 | ragtime | rag | rtp:mid/lamb/Alaskan%20Rag.mid
# --- Charles L. Johnson -------------------------------------------------------------------------------
0.70 | Dill Pickles | Charles L. Johnson | 1906 | ragtime | rag, folk rag | rtp:mid/rtref/Dill%20Pickles.mid gm:5iQ2M1cNgqQ pcr:Dill_Pickles_Rag kdf:rolls/new/johnson_90615s_dill_pickles_(nc)smythe.mid
0.45 | Powder Rag | Charles L. Johnson | 1908 | ragtime | rag | rtp:mid/cj/Powder%20Rag.mid gm:-tMOXapul4A
0.40 | Crazy Bone Rag | Charles L. Johnson | 1913 | ragtime | rag | rtp:mid/nol/crazy_sk.mid gm:Fi7xM8UUJ2g
0.40 | Apple Jack | Charles L. Johnson | 1909 | ragtime | rag | gm:Ua__cXC85BE
0.40 | Barber Pole Rag | Charles L. Johnson | 1910 | ragtime | rag | gm:cenH9mNx_Uk
0.40 | Hen Cackle Rag | Charles L. Johnson | 1912 | ragtime | rag, novelty | gm:aB-fg8lBDHs
0.40 | Blue Goose Rag | Charles L. Johnson | 1916 | ragtime | rag | rtp:mid/cj/Blue%20Goose.mid gm:HbXkJZH9cc4
0.40 | Doc Brown's Cake Walk | Charles L. Johnson | 1899 | ragtime | cakewalk | rtp:mid/cj/Doc%20Brown.mid gm:4TMWQcoNsQk
# --- other classic and popular rags ------------------------------------------------------------------
0.80 | 12th Street Rag | Euday L. Bowman | 1914 | ragtime | rag, popular rag | rtp:mid/mud/twelv_sk.mid
0.70 | Black and White Rag | George Botsford | 1908 | ragtime | rag, popular rag | gm:kffg-uIopO4 pcra:Botsford,_George|Black_and_White_Rag
0.40 | Grizzly Bear Rag | George Botsford | 1910 | ragtime | rag, popular rag | gm:sytjYXv2tHA
0.55 | St. Louis Rag | Tom Turpin | 1903 | ragtime | rag, classic rag | rtp:mid/rts/slrag_sk.mid pcra:Turpin,_Tom|The_St._Louis_Rag
0.75 | Alexander's Ragtime Band | Irving Berlin | 1911 | ragtime | ragtime song | rtp:mid/rts/alexa_sk.mid
0.65 | Nola | Felix Arndt | 1915 | ragtime | novelty, fox-trot | rtp:mid/nol/mnola_sk.mid gm:G9mtJzsjxm0
0.45 | Desecration Rag | Felix Arndt | 1914 | ragtime | novelty, humoresque rag | pcr:Arndt,_Felix|Desecration gm:gncRGBLTS9U
0.45 | Agitation Rag | Robert Hampton | 1915 | ragtime | rag, classic rag | rtp:mid/nol/agita_sk.mid gm:GfYtAdY0GAU
0.40 | Cataract Rag | Robert Hampton | 1914 | ragtime | rag, classic rag | rtp:mid/rts/catar_sk.mid gm:JBFxW0ercM4
0.45 | Pastime Rag No. 1 | Artie Matthews | 1913 | ragtime | rag, classic rag | gm:Ihq059U-weI
0.40 | Pastime Rag No. 5 | Artie Matthews | 1918 | ragtime | rag, classic rag | gm:1BshCbvpNR4
0.40 | Missouri Romp | Arthur Marshall | null | ragtime | rag, classic rag | roache:mo_romp
0.40 | Tickled to Death | Charles Hunter | 1899 | ragtime | rag, folk rag | rtp:mid/rts/tickl_sk.mid
0.40 | Cannonball Rag | Joseph Northrup | 1905 | ragtime | rag | rtp:mid/misc/canon_sk.mid
0.40 | Wild Cherries | Ted Snyder | 1908 | ragtime | rag, popular rag | rtp:mid/mud/wildc_sk.mid
0.40 | The Smiler | Percy Wenrich | 1907 | ragtime | rag, popular rag | rtp:mid/rtref/The%20Smiler.mid
0.40 | Peter Gink | George L. Cobb | 1918 | ragtime | novelty, one-step | rtp:mid/rtref/Peter%20Gink.mid
0.45 | That's a Plenty | Lew Pollack | 1914 | ragtime | rag, dixieland | rtp:mid/nol/plent_sk.mid
0.40 | Sleepy Hollow Rag | Clarence Woods | 1918 | ragtime | rag | rtp:mid/mud/sleep_sk.mid
# --- stride and Harlem piano -------------------------------------------------------------------------
0.75 | Carolina Shout | James P. Johnson | 1921 | ragtime | stride | rtp:mid/got/carol_sk.mid roache:c_shout pcra:Johnson,_James_P.|The_Carolina_Shout
0.60 | Snowy Morning Blues | James P. Johnson | 1927 | ragtime | stride, blues | rtp:mid/irres/snowy_sk.mid roache:smblues
0.50 | Jingles | James P. Johnson | 1926 | ragtime | stride | rtp:mid/kel/jingl_sk.mid roache:jgjingl
0.45 | Mule Walk | James P. Johnson | 1939 | ragtime | stride | roache:mulewalk
0.45 | You've Got to Be Modernistic | James P. Johnson | 1930 | ragtime | stride | rtp:mid/nol/moder_sk.mid
0.85 | Ain't Misbehavin' | Fats Waller | 1929 | ragtime | stride, song | rtp:mid/nol/aint_sk.mid
0.60 | Handful of Keys | Fats Waller | 1929 | ragtime | stride | pcra:Waller,_Fats|Handful_of_Keys
0.60 | Alligator Crawl | Fats Waller | 1927 | ragtime | stride | rtp:mid/mud/gator_sk.mid
0.55 | Smashing Thirds | Fats Waller | 1929 | ragtime | stride | rtp:mid/got/smash_sk.mid
0.50 | Valentine Stomp | Fats Waller | 1929 | ragtime | stride | rtp:mid/rts/valen_sk.mid
0.45 | Junk Man Rag | Luckey Roberts | 1913 | ragtime | stride, rag | rtp:mid/kel/junkm_sk.mid
0.40 | Pork and Beans | Luckey Roberts | 1913 | ragtime | stride, rag | rtp:mid/kel/porkb_sk.mid
0.55 | Charleston Rag | Eubie Blake | 1899 | ragtime | stride, rag | rtp:mid/mud/charl_sk.mid
0.40 | Chevy Chase | Eubie Blake | 1914 | ragtime | rag, fox-trot | rtp:mid/mud/chevy_sk.mid
0.40 | Troublesome Ivories | Eubie Blake | 1914 | ragtime | stride, rag | rtp:mid/rts/troub_sk.mid
0.40 | The Baltimore Todolo | Eubie Blake | null | ragtime | stride | roache:baltimor
0.45 | Little Rock Getaway | Joe Sullivan | 1935 | ragtime | stride, swing | roache:ltlrock
# --- novelty piano -----------------------------------------------------------------------------------
0.80 | Kitten on the Keys | Zez Confrey | 1921 | ragtime | novelty | rtp:mid/irres/kitte_sk.mid roache:kitenkey gm:m2IHWPhRnXc@Cory_Hall
0.60 | Dizzy Fingers | Zez Confrey | 1923 | ragtime | novelty | rtp:mid/nol/dizzy_sk.mid
0.45 | Coaxing the Piano | Zez Confrey | 1922 | ragtime | novelty | rtp:mid/nol/coaxi_sk.mid
0.35 | You Tell 'Em, Ivories | Zez Confrey | 1921 | ragtime | novelty | roache:youtellm
0.55 | In a Mist | Bix Beiderbecke | 1927 | ragtime | novelty, impressionist jazz | roache:inamist
0.35 | Bluin' the Black Keys | Arthur Schutt | 1926 | ragtime | novelty | roache:bluin
0.45 | Marigold | Billy Mayerl | 1927 | ragtime | novelty | gm:m2ohiV9j82Y
0.35 | Bats in the Belfry | Billy Mayerl | 1935 | ragtime | novelty | gm:a-ydiRJH2MY
0.35 | Railroad Rhythm | Billy Mayerl | 1938 | ragtime | novelty | gm:0_9RrbpGcOE
0.30 | Sweet William | Billy Mayerl | 1938 | ragtime | novelty | gm:dHrd-JDxz-E@Phillip_Dyson
0.30 | The Nimble-Fingered Gentleman | Billy Mayerl | null | ragtime | novelty | gm:qU1U4rnp55U
# --- Jelly Roll Morton -------------------------------------------------------------------------------
0.65 | King Porter Stomp | Jelly Roll Morton | 1924 | ragtime | stomp, new orleans | pcra:Morton,_Jelly_Roll|King_Porter_Stomp kdf:rolls/ragtime/morton_9595s_king_porter_stomp_(1924)_(nc)smythe.mid
0.50 | Jelly Roll Blues | Jelly Roll Morton | 1915 | ragtime | blues, new orleans, composer's roll | kdf:rolls/ragtime/morton_jelly_roll_blues_(1924)_s_(nc)smythe.mid@Jelly_Roll_Morton
0.50 | Black Bottom Stomp | Jelly Roll Morton | 1925 | ragtime | stomp, new orleans | kdf:rolls/ragtime/morton_9900s_black_bottom_stomp_(1919)_(nc)smythe.mid@Hi_Babit
0.45 | Mr. Jelly Lord | Jelly Roll Morton | 1923 | ragtime | stomp, new orleans, composer's roll | kdf:rolls/ragtime/morton_159s_mr_jelly_lord_(1924)_(nc)smythe.mid@Jelly_Roll_Morton
0.45 | Grandpa's Spells | Jelly Roll Morton | 1923 | ragtime | stomp, new orleans | kdf:rolls/ragtime/morton_grandpa's_spells_(1926)_s_(nc)smythe.mid
0.45 | Milenberg Joys | Jelly Roll Morton | 1925 | ragtime | stomp, new orleans | kdf:rolls/ragtime/morton_3269s_milenberg_joys_(1925)_(nc)smythe.mid@J._Lawrence_Cook
0.45 | Wolverine Blues | Jelly Roll Morton | 1923 | ragtime | blues, new orleans | rtp:mid/wild/wolve_sk.mid
0.45 | The Crave | Jelly Roll Morton | null | ragtime | tango, spanish tinge | gm:MkGjDbKauVo
0.30 | Mister Joe | Jelly Roll Morton | null | ragtime | rag, new orleans | roache:mrjoe
# --- blues of the ragtime era ------------------------------------------------------------------------
0.80 | St. Louis Blues | W. C. Handy | 1914 | ragtime | blues | rtp:mid/irres/stlou_sk.mid rtp:mid/rts/slblu_sk.mid
0.55 | The Memphis Blues | W. C. Handy | 1912 | ragtime | blues | rtp:mid/irres/mephi_sk.mid
0.50 | Yellow Dog Blues | W. C. Handy | 1914 | ragtime | blues | rtp:mid/wild/yello_sk.mid
0.40 | Ole Miss | W. C. Handy | 1916 | ragtime | blues, rag | rtp:mid/wild/olemi_sk.mid kdf:rolls/joplin/old_miss_rag_(nc)smythe.mid
0.50 | Royal Garden Blues | Clarence Williams | 1919 | ragtime | blues, with Spencer Williams | rtp:mid/irres/royal_sk.mid
# --- Gershwin ----------------------------------------------------------------------------------------
0.55 | Rialto Ripples | George Gershwin | 1917 | ragtime | rag, novelty, with Will Donaldson | pcr:Gershwin,_George|Rialto_Ripples_Rag gm:dB9y2d9_KC4
0.60 | Swanee | George Gershwin | 1919 | standards | song, composer's roll | gm:xqGI8sSgXbE@George_Gershwin_(piano_roll)
0.85 | Rhapsody in Blue (solo piano) | George Gershwin | 1924 | classical/20th century | rhapsody, jazz age | pcr:Gershwin,_George|Rhapsody_in_Blue
0.65 | Prelude No. 1 in B-flat major (Three Preludes) | George Gershwin | 1926 | classical/20th century | prelude, jazz age | pcr:Gershwin,_George|3_Preludes#1._Allegro_ben_ritmato_e_deciso
0.60 | Prelude No. 2 in C-sharp minor (Three Preludes) | George Gershwin | 1926 | classical/20th century | prelude, blues | pcr:Gershwin,_George|3_Preludes#2._Andante_con_moto_e_poco_rubato
0.55 | Prelude No. 3 in E-flat minor (Three Preludes) | George Gershwin | 1926 | classical/20th century | prelude, jazz age | pcr:Gershwin,_George|3_Preludes#3._Allegro_ben_ritmato_e_deciso
# (Songbook ballads such as Someone to Watch Over Me, Georgia on My Mind and The Man I Love belong to the
#  jazz/standards shelf, fetch_jazz.py, and are left out here.)
# --- Brazilian tango (Nazareth) ----------------------------------------------------------------------
0.55 | Odeon | Ernesto Nazareth | 1909 | ragtime | brazilian tango, choro | pcr:Nazareth,_Ernesto|Odeon gm:UWtmW7tejrI@Paul_Barton
0.45 | Brejeiro | Ernesto Nazareth | 1893 | ragtime | brazilian tango, choro | pcr:Nazareth,_Ernesto|Brejeiro gm:QP6OLwb9w50@Clélia_Iruzun
"""


def parse_works():
    works = []
    for ln in WORKS.strip().splitlines():
        if not ln.strip() or ln.lstrip().startswith('#'):
            continue
        parts = [p.strip() for p in ln.split('|', 6)]
        rank, title, composer, year, genre, tags, cands = parts
        genre, _, era = genre.partition('/')
        cl = []
        for tok in cands.split():
            src, _, rest = tok.partition(':')
            key, _, perf = rest.partition('@')
            cl.append((src, key.replace('%20', ' '), perf.replace('_', ' ') if perf else None))
        works.append({'rank': float(rank), 'title': title, 'composer': composer,
                      'year': None if year == 'null' else int(year), 'genre': genre, 'era': era or None,
                      'tags': [t.strip() for t in tags.split(',') if t.strip()], 'cands': cl})
    return works


def ordered(cands):
    idx = {c: i for i, c in enumerate(cands)}
    return sorted(cands, key=lambda c: (TAKE_ORDER[prior_take(c[0], c[1])], SUB_ORDER.get(c[0], 0), idx[c]))


def slug(work):
    sur = work['composer'].split()[-1]
    t = re.sub(r"[’']", '', work['title'])
    return safe_name(f"{sur}-{t}")[:80]


def build(args):
    works = parse_works()
    if args.only:
        works = [w for w in works if args.only.lower() in w['title'].lower()]
    os.makedirs(FILES, exist_ok=True)
    items, gaps, log_rows = [], [], []
    seen_titles = set()
    for i, w in enumerate(works):
        w['_i'] = i
        k = (w['title'].lower(), w['composer'].lower())
        if k in seen_titles:
            raise SystemExit(f"duplicate work in table: {w['title']}")
        seen_titles.add(k)
    # pass 1 without the Wayback Machine, pass 2 (slow, rate-limited) for the works that need it
    WAYBACK['enabled'] = False
    deferred = resolve(works, items, gaps, log_rows, args)
    if deferred:
        deferred.sort(key=lambda w: -w['rank'])  # best-known first, in case the archive runs slow
        log(f'--- second pass: {len(deferred)} works need the Wayback Machine')
        WAYBACK['enabled'] = True
        resolve(deferred, items, gaps, log_rows, args)
    items.sort(key=lambda it: it.pop('_i'))
    return finish(items, gaps, log_rows, args)


def resolve(works, items, gaps, log_rows, args):
    deferred = []
    for w in works:
        chosen = reserve = None
        for src, key, perf in ordered(w['cands']):
            try:
                data, meta = fetch_candidate(src, key, w)
                st = midi_stats(data)
            except Deferred:
                chosen = 'deferred'
                break
            except Exception as e:  # noqa: BLE001 - try the next candidate
                log_rows.append((w['title'], src, key, f'fail: {type(e).__name__}: {str(e)[:120]}'))
                if isinstance(e, WaybackDown) and w['title'] not in WAYBACK_FALLBACK:
                    WAYBACK_FALLBACK.append(w['title'])
                continue
            ok, why = acceptable(st, meta['take'])
            if not ok:
                log_rows.append((w['title'], src, key, f'rejected: {why}'))
                continue
            if meta['take'] == 'performance' and st['cc64'] < 5:
                # a keyboard performance with no sustain pedal sounds dry; prefer the next take, keep it in reserve
                log_rows.append((w['title'], src, key, 'reserve: performance without sustain pedal'))
                reserve = reserve or (src, key, perf, data, meta, st)
                continue
            chosen = (src, key, perf, data, meta, st)
            break
        if not chosen and reserve:
            chosen = reserve
        if chosen == 'deferred':
            deferred.append(w)
            continue
        if not chosen:
            gaps.append(w['title'])
            log(f"GAP  {w['title']} ({w['composer']})")
            continue
        src, key, perf, data, meta, st = chosen
        name = slug(w) + '.mid'
        if not args.dry_run:
            dst = os.path.join(FILES, name)
            if not (os.path.exists(dst) and open(dst, 'rb').read() == data):
                write_atomic(dst, data)
        items.append({'_i': w['_i'], 'src': 'files/' + name, 'title': w['title'], 'composer': w['composer'],
                      'performer': perf or meta.get('performer'), 'genre': w['genre'],
                      'era': w['era'] if w['genre'] == 'classical' else None, 'year': w['year'],
                      'source': meta['source'], 'source_ref': meta['source_ref'], 'licence': meta['licence'],
                      'take': meta['take'], 'tags': w['tags'] + (['piano roll'] if meta['take'] == 'roll' else []),
                      'rank': w['rank'], 'duration_s': st['duration_s'], 'notes': st['notes'],
                      'velocity_levels': st['distinct_vel'], 'sustain_events': st['cc64']})
        log(f"OK   {w['title'][:44]:44s} {meta['take']:13s} {src:6s} vel={st['distinct_vel']:3d} "
            f"cc64={st['cc64']:4d} {st['duration_s']:6.1f}s")
    return deferred


def finish(items, gaps, log_rows, args):
    if args.dry_run:
        return items, gaps, log_rows
    # drop files from earlier runs that are no longer chosen
    keep = {os.path.basename(i['src']) for i in items}
    if not args.only:
        for fn in os.listdir(FILES):
            if fn not in keep:
                os.remove(os.path.join(FILES, fn))
        man = {'v': 1, 'key': KEY, 'items': items}
        write_atomic(os.path.join(STAGE, 'manifest.json'),
                     json.dumps(man, indent=1, ensure_ascii=False).encode('utf-8'))
        write_atomic(os.path.join(STAGE, 'SOURCES.md'), sources_md(items, gaps).encode('utf-8'))
        write_atomic(os.path.join(CACHE, 'last_run_log.json'),
                     json.dumps({'gaps': gaps, 'attempts': log_rows}, indent=1).encode('utf-8'))
    return items, gaps, log_rows


SOURCE_DOCS = [
    ('Ragtime Press (Sue Keller)',
     "Sue Keller's own arrangements, played live on a weighted MIDI keyboard and recorded uncut (every key and "
     "pedal event). https://www.rtpress.com/titles.htm ; files under https://www.rtpress.com/mid/ , the album "
     "folders that now return 404 from the Internet Archive (http://web.archive.org/web/<ts>id_/http://rtpress.com:80/mid/...).",
     "Copyright (c) Ragtime Press / Sue Keller, all rights reserved (as stated in each file)."),
    ("John Roache's Ragtime MIDI Library",
     "John E. Roache's performances, played into a sequencer (1995-1999). Live site down; per-piece zips from the "
     "Internet Archive (johnroachemusic.com/midiragger/<name>.zip). The mono '_m' (or plain solo-piano) file of each "
     "zip is used; the stereo/drum variants are not.",
     "Performance (c) 1995-1999 John E. Roache; free for non-commercial use, to be distributed only in the original "
     "ZIP with its text file."),
    ('PianoCoRe v1.0 (PERiScoPe)',
     "PianoCoRe (SyMuPe, 2026) indexes piano performances under canonical titles; these rows are PERiScoPe "
     "transcriptions (Transkun V2) of commercial/YouTube recordings. Index: https://huggingface.co/datasets/SyMuPe/PianoCoRe ; "
     "files range-read from https://zenodo.org/records/19186016 (PianoCoRe-1.0-raw-midi.zip).",
     'CC BY-NC-SA 4.0 (PianoCoRe), respecting each source dataset.'),
    ('PianoCoRe v1.0 (GiantMIDI-Piano)', 'GiantMIDI-Piano rows as indexed (and titled) by PianoCoRe; see both.',
     'CC BY-NC-SA 4.0 (PianoCoRe) / CC BY 4.0 (GiantMIDI-Piano).'),
    ('PianoCoRe v1.0 (Aria-MIDI)',
     'Aria-MIDI rows (Aria-AMT transcriptions of YouTube audio, velocity quantised to about 16 levels), titled by '
     'PianoCoRe. Used only where nothing better exists.',
     'CC BY-NC-SA 4.0.'),
    ('GiantMIDI-Piano v1.2 (ByteDance)',
     'Transcriptions of YouTube solo-piano recordings by the Kong et al. high-resolution transcription model. '
     'https://github.com/bytedance/GiantMIDI-Piano ; files range-read from midis_v1.2.zip on Google Drive. '
     'Citation: Qiuqiang Kong, Bochen Li, Jitong Chen, Yuxuan Wang, "GiantMIDI-Piano: A large-scale MIDI dataset '
     'for classical piano music", TISMIR 5(1), 2022.',
     'CC BY 4.0 (plus the dataset disclaimer).'),
    ('Kunstderfuge (Alessandro Simonetto performances)',
     "Alessandro Simonetto's real-time performances of Joplin rags, https://kunstderfuge.com/ragtime.htm "
     '(direct /-/mid.files/me/ paths).',
     '(c) Alessandro Simonetto / onclassical; all rights reserved, no commercial use.'),
    ('Kunstderfuge (Terry Smythe piano-roll scans)',
     "Terry Smythe's scans of 88-note and reproducing piano rolls (roll number, title and pianist in the file's text "
     'events), via https://kunstderfuge.com/ragtime.htm (direct /-/mid.files/rolls/ paths). Rolls carry no '
     'dynamics: velocity is flat.',
     'May not be used for commercial purposes (Terry Smythe).'),
    ("Colin D. MacDonald's ragtimemusic.com",
     'Step-sequenced from published scores with hand-set dynamics; used only for Joplin titles no performance '
     'source has. https://ragtimemusic.com/joplin.html',
     'Copyright 1994-2001 Colin D. MacDonald.'),
]


def sources_md(items, gaps):
    from collections import Counter
    by_src = Counter(i['source'] for i in items)
    by_take = Counter(i['take'] for i in items)
    out = ['# Ragtime, stride and novelty: sources', '',
           f'{len(items)} titles, one take each, chosen in the order performance > transcription > roll > '
           'sequenced. Staged by tools/music_library/fetch_ragtime.py (re-runnable; downloads cached under '
           '~/.local/share/screen-machine/midi-library/_cache/ragtime/). Private, non-commercial use in this house '
           'only; licences are recorded, not filtered on.', '',
           'Takes: ' + ', '.join(f'{k} {v}' for k, v in by_take.most_common()), '']
    for name, desc, lic in SOURCE_DOCS:
        n = by_src.get(name, 0)
        if not n:
            continue
        out += [f'## {name} ({n})', '', desc, '', f'Licence: {lic}', '']
        out += [f"- {i['title']} ({i['composer']}){' - ' + i['performer'] if i['performer'] else ''}"
                for i in sorted(items, key=lambda i: -i['rank']) if i['source'] == name]
        out.append('')
    if gaps:
        out += ['## Not found', '', ', '.join(gaps), '']
    if WAYBACK_FALLBACK:
        out += ['## Waiting on the Wayback Machine', '',
                'The archive was rate-limiting this IP, so these use the next-best take (or are missing); '
                're-running the script upgrades them: ' + ', '.join(WAYBACK_FALLBACK), '']
    return '\n'.join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--only', help='only works whose title contains this text (no manifest written)')
    ap.add_argument('--dry-run', action='store_true', help='resolve and fetch, but write nothing to staging')
    ap.add_argument('--wayback-wait-min', type=float, default=60,
                    help='how long to wait out a Wayback Machine block before using the next-best takes '
                         '(a later re-run upgrades them once the archive answers again)')
    ap.add_argument('--no-wayback', action='store_true',
                    help='use only what is already cached from the Wayback Machine (next-best takes otherwise)')
    args = ap.parse_args()
    WAYBACK['give_up_s'] = args.wayback_wait_min * 60
    WAYBACK['down'] = args.no_wayback
    t0 = time.time()
    items, gaps, _ = build(args)
    from collections import Counter
    log(f"\n{len(items)} staged, {len(gaps)} gaps, {BYTES['n'] / 1e6:.1f} MB downloaded, {time.time() - t0:.0f}s")
    log('by take: ' + ', '.join(f'{k} {v}' for k, v in Counter(i['take'] for i in items).most_common()))
    log('by source: ' + ', '.join(f'{k} {v}' for k, v in Counter(i['source'] for i in items).most_common()))
    if gaps:
        log('gaps: ' + '; '.join(gaps))
    if WAYBACK_FALLBACK:
        log('Wayback blocked, re-run later to upgrade: ' + '; '.join(WAYBACK_FALLBACK))


if __name__ == '__main__':
    main()
