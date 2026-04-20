
import json
from pathlib import Path

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

st.set_page_config(page_title="LoRa Telemetry Reconstruction Dashboard", layout="wide")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

def parse_nmea_gpgga(line: str):
    try:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6 or not parts[0].endswith("GGA"):
            return None
        raw_lat, lat_dir, raw_lon, lon_dir = parts[2], parts[3], parts[4], parts[5]
        if not raw_lat or not raw_lon or not lat_dir or not lon_dir:
            return None
        lat_deg = float(raw_lat[:2]); lat_min = float(raw_lat[2:])
        lon_deg = float(raw_lon[:3]); lon_min = float(raw_lon[3:])
        latitude = lat_deg + lat_min / 60.0
        longitude = lon_deg + lon_min / 60.0
        if lat_dir.upper() == "S":
            latitude *= -1
        if lon_dir.upper() == "W":
            longitude *= -1
        return {"latitude": latitude, "longitude": longitude}
    except Exception:
        return None

def try_parse_fragment(fragment: str):
    result = {
        "raw_fragment": fragment,
        "fragment_type": "UNKNOWN",
        "latitude": None,
        "longitude": None,
        "is_broken": True,
        "parse_note": "Could not parse fragment",
    }
    if not isinstance(fragment, str) or not fragment.strip():
        result["parse_note"] = "Empty fragment"
        return result
    fragment = fragment.strip()
    try:
        obj = json.loads(fragment)
        result["fragment_type"] = "JSON"
        result["latitude"] = obj.get("latitude")
        result["longitude"] = obj.get("longitude")
        result["is_broken"] = pd.isna(result["latitude"]) or pd.isna(result["longitude"])
        result["parse_note"] = "Complete JSON parsed" if not result["is_broken"] else "JSON missing coordinates"
        return result
    except Exception:
        pass
    if fragment.startswith("{"):
        result["fragment_type"] = "JSON"
        repaired = fragment if fragment.endswith("}") else fragment + "}"
        repaired = repaired.replace("'", '"')
        try:
            obj = json.loads(repaired)
            result["latitude"] = obj.get("latitude")
            result["longitude"] = obj.get("longitude")
            result["is_broken"] = pd.isna(result["latitude"]) or pd.isna(result["longitude"])
            result["parse_note"] = "Partial JSON auto-corrected"
            return result
        except Exception:
            result["parse_note"] = "Incomplete JSON detected"
            return result
    if fragment.startswith("$"):
        result["fragment_type"] = "NMEA"
        parsed = parse_nmea_gpgga(fragment)
        if parsed:
            result["latitude"] = parsed["latitude"]
            result["longitude"] = parsed["longitude"]
            result["is_broken"] = False
            result["parse_note"] = "NMEA sentence parsed"
            return result
        result["parse_note"] = "Malformed or partial NMEA sentence"
        return result
    return result

def estimate_position(history):
    valid = [h for h in history if h.get("verified")]
    if len(valid) >= 2:
        p1, p2 = valid[-1], valid[-2]
        dlat = p1["latitude"] - p2["latitude"]
        dlon = p1["longitude"] - p2["longitude"]
        return p1["latitude"] + dlat, p1["longitude"] + dlon, "t-1,t-2 velocity extrapolation"
    if len(valid) == 1:
        p1 = valid[-1]
        return p1["latitude"], p1["longitude"], "carry-forward last valid point"
    return None, None, "insufficient history"

def load_sample_telemetry():
    return pd.read_csv(DATA_DIR / "telemetry.csv")

def normalize_manual_upload(uploaded_file):
    name = uploaded_file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file)
    if name.endswith(".json"):
        payload = json.load(uploaded_file)
        if isinstance(payload, list):
            return pd.DataFrame(payload)
        if isinstance(payload, dict):
            return pd.DataFrame([payload])
    raise ValueError("Please upload CSV or JSON telemetry files")

def reconstruct(df: pd.DataFrame):
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    if "time" in df.columns and "timestamp" not in df.columns:
        df.rename(columns={"time": "timestamp"}, inplace=True)
    if "node" in df.columns and "node_id" not in df.columns:
        df.rename(columns={"node": "node_id"}, inplace=True)
    if "node_id" not in df.columns:
        df["node_id"] = "NODE-1"
    if "timestamp" not in df.columns:
        df["timestamp"] = range(1, len(df) + 1)
    if "raw_fragment" not in df.columns:
        df["raw_fragment"] = None

    records = []
    for node_id, group in df.sort_values("timestamp").groupby("node_id", dropna=False):
        history = []
        for _, row in group.iterrows():
            lat = row.get("latitude")
            lon = row.get("longitude")
            fragment = row.get("raw_fragment")
            fragment_info = {
                "raw_fragment": fragment,
                "fragment_type": "DIRECT",
                "latitude": lat,
                "longitude": lon,
                "is_broken": pd.isna(lat) or pd.isna(lon),
                "parse_note": "Direct coordinate row" if not (pd.isna(lat) or pd.isna(lon)) else "Missing coordinate row",
            }
            if (pd.isna(lat) or pd.isna(lon)) and isinstance(fragment, str):
                fragment_info = try_parse_fragment(fragment)
                lat, lon = fragment_info["latitude"], fragment_info["longitude"]
            verified = not (pd.isna(lat) or pd.isna(lon))
            if verified:
                final_lat, final_lon = float(lat), float(lon)
                status = "VERIFIED"
                method = fragment_info["parse_note"]
            else:
                est_lat, est_lon, method = estimate_position(history)
                final_lat, final_lon = est_lat, est_lon
                status = "ESTIMATED" if est_lat is not None and est_lon is not None else "UNRESOLVED"
            record = row.to_dict()
            record.update({
                "fragment_type": fragment_info["fragment_type"],
                "parse_note": fragment_info["parse_note"],
                "verified_latitude": lat if verified else None,
                "verified_longitude": lon if verified else None,
                "final_latitude": final_lat,
                "final_longitude": final_lon,
                "status": status,
                "estimation_method": method,
            })
            records.append(record)
            if final_lat is not None and final_lon is not None:
                history.append({
                    "timestamp": row["timestamp"],
                    "latitude": final_lat,
                    "longitude": final_lon,
                    "verified": status == "VERIFIED",
                })
    return pd.DataFrame(records).sort_values(["node_id", "timestamp"])

