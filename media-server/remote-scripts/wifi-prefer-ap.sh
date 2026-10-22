#!/usr/bin/env bash
# wifi-prefer-ap — keep the kiosk's Wi-Fi on its best access point.
#
# Two APs share the house SSID and reach the kiosk at about the same signal (~-69 dBm), but one
# gives ~20 Mbit/s and the other ~5 Mbit/s (measured 2026-10-11). The APs' 802.11v BSS-transition
# requests ("WNM: Preferred List Available") and the odd disassociation keep moving the kiosk onto
# the slow one.
#
# While connected to PREFERRED, pin wpa_supplicant's network to that BSSID: it then turns down
# transition requests to other APs. If the kiosk stays disconnected for GRACE seconds while pinned
# (PREFERRED rebooting, changing channel, gone), release the pin so it can join any AP. Every RETRY
# seconds, if PREFERRED is visible again, re-pin and reassociate (a plain reconnect: `wpa_cli roam`
# proved unreliable on this rtw88 card). If that switch-back fails, the GRACE fallback releases the
# pin again and the retry interval doubles (up to RETRY_MAX), so a marginal AP cannot cause flapping.
# Boot always starts unpinned (the pin lives only in wpa_supplicant's memory), and stopping the
# service releases it, so `systemctl disable --now wifi-prefer-ap` restores stock behaviour.
#
# Config: /etc/default/wifi-prefer-ap  (PREFERRED=<bssid>; optional IFACE, NET_ID, GRACE, RETRY, RETRY_MAX, MIN_SIGNAL)
# Usage:  wifi-prefer-ap            run the watcher (systemd)
#         wifi-prefer-ap --release  clear the pin
set -u
IFACE=wlp2s0 NET_ID=0 PREFERRED= GRACE=40 RETRY=120 RETRY_MAX=1800 MIN_SIGNAL=-78
[[ -r /etc/default/wifi-prefer-ap ]] && . /etc/default/wifi-prefer-ap
PREFERRED=${PREFERRED,,}
W=(wpa_cli -p /run/wpa_supplicant -i "$IFACE")
short() { echo "..${1: -5}"; }   # last two octets only, for the journal

release() { "${W[@]}" set_network "$NET_ID" bssid any >/dev/null 2>&1; }
if [[ ${1:-} == --release ]]; then release; echo "pin released"; exit 0; fi
[[ $PREFERRED =~ ^([0-9a-f]{2}:){5}[0-9a-f]{2}$ ]] || { echo "PREFERRED not set in /etc/default/wifi-prefer-ap" >&2; exit 1; }

down_since=0 last_try=0 retry=$RETRY switching=0
echo "watching $IFACE: preferred AP $(short "$PREFERRED"), grace ${GRACE}s, retry ${RETRY}-${RETRY_MAX}s"
while sleep 5; do
  st=$("${W[@]}" status 2>/dev/null) || continue   # supplicant restarting
  state=$(sed -n 's/^wpa_state=//p' <<<"$st")
  cur=$(sed -n 's/^bssid=//p' <<<"$st"); cur=${cur,,}
  pin=$("${W[@]}" get_network "$NET_ID" bssid 2>/dev/null); pin=${pin,,}   # "FAIL" when unpinned
  now=$(date +%s)

  if [[ $state == COMPLETED ]]; then
    down_since=0
    if [[ $cur == "$PREFERRED" ]]; then
      [[ $pin == "$PREFERRED" ]] || { "${W[@]}" set_network "$NET_ID" bssid "$PREFERRED" >/dev/null && echo "on preferred AP: pinned"; }
      (( switching )) && echo "back on preferred AP"
      switching=0 retry=$RETRY
    elif (( now - last_try >= retry )); then
      # on another AP (fallback, or moved by the APs): go back once the preferred one is usable
      last_try=$now
      "${W[@]}" scan >/dev/null 2>&1; sleep 6
      sig=$("${W[@]}" scan_results 2>/dev/null | awk -v b="$PREFERRED" 'tolower($1) == b {print $3; exit}')
      if [[ -n $sig ]] && (( sig >= MIN_SIGNAL )); then
        echo "on $(short "$cur"), preferred visible at $sig dBm: switching back"
        "${W[@]}" set_network "$NET_ID" bssid "$PREFERRED" >/dev/null
        "${W[@]}" reassociate >/dev/null
        switching=1
      else
        echo "on $(short "$cur"), preferred not usable (${sig:-not seen}): staying, next check in ${retry}s"
      fi
    fi
  else
    (( down_since )) || down_since=$now
    if [[ $pin == "$PREFERRED" ]] && (( now - down_since >= GRACE )); then
      if (( switching )); then retry=$(( retry * 2 > RETRY_MAX ? RETRY_MAX : retry * 2 )); fi
      echo "disconnected $((now - down_since)) s while pinned: releasing pin so any AP can be used (next switch-back in ${retry}s)"
      release; "${W[@]}" reassociate >/dev/null
      last_try=$now switching=0
    fi
  fi
done
