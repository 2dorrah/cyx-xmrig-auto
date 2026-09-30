#!/data/data/com.termux/files/usr/bin/bash
set -u
cd "$(dirname "$0")"
: "${CYX_WALLET:?Set CYX_WALLET=YOUR_WALLET_OR_WORKER first}"
exec python cythanx-xmrig-batch.py --mine --profile intensive --user "$CYX_WALLET" "$@"
