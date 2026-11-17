#!/bin/bash
# Double-click me (macOS): installs what is missing the first time, then opens the dashboard.
# If macOS refuses to open it the first time: right-click > Open > Open.
cd "$(dirname "$0")" || exit 1
exec bash ./start.sh
