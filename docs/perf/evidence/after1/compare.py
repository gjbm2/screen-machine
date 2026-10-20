import json, sys, math, glob, os
P = sys.argv[1]
def pct(v, p):
    v = sorted(x for x in v if x is not None)
    if not v: return None
    k = (len(v)-1)*p/100; lo, hi = math.floor(k), math.ceil(k)
    return v[lo] + (v[hi]-v[lo])*(k-lo)
def load(f):
    j = json.load(open(f)); return j if isinstance(j, list) else (j.get('frames') or j.get('records') or [])
def runs(recs):
    out, cur = [], 0
    for r in recs:
        if r.get('repeat'): cur += 1
        else:
            if cur: out.append(cur)
            cur = 0
    if cur: out.append(cur)
    return out
def stats(label, f):
    recs = load(f)
    if not recs: return
    t0, t1 = recs[0]['t_start'], recs[-1]['t_start']; dur = max(t1 - t0, 1e-9)
    ren = [r for r in recs if not r.get('repeat')]
    content = [r for r in ren if not r.get('held')] if 'held' in recs[0] else ren
    rr = runs(recs)
    f1 = lambda x: '-' if x is None else f'{x:.1f}'
    row = dict(win=label, slots_s=len(recs)/dur, rendered_fps=len(ren)/dur, content_fps=len(content)/dur,
               repeat_pct=100*(len(recs)-len(ren))/len(recs), runs_ge10=sum(1 for x in rr if x >= 10), longest=max(rr) if rr else 0,
               host_p50=pct([r['host_ms'] for r in ren], 50), host_p95=pct([r['host_ms'] for r in ren], 95),
               slip_p50=pct([r['slip_ms'] for r in recs], 50), slip_p95=pct([r['slip_ms'] for r in recs], 95),
               write_p50=pct([r['write_ms'] for r in recs], 50), enc_p50=pct([r.get('encode_ms') for r in recs], 50),
               cpu_p50=pct([r.get('cpu_ms') for r in ren], 50), blocked_p50=pct([r.get('blocked_ms') for r in ren], 50),
               enc_cpu_p50=pct([r.get('enc_cpu_ms') for r in recs], 50))
    print(f"{label:34s} slots/s {row['slots_s']:.1f} | rendered {row['rendered_fps']:.1f} | content {row['content_fps']:.1f} | repeats {row['repeat_pct']:.0f}% | runs>=10 {row['runs_ge10']} (longest {row['longest']}) | host p50/p95 {f1(row['host_p50'])}/{f1(row['host_p95'])} | slip p50/p95 {f1(row['slip_p50'])}/{f1(row['slip_p95'])} | write p50 {f1(row['write_p50'])} | encode p50 {f1(row['enc_p50'])} | cpu p50 {f1(row['cpu_p50'])} blocked p50 {f1(row['blocked_p50'])} enc_cpu p50 {f1(row['enc_cpu_p50'])}")
def screen_of(d, sid):
    for fn in ('meta.json',):
        try:
            m = json.load(open(os.path.join(d, fn)))
            for s in m.get('sessions', []):
                if s.get('session') == sid: return s.get('screen'), s.get('scene')
        except Exception: pass
    return '?', '?'
print('== W1 baseline 18 Oct 14:27 (note-fluid, old code)')
for f in sorted(glob.glob(P+'/baseline/frames_s-*.json')):
    if f.endswith('.wall.json'): continue
    sid = os.path.basename(f)[7:-5]; sc = screen_of(P+'/baseline', sid); stats(f'{sc[0]} {sc[1]}', f)
print('== W3 18 Oct 15:01-15:04 (live-score, old code)')
for pre in ('cs_', 'r2_'):
    for f in sorted(glob.glob(P+f'/gil-profiler/{pre}frames_s-*.json')):
        sid = os.path.basename(f)[len(pre)+7:-5]; stats(f'{pre}{ {"s-58436e03":"north","s-419fc151":"south"}.get(sid, sid)} live-score', f)
print('== AFTER 20 Oct 22:50 (live-score-paper, changes 1-5)')
for f in sorted(glob.glob(P+'/after1/frames_s-*.json')):
    sid = os.path.basename(f)[7:-5]; sc = screen_of(P+'/after1', sid); stats(f'{sc[0]} {sc[1]}', f)
