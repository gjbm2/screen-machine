#!/usr/bin/env bash
# Usage: run_pair.sh <name> [nvenc_spike.py args...] -- waits for an idle GPU 1 once, then starts 2 encoder processes
# that begin their loops at the same epoch time.  Each has its own session guard, 600-frame cap and 55 s deadline.
set -u
D=/tmp/claude-1000/-home-gjbm2-dev-screen-machine/49a9e605-47c6-4ba4-98e3-fd28d61d6fec/scratchpad/perf/programme
V=$D/encode-spike/venv/bin/python
OUT=$D/gpu-encode-spike
name=$1; shift
idle() {
  s=$(curl -s -m 2 http://127.0.0.1:8702/api/sessions) || return 1
  echo "$s" | python3 -c 'import sys,json
d=json.load(sys.stdin); ss=d.get("sessions",d)
bad=[s for s in ss if str(s.get("gpu"))=="1" or str(s.get("state","")).lower() not in ("ended","failed")]
sys.exit(1 if bad else 0)' || return 1
  n=$(nvidia-smi -i 1 --query-gpu=encoder.stats.sessionCount --format=csv,noheader 2>/dev/null | tr -d ' ')
  [ "$n" = "0" ] || return 1
}
for k in $(seq 0 40); do
  if idle; then break; fi
  if [ "$k" = 40 ]; then echo "GAVE UP: GPU 1 not idle for 20 min" | tee $OUT/$name.gaveup; exit 9; fi
  echo "$(date +%T) not idle, waiting 30 s"; sleep 30
done
start=$(python3 -c 'import time; print(time.time()+6)')
echo "$(date +%T) start pair $name" >> $OUT/runs.log
for j in a b; do
  smi=""; [ $j = b ] && smi="--no-smi"
  CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=1 timeout -k 3 75 nice -n 19 $V -I $D/encode-spike/nvenc_spike.py \
    --name "${name}_$j" --json $OUT/${name}_$j.json --start-at $start $smi "$@" > $OUT/${name}_$j.log 2>&1 &
done
wait
echo "$(date +%T) end pair $name" >> $OUT/runs.log
for j in a b; do tail -2 $OUT/${name}_$j.log; done
