#!/bin/bash
# Script para iniciar webhook_server.py al reinicio del sistema
cd /home/meisoft/ComfyUI-Hermes-Listener
nohup python3 webhook_server.py > /tmp/webhook_server.log 2>&1 &
echo "Webhook server started at $(date)" >> /tmp/webhook_startup.log
