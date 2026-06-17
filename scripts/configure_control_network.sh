#!/usr/bin/env bash
set -euo pipefail

NIC="${BURNER_CONTROL_NIC:-ens5f0np0}"
ADDR="${BURNER_CONTROL_ADDR:-192.168.12.10/24}"
SRC_ADDR="${ADDR%%/*}"
NETPLAN_FILE="${BURNER_CONTROL_NETPLAN_FILE:-/etc/netplan/70-burner-control-network.yaml}"
PERSIST="${BURNER_CONTROL_PERSIST:-1}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "This script must run as root, for example: sudo bash $0" >&2
  exit 1
fi

ip link set "${NIC}" up
ip addr replace "${ADDR}" dev "${NIC}"
ip route replace 192.168.11.0/24 dev "${NIC}" src "${SRC_ADDR}"
ip route replace 192.168.12.0/24 dev "${NIC}" src "${SRC_ADDR}"

if [[ "${PERSIST}" == "1" ]]; then
  cat >"${NETPLAN_FILE}" <<EOF
network:
  version: 2
  ethernets:
    ${NIC}:
      dhcp4: false
      addresses:
        - ${ADDR}
      routes:
        - to: 192.168.11.0/24
          scope: link
        - to: 192.168.12.0/24
          scope: link
EOF
  chmod 600 "${NETPLAN_FILE}"
  netplan apply
fi

ip -brief addr show "${NIC}"
ip route get 192.168.11.1
ip route get 192.168.12.1
