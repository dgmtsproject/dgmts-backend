#!/bin/bash
# Sync Instantel Micromate CSV folders from Dulles FTP to local VPS storage.
# Fixes the duplicate-script bug: run ONLY this file in cron (one credential block).

set -u

HOST="cp.dullesgeotechnical.com"
USER="dgmts"
PASS="1234"

BASE_LOCAL="/root/root/ftp-server/Dulles Test"
LOG_TAG="[instantel-ftp]"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') ${LOG_TAG} $*"
}

sync_device() {
  local remote_dir="$1"
  local local_dir="$2"

  mkdir -p "$local_dir"

  log "Starting mirror ${remote_dir} -> ${local_dir}"

  if lftp -u "$USER","$PASS" "ftp://${HOST}" <<EOF
set net:timeout 30
set net:max-retries 2
set ftp:ssl-allow no
mirror --verbose --continue --only-newer "${remote_dir}" "${local_dir}"
bye
EOF
  then
    log "OK ${remote_dir}"
    return 0
  else
    log "FAILED ${remote_dir} (exit $?)"
    return 1
  fi
}

FAIL=0

sync_device "/UM16368" "${BASE_LOCAL}/UM16368" || FAIL=1
sync_device "/UM15783" "${BASE_LOCAL}/UM15783" || FAIL=1

if [[ "$FAIL" -ne 0 ]]; then
  log "One or more mirrors failed"
  exit 1
fi

log "All mirrors completed successfully"
exit 0
