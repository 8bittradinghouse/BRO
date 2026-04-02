#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root via: sudo bash $0"
  exit 1
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="/tmp/bro_hardening_${TS}.log"
SERVER_IP="${SERVER_IP:-45.77.177.47}"
SSH_USER="odah"
SSH_HOME="/home/${SSH_USER}"
SSH_DIR="${SSH_HOME}/.ssh"
AUTH_KEYS="${SSH_DIR}/authorized_keys"

exec > >(tee -a "${LOG}") 2>&1

run() {
  echo
  echo "+ $*"
  "$@"
}

say() {
  echo
  echo "== $* =="
}

require_key_confirmation() {
  local reply
  local normalized
  echo
  read -r -p "Type KEY_OK only after key login succeeds in a second terminal: " reply
  normalized="${reply//-/_}"
  normalized="${normalized^^}"
  if [[ "${normalized}" != "KEY_OK" ]]; then
    echo "Aborting: key login not confirmed."
    exit 2
  fi
}

say "STEP 0 - SAFETY SNAPSHOT"
run whoami
run hostname
run date -u

SSHD_BAK0="/etc/ssh/sshd_config.prehardening.${TS}"
run cp -a /etc/ssh/sshd_config "${SSHD_BAK0}"
echo "Backup created: ${SSHD_BAK0}"
run ufw status numbered

say "STEP 1 - SYSTEM UPDATES (NO REBOOT)"
run apt-get update
echo
echo "+ apt-get -y upgrade | tee /tmp/bro_hardening_upgrade.log"
apt-get -y upgrade | tee /tmp/bro_hardening_upgrade.log
run bash -lc "apt list --upgradable 2>/dev/null"
if [[ -f /var/run/reboot-required ]]; then
  echo "reboot_required"
else
  echo "reboot_not_required"
fi

say "STEP 2 - VERIFY SSH KEY ACCESS"
run install -d -m 700 "${SSH_DIR}"
run touch "${AUTH_KEYS}"
run chmod 600 "${AUTH_KEYS}"
run chown -R "${SSH_USER}:${SSH_USER}" "${SSH_DIR}"
run ls -ld "${SSH_DIR}"
run ls -l "${AUTH_KEYS}"

if [[ ! -s "${AUTH_KEYS}" ]]; then
  echo
  echo "No authorized keys found for ${SSH_USER}."
  echo "Add a public key, then verify login from a SECOND terminal:"
  echo "  ssh -i ~/.ssh/<keyname> ${SSH_USER}@${SERVER_IP}"
  echo "After adding key and validating second-terminal login, rerun this script."
  exit 3
fi

echo
echo "Current authorized_keys fingerprints:"
while IFS= read -r keyline; do
  [[ -z "${keyline}" ]] && continue
  printf '%s\n' "${keyline}" | ssh-keygen -lf /dev/stdin
done < "${AUTH_KEYS}"

echo
echo "Critical safety check:"
echo "1) Open a SECOND terminal."
echo "2) Login with key auth:"
echo "   ssh -i ~/.ssh/<keyname> ${SSH_USER}@${SERVER_IP}"
require_key_confirmation

say "STEP 3 - INSTALL FAIL2BAN"
run apt-get install -y fail2ban
run install -d -m 755 /etc/fail2ban/jail.d

F2B_JAIL="/etc/fail2ban/jail.d/sshd.local"
if [[ -f "${F2B_JAIL}" ]]; then
  F2B_BAK="${F2B_JAIL}.prehardening.${TS}"
  run cp -a "${F2B_JAIL}" "${F2B_BAK}"
  echo "Backup created: ${F2B_BAK}"
fi

echo
echo "+ cat > ${F2B_JAIL}"
cat > "${F2B_JAIL}" <<'EOF'
[sshd]
enabled = true
port = ssh
backend = systemd
maxretry = 5
findtime = 10m
bantime = 1h
bantime.increment = true
bantime.rndtime = 5m
EOF

run systemctl enable --now fail2ban
run fail2ban-client status
run fail2ban-client status sshd

say "STEP 4 - HARDEN SSH CONFIG"
echo "Rollback path if needed:"
echo "  cp -a /etc/ssh/sshd_config.pre_step4.${TS} /etc/ssh/sshd_config"
echo "  sshd -t && systemctl reload <ssh_service_unit>"

