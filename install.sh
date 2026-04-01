#!/usr/bin/env bash
set -e

REPO="https://github.com/jotapipol/srt-hls-monitor.git"
INSTALL_DIR="/opt/stacks/srt-monitor"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║        SRT Monitor — Instalador          ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""

# ── Root check ────────────────────────────────────────────
[ "$EUID" -ne 0 ] && error "Ejecutá este script como root (sudo bash install.sh)"

# ── Dependencias ──────────────────────────────────────────
info "Actualizando paquetes e instalando dependencias..."
apt-get update -qq
apt-get install -y -qq curl git ca-certificates gnupg lsb-release

# Docker
if ! command -v docker &>/dev/null; then
  info "Instalando Docker..."
  curl -fsSL https://get.docker.com | bash
  systemctl enable docker
  systemctl start docker
  success "Docker instalado"
else
  success "Docker ya instalado ($(docker --version | cut -d' ' -f3 | tr -d ','))"
fi

# Docker Compose plugin
if ! docker compose version &>/dev/null; then
  info "Instalando Docker Compose plugin..."
  apt-get install -y -qq docker-compose-plugin
  success "Docker Compose instalado"
else
  success "Docker Compose ya instalado ($(docker compose version --short))"
fi

# ── Clonar repo ───────────────────────────────────────────
if [ -d "$INSTALL_DIR/.git" ]; then
  warn "El directorio $INSTALL_DIR ya existe. Actualizando..."
  git -C "$INSTALL_DIR" pull
else
  info "Clonando repositorio..."
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone "$REPO" "$INSTALL_DIR"
  success "Repositorio clonado en $INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# ── Configuración interactiva ─────────────────────────────
echo ""
echo -e "${CYAN}── Configuración ──────────────────────────────${NC}"
echo "  Presioná Enter para aceptar el valor por defecto [entre corchetes]"
echo ""

prompt() {
  local label="$1"
  local default="$2"
  local var="$3"
  local secret="$4"
  local input=""
  if [ "$secret" = "1" ]; then
    read -rsp "  $label [$default]: " input </dev/tty 2>/dev/tty || true
    echo "" >/dev/tty
  else
    read -rp "  $label [$default]: " input </dev/tty 2>/dev/tty || true
  fi
  printf -v "$var" '%s' "${input:-$default}"
}

prompt "Puerto Grafana"          "3000"     GRAFANA_PORT
prompt "Usuario Grafana"         "admin"    GRAFANA_USER
prompt "Password Grafana"        "changeme" GRAFANA_PASSWORD 1
prompt "Puerto monitor UI"       "8900"     MONITOR_PORT
prompt "Puerto Speedtest"        "9800"     SPEEDTEST_PORT
prompt "Puerto Speedtest SSL"    "9801"     SPEEDTEST_PORT_SSL
prompt "Puerto Netdata"          "19999"    NETDATA_PORT

echo ""
info "Generando .env..."
cat > .env << EOF
GRAFANA_PORT=${GRAFANA_PORT}
GRAFANA_USER=${GRAFANA_USER}
GRAFANA_PASSWORD=${GRAFANA_PASSWORD}
MONITOR_PORT=${MONITOR_PORT}
SPEEDTEST_PORT=${SPEEDTEST_PORT}
SPEEDTEST_PORT_SSL=${SPEEDTEST_PORT_SSL}
NETDATA_PORT=${NETDATA_PORT}
NETDATA_CLAIM_TOKEN=
NETDATA_CLAIM_ROOMS=
EOF
success ".env generado"

# ── Directorios de datos ──────────────────────────────────
mkdir -p data/hls data/hls_preview
success "Directorios de datos creados"

# ── Build y deploy ────────────────────────────────────────
echo ""
info "Construyendo imagen del transcoder..."
docker compose build transcoder

echo ""
info "Levantando el stack..."
docker compose up -d

# ── Estado final ──────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         Stack levantado con éxito        ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""

IP=$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')

echo -e "  ${CYAN}Monitor UI${NC}      →  http://${IP}:${MONITOR_PORT}"
echo -e "  ${CYAN}Grafana${NC}         →  http://${IP}:${GRAFANA_PORT}  (${GRAFANA_USER} / ${GRAFANA_PASSWORD})"
echo -e "  ${CYAN}Netdata${NC}         →  http://${IP}:${NETDATA_PORT}"
echo -e "  ${CYAN}Speedtest${NC}       →  http://${IP}:${SPEEDTEST_PORT}"
echo ""
echo -e "  ${CYAN}SRT ingest${NC}      →  srt://${IP}:8890?streamid=publish:NOMBRE"
echo ""
echo -e "  ${YELLOW}Para activar transcode 480p (requiere hardware):${NC}"
echo -e "  Editar PREVIEW_MODE en monitor/html/index.html → 'transcode'"
echo -e "  luego: docker compose up -d transcoder"
echo ""
docker compose ps
