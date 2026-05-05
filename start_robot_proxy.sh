#!/usr/bin/env bash
set -u

APP_DIR="/home/unitree/mydemo"
ETH_IF="eth0"
ETH_IP_CIDR="192.168.123.162/24"
ROBOT_DEV_IP="192.168.123.161"
MULTICAST_TEST_IP="239.255.0.1"
MULTICAST_ROUTE="239.255.0.0/16"

echo "[g1_robot_proxy] cd ${APP_DIR}"
cd "${APP_DIR}" || {
  echo "[g1_robot_proxy][ERROR] failed to cd ${APP_DIR}"
  exit 1
}

echo "[g1_robot_proxy] stopping old g1_robot_proxy.py processes"
pkill -f "[g]1_robot_proxy.py" || true

echo "[g1_robot_proxy] clearing CycloneDDS env"
unset CYCLONEDDS_URI
unset CYCLONEDDS_HOME

echo "[g1_robot_proxy] bringing ${ETH_IF} up"
sudo ip link set "${ETH_IF}" up || echo "[g1_robot_proxy][WARN] failed to set ${ETH_IF} up"

echo "[g1_robot_proxy] ensuring ${ETH_IP_CIDR} on ${ETH_IF}"
if ip -4 addr show dev "${ETH_IF}" | grep -q "${ETH_IP_CIDR}"; then
  echo "[g1_robot_proxy] ${ETH_IP_CIDR} already configured"
else
  sudo ip addr add "${ETH_IP_CIDR}" dev "${ETH_IF}" || echo "[g1_robot_proxy][WARN] failed to add ${ETH_IP_CIDR}"
fi

echo "[g1_robot_proxy] ensuring multicast route ${MULTICAST_ROUTE} dev ${ETH_IF}"
sudo ip route replace "${MULTICAST_ROUTE}" dev "${ETH_IF}" || echo "[g1_robot_proxy][WARN] failed to replace multicast route"

echo "[g1_robot_proxy] ip addr show ${ETH_IF}"
ip addr show "${ETH_IF}" || true

echo "[g1_robot_proxy] ip route get ${ROBOT_DEV_IP}"
ip route get "${ROBOT_DEV_IP}" || true

echo "[g1_robot_proxy] ip route get ${MULTICAST_TEST_IP}"
ip route get "${MULTICAST_TEST_IP}" || true

echo "[g1_robot_proxy] ping ${ROBOT_DEV_IP}"
if ! ping -c 1 -W 1 "${ROBOT_DEV_IP}"; then
  echo "[g1_robot_proxy][WARN] ping ${ROBOT_DEV_IP} failed; continuing anyway"
fi

echo "[g1_robot_proxy] starting python3 g1_robot_proxy.py"
exec python3 g1_robot_proxy.py
