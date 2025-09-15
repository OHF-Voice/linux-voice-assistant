#!/usr/bin/env bash
set -euo pipefail

PORT=6053
NAME="%h (Linux Voice Assistant)"
SERVICE=/etc/avahi/services/esphome-lva.service

# Wait up to ~40s for any global IPv4 (cosmetic: to include ipv4_* TXT)
deadline=$((SECONDS+40))
while [ $SECONDS -lt $deadline ]; do
  ip -o -4 addr show up scope global | grep -q . && break || true
  sleep 1
done

# Collect all UP, non-loopback ifaces
mapfile -t IFACES < <(ip -o link show up | awk -F': ' '{print $2}' | grep -v '^lo$' | sort -u)

TXT="    <txt-record>platform=linux</txt-record>
"
LIST=""
for i in "${IFACES[@]}"; do
  [ -e "/sys/class/net/$i/address" ] || continue
  mac=$(cat "/sys/class/net/$i/address")
  mac_nc=${mac//:/}   # no colons (ESPHome style)
  if [ -d "/sys/class/net/$i/wireless" ] || [[ "$i" == wl* ]]; then net=wifi; else net=ethernet; fi

  TXT+="    <txt-record>net_${i}=${net}</txt-record>
"
  TXT+="    <txt-record>mac_${i}=${mac_nc}</txt-record>
"

  # IPv4 (global)
  while read -r a; do
    [ -n "$a" ] || continue
    a=${a%%/*}
    TXT+="    <txt-record>ipv4_${i}=${a}</txt-record>
"
  done < <(ip -o -4 addr show dev "$i" scope global | awk '{print $4}' || true)

  # IPv6 (global, not link-local)
  while read -r a6; do
    [ -n "$a6" ] || continue
    a6=${a6%%/*}
    TXT+="    <txt-record>ipv6_${i}=${a6}</txt-record>
"
  done < <(ip -o -6 addr show dev "$i" scope global | awk '{print $4}' || true)

  LIST+="${i},"
done
LIST=${LIST%,}

cat >"$SERVICE" <<XML
<?xml version="1.0" standalone='no'?><!--*-nxml-*-->
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">${NAME}</name>
  <service>
    <type>_esphomelib._tcp</type>
    <port>${PORT}</port>
    <txt-record>ifaces=${LIST}</txt-record>
${TXT}  </service>
</service-group>
XML

# Avahi will (re)publish on all active ifaces automatically
systemctl restart avahi-daemon || true
