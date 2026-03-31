#!/usr/bin/env python3
"""
MediaMTX custom Prometheus exporter — v3 API

Diseño:
- Campos instantáneos (RTT, bitrate, buffer) → Gauge, valor directo
- Campos acumulados (loss, drop, retrans, bytes) → el exporter calcula
  el delta entre polls y lo expone como tasa por segundo (Gauge de rate).
  Así Grafana recibe datos reales sin necesidad de delta() ni rate().
"""
import os, time, requests
from prometheus_client import start_http_server, Gauge

API  = os.getenv("MEDIAMTX_API", "http://host.docker.internal:9997")
PORT = int(os.getenv("EXPORTER_PORT", 9999))
POLL = int(os.getenv("POLL_INTERVAL", 1))

L = ["path", "conn_id"]

# --- Métricas instantáneas (van y vienen) ---
srt_rtt          = Gauge("mtx_srt_rtt_ms",             "RTT ms",                     L)
srt_recv_rate    = Gauge("mtx_srt_recv_rate_mbps",     "Recv rate Mbps",             L)
srt_link_cap     = Gauge("mtx_srt_link_capacity_mbps", "Link capacity Mbps",         L)
srt_rcv_buf_ms   = Gauge("mtx_srt_recv_buf_ms",        "Recv buffer ms",             L)
srt_rcv_delay    = Gauge("mtx_srt_recv_tsb_delay_ms",  "TSB PD delay ms (latencia)", L)
srt_loss_rate_pct= Gauge("mtx_srt_loss_recv_rate_pct", "Loss rate % (libsrt)",       L)
srt_connected    = Gauge("mtx_srt_connected",           "Stream conectado",           ["path","conn_id","remote_addr","state"])

# --- Tasas derivadas: delta/segundo calculado por el exporter ---
# (son Gauges que representan paquetes/segundo o bytes/segundo)
srt_loss_per_sec    = Gauge("mtx_srt_loss_per_sec",    "Paquetes perdidos/s",        L)
srt_drop_per_sec    = Gauge("mtx_srt_drop_per_sec",    "Drops irrecuperables/s",     L)
srt_retrans_per_sec = Gauge("mtx_srt_retrans_per_sec", "Retransmisiones recibidas/s",L)
srt_recv_per_sec    = Gauge("mtx_srt_recv_per_sec",    "Paquetes recibidos/s",       L)
srt_bytes_per_sec   = Gauge("mtx_srt_bytes_per_sec",   "Bytes recibidos/s",          L)

# --- Acumulados (para contexto histórico de la sesión) ---
srt_loss_total   = Gauge("mtx_srt_pkt_loss_total",     "Loss acumulado sesión",      L)
srt_drop_total   = Gauge("mtx_srt_pkt_drop_total",     "Drops acumulados sesión",    L)
srt_retrans_total= Gauge("mtx_srt_pkt_retrans_total",  "Retrans acumuladas sesión",  L)

# --- Paths ---
path_ready       = Gauge("mtx_path_ready",              "Path con fuente activa",     ["path"])
path_readers     = Gauge("mtx_path_readers",            "Readers en path",            ["path"])

# --- HLS ---
hls_bytes_sent   = Gauge("mtx_hls_bytes_sent",          "HLS bytes enviados",         ["path"])

SRT_INST_GAUGES = [srt_rtt, srt_recv_rate, srt_link_cap, srt_rcv_buf_ms,
                   srt_rcv_delay, srt_loss_rate_pct]
SRT_RATE_GAUGES = [srt_loss_per_sec, srt_drop_per_sec, srt_retrans_per_sec,
                   srt_recv_per_sec, srt_bytes_per_sec]
SRT_ACCUM_GAUGES= [srt_loss_total, srt_drop_total, srt_retrans_total]
ALL_SRT_GAUGES  = SRT_INST_GAUGES + SRT_RATE_GAUGES + SRT_ACCUM_GAUGES

PATH_GAUGES     = [path_ready, path_readers]
HLS_GAUGES      = [hls_bytes_sent]

# Estado anterior para calcular deltas
# { (path, conn_id): { campo: valor_anterior, "_ts": timestamp } }
prev_state = {}
prev_paths     = set()
prev_hls_paths = set()
prev_conn_ids  = set()

def g(d, *keys):
    for k in keys:
        v = d.get(k)
        if v is not None:
            try: return float(v)
            except: pass
    return 0.0

def per_sec(curr, prev, dt):
    """Delta por segundo entre dos valores acumulados."""
    delta = curr - prev
    if delta < 0:  # reset de conexión
        return 0.0
    return delta / dt if dt > 0 else 0.0