def marker_color(status: str):
    return {"VERIFIED":"green","ESTIMATED":"red","UNRESOLVED":"gray"}.get(status,"blue")

st.title("Predictive Reconstruction of Fragmented LoRa Telemetry")
st.caption("Prototype implementation for Problem Statement 2: detect fragmented packets, predict missing positions, and preserve visual continuity on a dashboard.")

with st.sidebar:
    st.header("Data Sources")
    use_sample = st.checkbox("Use sample telemetry", value=True)
    uploaded = st.file_uploader("Upload CSV or JSON telemetry", type=["csv", "json"])
    st.markdown("---")
    st.header("Filters")
    selected_status = st.multiselect("Show statuses", ["VERIFIED", "ESTIMATED", "UNRESOLVED"], default=["VERIFIED", "ESTIMATED", "UNRESOLVED"])
    selected_nodes = st.text_input("Node filter (comma-separated optional)", "")
    st.markdown("---")
    st.markdown("- **Green** → verified packet\n- **Red** → estimated position\n- **Gray** → unresolved fragment")

if uploaded is not None:
    telemetry = normalize_manual_upload(uploaded)
elif use_sample:
    telemetry = load_sample_telemetry()
else:
    st.info("Upload a telemetry file or enable sample telemetry.")
    st.stop()

reconstructed = reconstruct(telemetry)

if selected_nodes.strip():
    node_values = [n.strip() for n in selected_nodes.split(",") if n.strip()]
    reconstructed = reconstructed[reconstructed["node_id"].astype(str).isin(node_values)]

reconstructed = reconstructed[reconstructed["status"].isin(selected_status)]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Packets", len(reconstructed))
c2.metric("Verified", int((reconstructed["status"] == "VERIFIED").sum()))
c3.metric("Estimated", int((reconstructed["status"] == "ESTIMATED").sum()))
c4.metric("Unresolved", int((reconstructed["status"] == "UNRESOLVED").sum()))

st.subheader("Reconstructed Telemetry Table")
visible_cols = [
    "timestamp","node_id","raw_fragment","fragment_type","parse_note",
    "verified_latitude","verified_longitude","final_latitude","final_longitude",
    "status","estimation_method"
]
st.dataframe(reconstructed[[c for c in visible_cols if c in reconstructed.columns]], use_container_width=True)

st.subheader("Operational Map")
mapped = reconstructed.dropna(subset=["final_latitude", "final_longitude"]).copy()
if mapped.empty:
    st.warning("No mappable coordinates available.")
else:
    center_lat = mapped["final_latitude"].mean()
    center_lon = mapped["final_longitude"].mean()
    m = folium.Map(location=[center_lat, center_lon], zoom_start=5)
    for _, row in mapped.iterrows():
        popup = f'''
        <b>Node:</b> {row["node_id"]}<br>
        <b>Timestamp:</b> {row["timestamp"]}<br>
        <b>Status:</b> {row["status"]}<br>
        <b>Fragment Type:</b> {row["fragment_type"]}<br>
        <b>Parse Note:</b> {row["parse_note"]}<br>
        <b>Estimation:</b> {row["estimation_method"]}<br>
        <b>Latitude:</b> {row["final_latitude"]:.6f}<br>
        <b>Longitude:</b> {row["final_longitude"]:.6f}
        '''
        folium.CircleMarker(
            location=[row["final_latitude"], row["final_longitude"]],
            radius=7, color=marker_color(row["status"]),
            fill=True, fill_opacity=0.9,
            popup=folium.Popup(popup, max_width=320),
            tooltip=f'{row["node_id"]} | {row["status"]}'
        ).add_to(m)
    for node_id, group in mapped.sort_values("timestamp").groupby("node_id"):
        coords = group[["final_latitude", "final_longitude"]].values.tolist()
        if len(coords) >= 2:
            folium.PolyLine(coords, weight=2, opacity=0.7, tooltip=f"Track: {node_id}").add_to(m)
    st_folium(m, width=None, height=520)

st.markdown("""
### How this matches the PDF
- Detects incomplete telemetry using missing coordinates, malformed JSON, or partial NMEA.
- Applies auto-correction for partial JSON when possible.
- Uses historical buffer logic (`t-1`, `t-2`) to estimate fragmented positions.
- Keeps the dashboard visually continuous by marking estimated points separately from verified ones.
""")
