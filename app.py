"""
Cell Phone Tracker - Production Server
Author: Senior Dev
Version: 2.0.0
"""

import os
import logging
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, request, jsonify, render_template_string, abort, session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(32))

PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "changeme")
API_KEY = os.environ.get("API_KEY", "changeme_api")
MAX_HISTORY = 20

location_history: list[dict] = []


def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-API-Key", "").strip()
        if not key or key != API_KEY:
            logger.warning("Clave API inválida. IP: %s", request.remote_addr)
            abort(401, description="API key inválida o ausente.")
        return f(*args, **kwargs)
    return decorated


def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M:%S UTC")


def render_panel(authenticated=False, error=False):
    latest = location_history[-1] if location_history else None
    return render_template_string(
        HTML_PANEL,
        authenticated=authenticated,
        error=error,
        latest=latest,
        history=list(reversed(location_history[-5:])),
    )


HTML_PANEL = """
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Rastreador · Panel</title>
  <style>
    :root {
      --bg:#0d1117;--surface:#161b22;--border:#30363d;
      --accent:#58a6ff;--danger:#f85149;--success:#3fb950;
      --text:#e6edf3;--muted:#8b949e;--radius:8px;
    }
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
    body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--bg);color:var(--text);min-height:100vh;}
    header{padding:16px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px;background:var(--surface);}
    header h1{font-size:1rem;font-weight:600;}
    .badge{font-size:.7rem;padding:2px 8px;border-radius:20px;background:var(--success);color:#000;font-weight:700;}
    .container{max-width:600px;margin:0 auto;padding:20px;}
    .login-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:32px 24px;margin-top:60px;}
    .login-card h2{font-size:1.1rem;margin-bottom:20px;}
    input[type=password]{width:100%;padding:10px 12px;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);color:var(--text);font-size:.95rem;margin-bottom:12px;outline:none;}
    .btn{display:block;width:100%;padding:10px;background:var(--accent);color:#000;border:none;border-radius:var(--radius);font-weight:600;font-size:.95rem;cursor:pointer;text-align:center;text-decoration:none;}
    .btn-ghost{background:transparent;color:var(--accent);border:1px solid var(--accent);}
    .error-msg{color:var(--danger);font-size:.85rem;margin-bottom:10px;}
    .card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:16px;margin-bottom:16px;}
    .card-title{font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin-bottom:12px;}
    .stat-row{display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--border);font-size:.88rem;}
    .stat-row:last-child{border-bottom:none;}
    .stat-label{color:var(--muted);}
    .stat-value{font-weight:600;color:var(--accent);font-family:monospace;}
    .map-frame{width:100%;height:280px;border-radius:var(--radius);overflow:hidden;border:1px solid var(--border);margin-bottom:16px;}
    .map-frame iframe{width:100%;height:100%;border:none;}
    .actions{display:flex;gap:10px;margin-bottom:20px;}
    .actions a{flex:1;}
    .no-data{text-align:center;padding:48px 20px;color:var(--muted);}
    .history-item{font-size:.8rem;color:var(--muted);padding:4px 0;font-family:monospace;}
    .battery-bar{height:6px;background:var(--border);border-radius:3px;margin-top:4px;overflow:hidden;}
    .battery-fill{height:100%;background:var(--success);border-radius:3px;}
  </style>
</head>
<body>
<header>
  <span>📍</span>
  <h1>Rastreador de Celular</h1>
  {% if authenticated and latest %}<span class="badge">EN VIVO</span>{% endif %}
</header>
<div class="container">
{% if not authenticated %}
  <div class="login-card">
    <h2>Acceso seguro</h2>
    {% if error %}<p class="error-msg">Contraseña incorrecta.</p>{% endif %}
    <form method="POST" action="/panel">
      <input type="password" name="password" placeholder="Contraseña" autofocus required>
      <button type="submit" class="btn">Entrar</button>
    </form>
  </div>
{% else %}
  {% if latest %}
    <div class="card">
      <p class="card-title">Última ubicación</p>
      <div class="stat-row"><span class="stat-label">Actualizado</span><span class="stat-value">{{ latest.fecha }}</span></div>
      <div class="stat-row"><span class="stat-label">Latitud</span><span class="stat-value">{{ latest.lat }}</span></div>
      <div class="stat-row"><span class="stat-label">Longitud</span><span class="stat-value">{{ latest.lon }}</span></div>
      <div class="stat-row"><span class="stat-label">Precisión</span><span class="stat-value">{{ latest.precision }}m</span></div>
      <div class="stat-row" style="flex-direction:column;align-items:flex-start;">
        <div style="display:flex;justify-content:space-between;width:100%">
          <span class="stat-label">Batería</span><span class="stat-value">{{ latest.bateria }}%</span>
        </div>
        <div class="battery-bar" style="width:100%"><div class="battery-fill" style="width:{{ latest.bateria }}%"></div></div>
      </div>
    </div>
    <div class="map-frame">
      <iframe src="https://www.openstreetmap.org/export/embed.html?bbox={{ latest.lon|float - 0.005 }},{{ latest.lat|float - 0.005 }},{{ latest.lon|float + 0.005 }},{{ latest.lat|float + 0.005 }}&layer=mapnik&marker={{ latest.lat }},{{ latest.lon }}" allowfullscreen></iframe>
    </div>
    <div class="actions">
      <a class="btn" href="https://maps.google.com/?q={{ latest.lat }},{{ latest.lon }}" target="_blank">Google Maps</a>
      <a class="btn btn-ghost" href="/panel">Actualizar</a>
    </div>
    {% if history|length > 1 %}
    <div class="card">
      <p class="card-title">Historial reciente</p>
      {% for loc in history %}<div class="history-item">{{ loc.fecha }} — {{ loc.lat }}, {{ loc.lon }} ({{ loc.bateria }}% bat)</div>{% endfor %}
    </div>
    {% endif %}
  {% else %}
    <div class="no-data"><p>📡</p><p>Sin datos aún.</p></div>
  {% endif %}
  <form method="POST" action="/logout" style="margin-top:8px;">
    <button type="submit" class="btn btn-ghost" style="font-size:.85rem;padding:8px;">Cerrar sesión</button>
  </form>
{% endif %}
</div>
</body>
</html>
"""


