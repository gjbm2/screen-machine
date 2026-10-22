#!/usr/bin/env bash
# Undo wired-first networking: back to Wi-Fi only. Installed as /usr/local/sbin/eth-primary-rollback.
# Also armed as a 5-minute dead-man during install. Run as root.
rm -f /etc/netplan/60-ethernet.yaml \
      /etc/systemd/network/10-netplan-wlp2s0.network.d/50-wifi-source-route.conf \
      /etc/sysctl.d/60-multihome-arp.conf
rmdir /etc/systemd/network/10-netplan-wlp2s0.network.d 2>/dev/null
netplan generate
networkctl reload
ip rule del priority 102 2>/dev/null
ip route flush table 102 2>/dev/null
ip link set enp1s0 down
sysctl -qw net.ipv4.conf.all.arp_ignore=0 net.ipv4.conf.all.arp_announce=0
echo "$(date -u +%FT%TZ) eth-primary-rollback: Ethernet config removed, Wi-Fi only" | systemd-cat -t eth-primary-rollback
