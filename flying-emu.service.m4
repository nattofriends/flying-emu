[Unit]
Description=Bridge from EMU-2 to MQTT
Wants=network-online.service
After=network-online.service

[Service]
Type=simple
ExecStart=/bin/sh -c venv/bin/flying-emu
WorkingDirectory=CURDIR
Restart=on-failure
RestartSec=5
StartLimitInterval=0

[Install]
WantedBy=default.target