@app.route("/")
def index():
    return '<meta http-equiv="refresh" content="0;url=/panel">'


@app.route("/panel", methods=["GET", "POST"])
def panel():
    if request.method == "POST":
        pwd = request.form.get("password", "").strip()
        if pwd == PANEL_PASSWORD:
            session["authenticated"] = True
            logger.info("Login exitoso. IP: %s", request.remote_addr)
        else:
            logger.warning("Login fallido. IP: %s", request.remote_addr)
            return render_panel(authenticated=False, error=True)
    return render_panel(authenticated=bool(session.get("authenticated")))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return render_panel(authenticated=False)


@app.route("/ubicacion", methods=["POST"])
@require_api_key
def recibir_ubicacion():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Payload inválido."}), 400
    lat = data.get("lat")
    lon = data.get("lon")
    if lat is None or lon is None:
        return jsonify({"error": "lat y lon requeridos."}), 422
    try:
        lat = float(lat)
        lon = float(lon)
    except (ValueError, TypeError):
        return jsonify({"error": "lat/lon deben ser numéricos."}), 422
    entry = {
        "lat": round(lat, 7),
        "lon": round(lon, 7),
        "precision": data.get("precision", "?"),
        "bateria": data.get("bateria", "?"),
        "fecha": now_str(),
    }
    location_history.append(entry)
    if len(location_history) > MAX_HISTORY:
        location_history.pop(0)
    logger.info("Ubicación recibida: %.6f, %.6f bat=%s%%", lat, lon, entry["bateria"])
    return jsonify({"ok": True, "fecha": entry["fecha"]}), 200


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "registros": len(location_history),
        "ultima": location_history[-1]["fecha"] if location_history else None,
    })


@app.errorhandler(401)
def unauthorized(e):
    return jsonify({"error": str(e.description)}), 401


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Ruta no encontrada."}), 404


@app.errorhandler(500)
def server_error(e):
    logger.exception("Error interno.")
    return jsonify({"error": "Error interno del servidor."}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
