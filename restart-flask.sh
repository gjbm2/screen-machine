#!/bin/bash
pkill -f "python3 app.py"
sleep 1
.venv/bin/python3 app.py
