"""Re-run of box-budget/budget_calc.py with the critics' rows (18 Oct, ~20:10). Pure arithmetic.
Changes vs v1: GPU encode uses the spike's measured PROCESS CPU p50 2.8-5.1 ms/frame (paced runs, summary_v2.tsv)
instead of 1-1.5 ms [U]; adds live-score children [U], one shared preview worker [U], the box probe [I]."""
CLK = (1/1.65, 1/1.5)
def rng(a,b): return [round(a,2), round(b,2)]
def add(*rs): return rng(sum(r[0] for r in rs), sum(r[1] for r in rs))
def sub(a,b): return rng(a[0]-b[1], a[1]-b[0])
LPS=20
win_a=rng(12.0,14.5); win_b=rng(12.0-1.8-1.0,14.5-0.2); win_c=rng(win_b[0]*CLK[0], win_b[1]*CLK[1])
sup={k:rng(LPS-w[1],LPS-w[0]) for k,w in (('a',win_a),('b',win_b),('c',win_c))}
av_after={'render own CPU 5-7 ms x120':rng(0.6,0.84),'GPU encode, process CPU 2.8-5.1 ms x120 [M spike]':rng(0.34,0.61),
 'stream control + fan-out':rng(0.3,0.5),'show_service incl. show-rx + beat process':rng(0.8,1.2),'Flask/room':rng(0.25,0.35),
 'guest softirq/irq':rng(0.35,0.66),'live-score children (analyse, engrave, PM2S, extra ffmpeg) [U]':rng(0.3,1.0),
 'shared preview worker while browsing [U]':rng(0.0,0.3),'box probe [I]':rng(0.02,0.02)}
AVA=add(*av_after.values())
poll_a=add(rng(0.81,0.82),rng(0.30,0.60),rng(0.17,0.17),rng(0.14,0.29),rng(0.33,0.33))
batch=add(rng(0.45,2.07),rng(0.0,1.5))
pb=sub(poll_a,rng(0.33,0.33))
bg={'a':add(poll_a,batch),'b':add(pb,batch),'c':add(rng(pb[0]*CLK[0],pb[1]*CLK[1]),batch)}
print('AV_after cores (today clock):',AVA,' clock fixed:',rng(AVA[0]*CLK[0],AVA[1]*CLK[1]))
for k,a in av_after.items(): print('  ',k,a)
for sc in 'abc':
    av=AVA if sc!='c' else rng(AVA[0]*CLK[0],AVA[1]*CLK[1])
    head=rng(sup[sc][0]-av[1]-bg[sc][1], sup[sc][1]-av[0]-bg[sc][0])
    print(sc,'wsl_supply',sup[sc],'av',av,'bg',bg[sc],'headroom',head)
print('scenarios: a = today desktop; b = Brave live tabs closed + Spectre UI built + stray vites stopped; c = b + CPU clock fixed (1.5-1.65x)')