def collect():
    global prev_state, prev_paths, prev_hls_paths, prev_conn_ids

    now = time.monotonic()
    curr_conn_ids = set()

    try:
        items = requests.get(f"{API}/v3/srtconns/list", timeout=3).json().get("items", [])
        for item in items:
            cid  = item["id"]
            path = item.get("path", "")
            ra   = item.get("remoteAddr", "")
            st   = item.get("state", "")
            lbl  = [path, cid]
            key  = (path, cid)
            curr_conn_ids.add((path, cid, ra, st))

            # Valores acumulados del API
            loss    = g(item, "packetsReceivedLoss")
            drop    = g(item, "packetsReceivedDrop")
            retrans = g(item, "packetsReceivedRetrans")
            recv    = g(item, "packetsReceived")
            brecv   = g(item, "bytesReceived")

            # Métricas instantáneas
            srt_connected.labels(path, cid, ra, st).set(1)
            srt_rtt.labels(*lbl).set(g(item, "msRTT"))
            srt_recv_rate.labels(*lbl).set(g(item, "mbpsReceiveRate"))
            srt_link_cap.labels(*lbl).set(g(item, "mbpsLinkCapacity"))
            srt_rcv_buf_ms.labels(*lbl).set(g(item, "msReceiveBuf"))
            srt_rcv_delay.labels(*lbl).set(g(item, "msReceiveTsbPdDelay"))
            srt_loss_rate_pct.labels(*lbl).set(g(item, "packetsReceivedLossRate"))

            # Acumulados de sesión
            srt_loss_total.labels(*lbl).set(loss)
            srt_drop_total.labels(*lbl).set(drop)
            srt_retrans_total.labels(*lbl).set(retrans)

            # Tasas por segundo
            if key in prev_state:
                ps = prev_state[key]
                dt = now - ps["_ts"]
                srt_loss_per_sec.labels(*lbl).set(per_sec(loss,    ps["loss"],    dt))
                srt_drop_per_sec.labels(*lbl).set(per_sec(drop,    ps["drop"],    dt))
                srt_retrans_per_sec.labels(*lbl).set(per_sec(retrans, ps["retrans"], dt))
                srt_recv_per_sec.labels(*lbl).set(per_sec(recv,    ps["recv"],    dt))
                srt_bytes_per_sec.labels(*lbl).set(per_sec(brecv,  ps["brecv"],   dt))
            else:
                # Primera vez que vemos esta conexión — tasa desconocida
                for gauge in SRT_RATE_GAUGES:
                    gauge.labels(*lbl).set(0)

            prev_state[key] = {"loss": loss, "drop": drop, "retrans": retrans,
                               "recv": recv, "brecv": brecv, "_ts": now}

    except Exception as e:
        print(f"[srt] {e}", flush=True)

    # Limpiar conexiones que ya no existen
    for (path, cid, ra, st) in prev_conn_ids - curr_conn_ids:
        lbl = [path, cid]
        for gauge in ALL_SRT_GAUGES:
            try: gauge.remove(*lbl)
            except: pass
        try: srt_connected.remove(path, cid, ra, st)
        except: pass
        prev_state.pop((path, cid), None)
    prev_conn_ids = curr_conn_ids

    # --- Paths ---
    curr_paths = set()
    try:
        for item in requests.get(f"{API}/v3/paths/list", timeout=3).json().get("items", []):
            n = item["name"]
            curr_paths.add(n)
            readers = item.get("readers", [])
            path_ready.labels(n).set(1 if item.get("ready") else 0)
            path_readers.labels(n).set(len(readers) if isinstance(readers, list) else int(readers))
    except Exception as e:
        print(f"[paths] {e}", flush=True)

    for n in prev_paths - curr_paths:
        for gauge in PATH_GAUGES:
            try: gauge.remove(n)
            except: pass
    prev_paths = curr_paths

    # --- HLS ---
    curr_hls_paths = set()
    try:
        for item in requests.get(f"{API}/v3/hlsmuxers/list", timeout=3).json().get("items", []):
            n = item.get("path", "unknown")
            curr_hls_paths.add(n)
            hls_bytes_sent.labels(n).set(g(item, "bytesSent"))
    except Exception as e:
        print(f"[hls] {e}", flush=True)

    for n in prev_hls_paths - curr_hls_paths:
        for gauge in HLS_GAUGES:
            try: gauge.remove(n)
            except: pass
    prev_hls_paths = curr_hls_paths

if __name__ == "__main__":
    start_http_server(PORT)
    print(f"Exporter :{PORT} → {API} cada {POLL}s", flush=True)
    while True:
        collect()
        time.sleep(POLL)
