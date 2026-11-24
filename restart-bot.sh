#!/bin/bash

# ------------------------------------------------------------
# Script de reinicio automático para el bot de trading
# - Hace git stash pull (guarda cambios locales si los hay)
# - Actualiza dependencias (si requirements.txt cambió)
# - Verifica conectividad con Binance
# - Reinicia el servicio systemd tradingbot
# ------------------------------------------------------------

REPO_DIR="/root/trading-bot"
TMUX_SESSION="tradingbot"  # legado: solo para matar sesiones viejas
SERVICE="tradingbot"
BRANCH="main"
LOG_FILE="/var/log/trading-bot-restart.log"

# Función para loguear y mostrar en consola
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1"
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOG_FILE"
}

# Verificar que el directorio existe
if [ ! -d "$REPO_DIR" ]; then
    log "❌ Error: No se encuentra el directorio $REPO_DIR"
    exit 1
fi

cd "$REPO_DIR" || exit 1

# ------------------------------------------------------------
# 1. Guardar cambios locales no commiteados (stash)
# ------------------------------------------------------------
log "📦 Verificando cambios locales no guardados..."
if ! git diff --quiet || ! git diff --cached --quiet; then
    log "⚠️ Se detectaron cambios locales sin commit. Haciendo git stash..."
    git stash push -m "Auto-stash antes de reiniciar bot" >> "$LOG_FILE" 2>&1
    STASHED=true
else
    log "✅ No hay cambios locales pendientes."
    STASHED=false
fi

# ------------------------------------------------------------
# 2. Hacer git pull
# ------------------------------------------------------------
log "📡 Verificando cambios en Git (rama $BRANCH)..."

# Guardar commit actual antes del pull
BEFORE=$(git rev-parse HEAD 2>/dev/null)
if [ $? -ne 0 ]; then
    log "❌ Error: No es un repositorio git válido en $REPO_DIR"
    exit 1
fi

# Hacer pull
git pull origin "$BRANCH" >> "$LOG_FILE" 2>&1
if [ $? -ne 0 ]; then
    log "❌ Error al hacer git pull. Revisa manualmente."
    # Si hubo error, restaurar stash si existe
    if [ "$STASHED" = true ]; then
        git stash pop >> "$LOG_FILE" 2>&1
        log "🔄 Cambios locales restaurados."
    fi
    exit 1
fi

# Comprobar si hubo cambios
AFTER=$(git rev-parse HEAD)
if [ "$BEFORE" = "$AFTER" ]; then
    log "✅ No hay nuevos commits. No se requiere actualización de código."
else
    log "🔄 Nuevos commits detectados. Código actualizado."
fi

# ------------------------------------------------------------
# 3. Actualizar dependencias (si existe requirements.txt)
# ------------------------------------------------------------
if [ -f "requirements.txt" ]; then
    log "📦 Actualizando dependencias Python..."
    # Verificar/activar entorno virtual
    if [ ! -d "venv" ]; then
        log "⚠️ No se encontró el entorno virtual 'venv'. Creándolo..."
        python3 -m venv venv
    fi
    source venv/bin/activate
    pip install --upgrade pip >> "$LOG_FILE" 2>&1
    pip install -r requirements.txt >> "$LOG_FILE" 2>&1
    if [ $? -eq 0 ]; then
        log "✅ Dependencias actualizadas correctamente."
    else
        log "❌ Error al instalar dependencias. Revisa requirements.txt"
        exit 1
    fi
    deactivate
else
    log "⚠️ No se encontró requirements.txt. Omitiendo actualización de dependencias."
fi

# ------------------------------------------------------------
# 4. Verificar conectividad con Binance
# ------------------------------------------------------------
log "🌐 Verificando conectividad con Binance API..."
BINANCE_PING=$(curl -s -o /dev/null -w "%{http_code}" https://api.binance.com/api/v3/ping)
if [ "$BINANCE_PING" -eq 200 ]; then
    log "✅ Conexión a Binance OK."
else
    log "❌ No se pudo conectar a Binance (HTTP $BINANCE_PING). Revisa tu red."
    exit 1
fi

# ------------------------------------------------------------
# 5. Reiniciar el bot vía systemd (arranca solo tras reboot/crash)
# ------------------------------------------------------------
if [ ! -d "$REPO_DIR/venv" ]; then
    log "❌ Error: No se encontró el entorno virtual en $REPO_DIR/venv"
    exit 1
fi

# Matar sesión tmux legada si existe, para no correr dos instancias
if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
    log "⚡ Matando sesión tmux legada: $TMUX_SESSION"
    tmux kill-session -t "$TMUX_SESSION"
    sleep 1
fi

log "🔧 Instalando/actualizando unidad systemd $SERVICE..."
cp "$REPO_DIR/deploy/$SERVICE.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE" >> "$LOG_FILE" 2>&1

log "🔄 Reiniciando servicio $SERVICE..."
systemctl restart "$SERVICE"
sleep 3

if systemctl is-active --quiet "$SERVICE"; then
    log "✅ Bot reiniciado exitosamente (systemd: $SERVICE)."
    log "📋 Logs: journalctl -u $SERVICE -f   |   tail -f $REPO_DIR/logs/bot.log"
else
    log "❌ El servicio $SERVICE no quedó activo. Revisa: journalctl -u $SERVICE -n 50"
    exit 1
fi

# Si se había hecho stash y no hubo errores, opcionalmente podemos eliminar el stash (o mantenerlo)
if [ "$STASHED" = true ]; then
    log "📌 Los cambios locales fueron guardados en stash. Para recuperarlos: git stash pop"
    # Si quieres restaurarlos automáticamente (sin conflictos) puedes descomentar:
    # git stash pop >> "$LOG_FILE" 2>&1 && log "✅ Cambios locales restaurados."
fi

exit 0
