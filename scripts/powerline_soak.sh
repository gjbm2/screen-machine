#!/usr/bin/env bash
# 24-hour powerline soak test for the kiosk box (acceptance test in docs/POWERLINE_RESEARCH_2026-10.md):
# every INTERVAL s, push MB megabytes (default 200, ~10 s at 170 Mbit/s) from this workstation to the box's
# Ethernet address and record throughput, plus pings (5 Hz) from the box to the router over Ethernet: 10 s idle,
# then 12 s overlapping the push. Stop it with:  kill $(cat logs/powerline-soak.pid)
# Run from the repo root in WSL:  setsid nohup bash scripts/powerline_soak.sh > /dev/null 2>&1 &
# Output: logs/powerline-soak-<start>.csv  (summarise with: column -s, -t < that file)
set -u
BOX=${BOX:-gjbm2@192.168.1.92}; ETH=${ETH:-192.168.1.10}; MB=${MB:-200}; INTERVAL=${INTERVAL:-900}; HOURS=${HOURS:-24}
OUT=logs/powerline-soak-$(date +%Y%m%d-%H%M).csv
mkdir -p logs; echo $$ > logs/powerline-soak.pid; echo "utc,mbit_s,ping_idle_p50,ping_idle_p99,ping_load_p50,ping_load_p99,ping_loss_pct,eth_carrier,default_dev" > "$OUT"
B=(ssh -o BatchMode=yes -o ConnectTimeout=8 "$BOX")
scp -q -o BatchMode=yes scripts/tput.py "$BOX:/tmp/tput.py"
"${B[@]}" "setsid nohup timeout $((HOURS*3600+600)) python3 /tmp/tput.py recv 5201 > /tmp/tput_recv_soak.log 2>&1 < /dev/null &"
pings() {  # $1 = seconds; prints p50 p99 loss%
  "${B[@]}" "ping -i 0.2 -c $(( $1 * 5 )) -I $ETH 192.168.1.1 2>/dev/null" | python3 -c "
import sys,re
t=sys.stdin.read(); v=sorted(float(x) for x in re.findall(r'time=([\d.]+)',t)); m=re.search(r'([\d.]+)% packet loss',t)
p=lambda q: v[min(len(v)-1,int(q*len(v)))] if v else -1
print(f'{p(.5):.1f} {p(.99):.1f} {m.group(1) if m else -1}')"
}
end=$(( $(date +%s) + HOURS*3600 ))
while [ "$(date +%s)" -lt "$end" ]; do
  t=$(date -u +%FT%TZ)
  read i50 i99 iloss < <(pings 10)
  ( pings 12 > /tmp/soak_load_ping.$$ ) & pp=$!
  sleep 1; t0=$(date +%s.%N); timeout 120 python3 scripts/tput.py push "$ETH" 5201 "$MB" >/dev/null 2>&1; rc=$?; t1=$(date +%s.%N)
  wait $pp; read l50 l99 lloss < /tmp/soak_load_ping.$$; rm -f /tmp/soak_load_ping.$$
  mbit=$( [ $rc -eq 0 ] && python3 -c "print(f'{$MB*8/($t1-$t0):.0f}')" || echo fail )
  st=$("${B[@]}" "echo \$(cat /sys/class/net/enp1s0/carrier) \$(ip route show default | head -1 | awk '{print \$5}')" 2>/dev/null || echo "? ?")
  echo "$t,$mbit,$i50,$i99,$l50,$l99,$lloss,${st% *},${st#* }" >> "$OUT"
  sleep "$INTERVAL"
done