SSHD_BAK4="/etc/ssh/sshd_config.pre_step4.${TS}"
run cp -a /etc/ssh/sshd_config "${SSHD_BAK4}"
echo "Backup created: ${SSHD_BAK4}"

run sed -ri '/^\s*(PubkeyAuthentication|PasswordAuthentication|PermitRootLogin|KbdInteractiveAuthentication|ChallengeResponseAuthentication|MaxAuthTries|AllowUsers)\s+/Id' /etc/ssh/sshd_config
echo
echo "+ append explicit BRO hardening block to /etc/ssh/sshd_config"
cat >> /etc/ssh/sshd_config <<EOF

# BRO hardening block ${TS}
PubkeyAuthentication yes
PasswordAuthentication no
PermitRootLogin no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
MaxAuthTries 3
AllowUsers ${SSH_USER}
EOF

# Ensure drop-in files do not force password auth before main config is read.
for conf in /etc/ssh/sshd_config.d/*.conf; do
  [[ -e "${conf}" ]] || continue
  if grep -qi '^\s*PasswordAuthentication\s\+' "${conf}"; then
    conf_bak="${conf}.prehardening.${TS}"
    run cp -a "${conf}" "${conf_bak}"
    echo "Backup created: ${conf_bak}"
    run sed -ri 's/^\s*PasswordAuthentication\s+.*/PasswordAuthentication no/I' "${conf}"
  fi
done

run sshd -t

echo
echo "+ detect SSH service unit"
SSH_UNIT="$(systemctl list-unit-files | awk '$1=="ssh.service"{print "ssh"} $1=="sshd.service"{print "sshd"}' | head -n1)"
if [[ -z "${SSH_UNIT}" ]]; then
  echo "Could not detect ssh service unit (ssh vs sshd). Aborting."
  exit 4
fi
echo "Detected SSH unit: ${SSH_UNIT}"
run systemctl reload "${SSH_UNIT}"

echo
echo "Re-validate second-terminal key session is still connected."
require_key_confirmation

say "STEP 5 - FIREWALL NOISE REDUCTION"
run ufw status numbered

mapfile -t allow_rules < <(
  ufw status numbered \
    | sed -n 's/^\[\s*\([0-9]\+\)\].*22\/tcp.*ALLOW IN.*/\1/p' \
    | sort -rn
)
for idx in "${allow_rules[@]}"; do
  run ufw --force delete "${idx}"
done

if ufw status | grep -qE '^22/tcp[[:space:]]+LIMIT IN'; then
  echo "UFW limit rule for 22/tcp already present."
else
  run ufw limit 22/tcp
fi

run ufw status verbose

say "STEP 6 - VERIFY UNATTENDED UPGRADES"
run systemctl is-enabled unattended-upgrades
run systemctl is-active unattended-upgrades

AUTO_FILE="/etc/apt/apt.conf.d/20auto-upgrades"
run bash -lc "grep -E 'APT::Periodic::(Update-Package-Lists|Unattended-Upgrade)' ${AUTO_FILE} || true"

if ! grep -q 'APT::Periodic::Update-Package-Lists "1";' "${AUTO_FILE}" 2>/dev/null || \
   ! grep -q 'APT::Periodic::Unattended-Upgrade "1";' "${AUTO_FILE}" 2>/dev/null; then
  AUTO_BAK="${AUTO_FILE}.prehardening.${TS}"
  if [[ -f "${AUTO_FILE}" ]]; then
    run cp -a "${AUTO_FILE}" "${AUTO_BAK}"
    echo "Backup created: ${AUTO_BAK}"
  fi
  echo
  echo "+ write ${AUTO_FILE}"
  cat > "${AUTO_FILE}" <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
fi

say "STEP 7 - FINAL VERIFICATION"
run bash -lc "sshd -T | grep -E '^(permitrootlogin|passwordauthentication|pubkeyauthentication|allowusers|maxauthtries|kbdinteractiveauthentication|challengeresponseauthentication)'"
run fail2ban-client status sshd
run ufw status verbose
run bash -lc "ss -tulpen | grep ':22'"
run last -n 10

say "HARDENING COMPLETE"
echo "Log file: ${LOG}"
echo "Primary backups:"
echo "  ${SSHD_BAK0}"
echo "  ${SSHD_BAK4}"
if [[ -n "${F2B_BAK:-}" ]]; then
  echo "  ${F2B_BAK}"
fi
if [[ -n "${AUTO_BAK:-}" ]]; then
  echo "  ${AUTO_BAK}"
fi
