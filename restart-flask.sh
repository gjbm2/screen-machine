#!/bin/bash
pkill -f "python app.py"
sleep 1
.venv/bin/python3 app.py
