#!/bin/bash
# cron_update_nodes.sh
# Script para actualizar periódicamente la base de datos del mecánico de ComfyUI
# Se ejecuta via cron o manualmente

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_PATH="$SCRIPT_DIR/comfyui_nodes.db"
LOG_FILE="$SCRIPT_DIR/logs/cron_update_nodes.log"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Create logs directory if needed
mkdir -p "$SCRIPT_DIR/logs"

echo "[$TIMESTAMP] Starting node DB update..." >> "$LOG_FILE"

# Verify ComfyUI is reachable
curl -s --max-time 5 http://127.0.0.1:8189/ | head -c 1 > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "[$TIMESTAMP] ComfyUI not reachable at http://127.0.0.1:8189" >> "$LOG_FILE"
    exit 1
fi

# Run the update script
cd "$SCRIPT_DIR"
python3 update_node_db.py >> "$LOG_FILE" 2>&1
if [ $? -eq 0 ]; then
    echo "[$TIMESTAMP] Update completed successfully" >> "$LOG_FILE"
else
    echo "[$TIMESTAMP] Update FAILED (exit code $?)" >> "$LOG_FILE"
    exit 1
fi
