#!/bin/bash
# Instantel 2 only (UM16368) — use sync_instantel_ftp.sh to sync both devices.

set -u

HOST="cp.dullesgeotechnical.com"
USER="dgmts"
PASS="1234"
REMOTE_DIR="/UM16368"
LOCAL_DIR="/root/root/ftp-server/Dulles Test/UM16368"

echo "$(date '+%Y-%m-%d %H:%M:%S') [UM16368] Starting mirror ${REMOTE_DIR} -> ${LOCAL_DIR}"
mkdir -p "$LOCAL_DIR"

lftp -u "$USER","$PASS" "ftp://${HOST}" <<EOF
set net:timeout 30
set net:max-retries 2
set ftp:ssl-allow no
mirror --verbose --continue --only-newer "${REMOTE_DIR}" "${LOCAL_DIR}"
bye
EOF

echo "$(date '+%Y-%m-%d %H:%M:%S') [UM16368] Done (exit $?)"
