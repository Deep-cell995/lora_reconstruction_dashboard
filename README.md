
# Predictive Reconstruction of Fragmented LoRa Telemetry

Prototype implementation for **Problem Statement 2** from the assignment PDF.

## Features
- Detects broken telemetry rows with missing coordinates
- Parses direct latitude/longitude rows
- Attempts auto-correction of partial JSON fragments
- Attempts parsing of NMEA GPGGA packets
- Estimates missing positions using previous valid points (`t-1`, `t-2`)
- Displays **verified**, **estimated**, and **unresolved** packets on an interactive map

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Logic summary
The prototype treats missing or fragmented telemetry as a continuity problem. It first tries to recover coordinates from the raw fragment. If recovery fails, it uses a historical buffer and a constant-velocity estimate derived from the last two valid points. Estimated positions are rendered differently from verified positions so analysts can maintain situational awareness while waiting for the next complete packet.
