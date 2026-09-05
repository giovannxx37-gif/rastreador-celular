"""
Cell Phone Tracker — Production Server
Version : 4.1.0
Database: SQLite (dev) · PostgreSQL (prod) · Redis (cache)
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from functools import wraps
from typing import Any

from flask import Flask, jsonify, render_template_string, request, session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("tracker")


class Config:
    SECRET_KEY     = os.environ.get("SECRET_KEY", os.urandom(32).hex())
    PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "")
    API_KEY        = os.environ.get("API_KEY", "")
    RATE_LIMIT_RPM = int(os.environ.get("RATE_LIMIT_RPM", "10"))
    MAX_HISTORY    = int(os.environ.get("MAX_HISTORY", "200"))
    DATABASE_URL   = os.environ.get("DATABASE_URL", "")
    REDIS_URL      = os.environ.get("REDIS_URL", "")
    SQLITE_PATH    = os.environ.get("SQLITE_PATH", "/tmp/tracker.db")

    @classmethod
    def validate(cls):
        missing = [k for k in ("PANEL_PASSWORD", "API_KEY") if not getattr(cls, k)]
        if missing:
            raise RuntimeError(f"Variables faltantes: {missing}")

    @classmethod
    def db_backend(cls):
        if cls.DATABASE_URL:
            return "postgresql"
        if cls.REDIS_URL:
            return "redis"
        return "sqlite"

Config.validate()
logger.info("DB backend: %s", Config.db_backend())

app = Flask(__name__)
app.secret_key = Config.SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

_rate_store: dict[str, list[float]] = defaultdict(list)

def is_rate_limited(ip: str) -> bool:
    now = time.time()
    _rate_store[ip] = [t for t in _rate_store[ip] if now - t < 60]
    if len(_rate_store[ip]) >= Config.RATE_LIMIT_RPM:
        return True
    _rate_store[ip].append(now)
    return False


class Database:
    def __init__(self):
        self._backend = Config.db_backend()
        if self._backend == "postgresql":
            self._init_postgresql()
        elif self._backend == "redis":
            self._init_redis()
        else:
            self._init_sqlite()

    def _init_sqlite(self):
        import sqlite3
        self._path = Config.SQLITE_PATH
        conn = sqlite3.connect(self._path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS locations (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                lat        REAL NOT NULL,
                lon        REAL NOT NULL,
                precision  REAL,
                battery    INTEGER,
                ip         TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON locations(created_at DESC)")
        conn.commit()
        conn.close()
        logger.info("SQLite listo: %s", self._path)

    def _sqlite_conn(self):
        import sqlite3
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_postgresql(self):
        try:
            import psycopg2
            conn = psycopg2.connect(Config.DATABASE_URL)
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS locations (
                    id         SERIAL PRIMARY KEY,
                    lat        DOUBLE PRECISION NOT NULL,
                    lon        DOUBLE PRECISION NOT NULL,
                    precision  DOUBLE PRECISION,
                    battery    INTEGER,
                    ip         TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ts ON locations(created_at DESC)")
            conn.commit()
            conn.close()
            logger.info("PostgreSQL listo.")
        except Exception as e:
            logger.error("PostgreSQL falló (%s). Usando SQLite.", e)
            self._backend = "sqlite"
            self._init_sqlite()

    def _pg_conn(self):
        import psycopg2
        import psycopg2.extras
        return psycopg2.connect(Config.DATABASE_URL,
                                cursor_factory=psycopg2.extras.RealDictCursor)

    def _init_redis(self):
        try:
            import redis
            self._redis = redis.from_url(Config.REDIS_URL, decode_responses=True)
            self._redis.ping()
            self._rkey = "tracker:locations"
            logger.info("Redis listo.")
        except Exception as e:
            logger.error("Redis falló (%s). Usando SQLite.", e)
            self._backend = "sqlite"
            self._init_sqlite()

    def insert(self, entry: dict[str, Any]) -> None:
        if self._backend == "postgresql":
            conn = self._pg_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO locations (lat,lon,precision,battery,ip) VALUES (%s,%s,%s,%s,%s)",
                    (entry["lat"], entry["lon"], entry.get("precision"),
                     entry.get("battery"), entry.get("ip")),
                )
                cur.execute(
                    "DELETE FROM locations WHERE id NOT IN "
                    "(SELECT id FROM locations ORDER BY id DESC LIMIT %s)",
                    (Config.MAX_HISTORY,),
                )
                conn.commit()
            finally:
                conn.close()
        elif self._backend == "redis":
            import json
            self._redis.lpush(self._rkey, json.dumps(entry))
            self._redis.ltrim(self._rkey, 0, Config.MAX_HISTORY - 1)
        else:
            conn = self._sqlite_conn()
            try:
                conn.execute(
                    "INSERT INTO locations (lat,lon,precision,battery,ip,created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (entry["lat"], entry["lon"], entry.get("precision"),
                     entry.get("battery"), entry.get("ip"), entry["created_at"]),
                )
                conn.execute(
                    "DELETE FROM locations WHERE id NOT IN "
                    "(SELECT id FROM locations ORDER BY id DESC LIMIT ?)",
                    (Config.MAX_HISTORY,),
                )
                conn.commit()
            finally:
                conn.close()

    def latest(self) -> dict | None:
        if self._backend == "postgresql":
            conn = self._pg_conn()
            cur = conn.cursor()
            cur.execute("SELECT * FROM locations ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            conn.close()
            if row:
                r = dict(row)
                r["created_at"] = str(r.get("created_at", ""))
                return r
            return None
        elif self._backend == "redis":
            import json
            raw = self._redis.lindex(self._rkey, 0)
            return json.loads(raw) if raw else None
        else:
            conn = self._sqlite_conn()
            row = conn.execute(
                "SELECT * FROM locations ORDER BY id DESC LIMIT 1"
            ).fetchone()
            conn.close()
            return dict(row) if row else None

    def history(self, limit: int = 10) -> list[dict]:
        if self._backend == "postgresql":
            conn = self._pg_conn()
            cur = conn.cursor()
            cur.execute("SELECT * FROM locations ORDER BY id DESC LIMIT %s", (limit,))
            rows = cur.fetchall()
            conn.close()
            result = []
            for r in rows:
                d = dict(r)
                d["created_at"] = str(d.get("created_at", ""))
                result.append(d)
            return result
        elif self._backend == "redis":
            import json
            items = self._redis.lrange(self._rkey, 0, limit - 1)
            return [json.loads(i) for i in items]
        else:
            conn = self._sqlite_conn()
            rows = conn.execute(
                "SELECT * FROM locations ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]

    def count(self) -> int:
        if self._backend == "postgresql":
            conn = self._pg_conn()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as c FROM locations")
            row = cur.fetchone()
            conn.close()
            return row["c"] if row else 0
        elif self._backend == "redis":
            return self._redis.llen(self._rkey)
        else:
            conn = self._sqlite_conn()
            row = conn.execute("SELECT COUNT(*) as c FROM locations").fetchone()
            conn.close()
            return row["c"] if row else 0


db = Database()


def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        ip = request.remote_addr or "unknown"
        if is_rate_limited(ip):
            return jsonify({"error": "Demasiadas solicitudes."}), 429
        key = request.headers.get("X-API-Key", "").strip()
        if not key or key != Config.API_KEY:
            logger.warning("API key inválida. IP: %s", ip)
            return jsonify({"error": "No autorizado."}), 401
        return f(*args, **kwargs)
    return decorated


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


HTML = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Tracker · Panel</title>
  <style>
    :root{--bg:#0d1117;--s:#161b22;--b:#30363d;--a:#58a6ff;--err:#f85149;--ok:#3fb950;--t:#e6edf3;--m:#8b949e;--r:8px}
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}

    /* ---------- Fondo futurista (SVG + CSS, sin imágenes externas) ---------- */
    html,body{height:100%}
    body{
      font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      color:var(--t);
      min-height:100vh;
      position:relative;
      isolation:isolate;
      background-color:#060a0f;
      background-image:
        radial-gradient(circle at 15% 10%, rgba(63,185,80,.10) 0%, transparent 40%),
        radial-gradient(circle at 85% 15%, rgba(88,166,255,.16) 0%, transparent 45%),
        radial-gradient(circle at 50% 100%, rgba(88,166,255,.10) 0%, transparent 55%),
        linear-gradient(180deg,#05070a 0%,#0a0f16 40%,#080b10 100%);
      background-attachment:fixed;
      overflow-x:hidden;
    }
    /* grid tecnológico */
    body::before{
      content:"";
      position:fixed;
      inset:0;
      z-index:-2;
      background-image:
        linear-gradient(rgba(88,166,255,.07) 1px, transparent 1px),
        linear-gradient(90deg, rgba(88,166,255,.07) 1px, transparent 1px);
      background-size:42px 42px;
      -webkit-mask-image:radial-gradient(circle at 50% 30%, #000 0%, transparent 75%);
      mask-image:radial-gradient(circle at 50% 30%, #000 0%, transparent 75%);
      animation:gridDrift 30s linear infinite;
    }
    @keyframes gridDrift{
      0%{background-position:0 0,0 0}
      100%{background-position:42px 42px,42px 42px}
    }
    /* "orbe" de energía flotante, hecho en SVG puro */
    .orb{
      position:fixed;
      z-index:-1;
      pointer-events:none;
      opacity:.55;
      filter:blur(.5px);
    }
    .orb-1{top:-120px;right:-120px;width:340px;height:340px;animation:float1 14s ease-in-out infinite}
    .orb-2{bottom:-140px;left:-100px;width:300px;height:300px;animation:float2 18s ease-in-out infinite}
    @keyframes float1{0%,100%{transform:translate(0,0)}50%{transform:translate(-18px,22px)}}
    @keyframes float2{0%,100%{transform:translate(0,0)}50%{transform:translate(16px,-18px)}}

    a{color:var(--a);text-decoration:none}
    header{padding:14px 20px;border-bottom:1px solid var(--b);background:rgba(22,27,34,.75);backdrop-filter:blur(6px);display:flex;align-items:center;gap:10px;position:relative;z-index:1}
    header h1{font-size:.95rem;font-weight:600}
    .live{font-size:.65rem;padding:2px 7px;border-radius:20px;background:var(--ok);color:#000;font-weight:700;animation:pulse 2s infinite}
    @keyframes pulse{0%,100%{opacity:1}50%{opacity:.6}}
    .wrap{max-width:580px;margin:0 auto;padding:20px 16px;position:relative;z-index:1}
    .login{background:rgba(22,27,34,.85);backdrop-filter:blur(8px);border:1px solid var(--b);border-radius:var(--r);padding:32px 24px;margin-top:60px;box-shadow:0 0 40px rgba(88,166,255,.08)}
    .login h2{font-size:1.05rem;margin-bottom:18px}
    .err{color:var(--err);font-size:.82rem;margin-bottom:10px}
    input{width:100%;padding:10px 12px;background:var(--bg);border:1px solid var(--b);border-radius:var(--r);color:var(--t);font-size:.92rem;margin-bottom:12px;outline:none}
    input:focus{border-color:var(--a)}
    .btn{display:block;width:100%;padding:10px;background:var(--a);color:#000;border:none;border-radius:var(--r);font-weight:600;font-size:.92rem;cursor:pointer;text-align:center;transition:opacity .15s}
    .btn:hover{opacity:.85}
    .btn-o{background:transparent;color:var(--a);border:1px solid var(--a)}
    .card{background:rgba(22,27,34,.85);backdrop-filter:blur(8px);border:1px solid var(--b);border-radius:var(--r);padding:16px;margin-bottom:14px;box-shadow:0 0 30px rgba(88,166,255,.05)}
    .card-title{font-size:.65rem;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--m);margin-bottom:12px}
    .row{display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--b);font-size:.85rem}
    .row:last-child{border-bottom:none}
    .lbl{color:var(--m)}
    .val{font-weight:600;color:var(--a);font-family:monospace;font-size:.82rem}
    .bat-bar{height:5px;background:var(--b);border-radius:3px;overflow:hidden;margin-top:4px;width:100%}
    .bat-fill{height:100%;border-radius:3px}
    .map{height:260px;border-radius:var(--r);overflow:hidden;border:1px solid var(--b);margin-bottom:14px}
    .map iframe{width:100%;height:100%;border:none}
    .actions{display:flex;gap:10px;margin-bottom:16px}
    .actions .btn{flex:1}
    .h-row{font-size:.75rem;color:var(--m);padding:5px 0;border-bottom:1px solid var(--b);font-family:monospace}
    .h-row:last-child{border-bottom:none}
    .empty{text-align:center;padding:50px 0;color:var(--m)}
  </style>
</head>
<body>
<!-- Orbes decorativos SVG (100% originales, sin licencias externas) -->
<svg class="orb orb-1" viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <radialGradient id="g1" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#58a6ff" stop-opacity="0.55"/>
      <stop offset="60%" stop-color="#1f6feb" stop-opacity="0.18"/>
      <stop offset="100%" stop-color="#0d1117" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <circle cx="100" cy="100" r="100" fill="url(#g1)"/>
  <g stroke="#58a6ff" stroke-opacity="0.35" fill="none" stroke-width="0.6">
    <circle cx="100" cy="100" r="70"/>
    <circle cx="100" cy="100" r="50"/>
    <ellipse cx="100" cy="100" rx="90" ry="35" transform="rotate(25 100 100)"/>
    <ellipse cx="100" cy="100" rx="90" ry="35" transform="rotate(-25 100 100)"/>
  </g>
</svg>
<svg class="orb orb-2" viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <radialGradient id="g2" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#3fb950" stop-opacity="0.45"/>
      <stop offset="60%" stop-color="#238636" stop-opacity="0.15"/>
      <stop offset="100%" stop-color="#0d1117" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <circle cx="100" cy="100" r="100" fill="url(#g2)"/>
  <g stroke="#3fb950" stroke-opacity="0.3" fill="none" stroke-width="0.6">
    <circle cx="100" cy="100" r="65"/>
    <circle cx="100" cy="100" r="42"/>
    <ellipse cx="100" cy="100" rx="88" ry="30" transform="rotate(15 100 100)"/>
    <ellipse cx="100" cy="100" rx="88" ry="30" transform="rotate(-40 100 100)"/>
  </g>
</svg>

<header>
  <span>📍</span><h1>Rastreador de Celular</h1>
  {% if view == 'dashboard' and latest %}<span class="live">EN VIVO</span>{% endif %}
</header>
<div class="wrap">
{% if view == 'login' %}
  <div class="login">
    <h2>Acceso seguro</h2>
    {% if error %}<p class="err">Contraseña incorrecta.</p>{% endif %}
    <form method="POST" action="/panel">
      <input type="password" name="password" placeholder="Contraseña" autofocus required>
      <button class="btn" type="submit">Entrar</button>
    </form>
  </div>
{% elif view == 'dashboard' %}
  {% if latest %}
    <div class="card">
      <p class="card-title">Última ubicación</p>
      <div class="row"><span class="lbl">Actualizado</span><span class="val">{{ latest.created_at }}</span></div>
      <div class="row"><span class="lbl">Latitud</span><span class="val">{{ latest.lat }}</span></div>
      <div class="row"><span class="lbl">Longitud</span><span class="val">{{ latest.lon }}</span></div>
      <div class="row"><span class="lbl">Precisión</span><span class="val">{{ latest.precision }}m</span></div>
      <div class="row" style="flex-direction:column;align-items:flex-start;gap:4px">
        <div style="display:flex;justify-content:space-between;width:100%">
          <span class="lbl">Batería</span><span class="val">{{ latest.battery }}%</span>
        </div>
        <div class="bat-bar">
          <div class="bat-fill" style="width:{{ latest.battery }}%;background:{% if latest.battery < 20 %}#f85149{% elif latest.battery < 50 %}#e3b341{% else %}#3fb950{% endif %}"></div>
        </div>
      </div>
    </div>
    <div class="map">
      <iframe src="https://www.openstreetmap.org/export/embed.html?bbox={{ latest.lon - 0.005 }},{{ latest.lat - 0.005 }},{{ latest.lon + 0.005 }},{{ latest.lat + 0.005 }}&layer=mapnik&marker={{ latest.lat }},{{ latest.lon }}" allowfullscreen loading="lazy"></iframe>
    </div>
    <div class="actions">
      <a class="btn" href="https://maps.google.com/?q={{ latest.lat }},{{ latest.lon }}" target="_blank" rel="noopener">Google Maps</a>
      <a class="btn btn-o" href="/panel">↺ Actualizar</a>
    </div>
    {% if history|length > 1 %}
    <div class="card">
      <p class="card-title">Historial ({{ history|length }} registros)</p>
      {% for loc in history %}<div class="h-row">{{ loc.created_at }} · {{ loc.lat }}, {{ loc.lon }} · 🔋{{ loc.battery }}%</div>{% endfor %}
    </div>
    {% endif %}
    <p style="text-align:center;margin-top:10px;font-size:.72rem;color:var(--m)"><a href="/health" target="_blank">Estado del sistema</a></p>
  {% else %}
    <div class="empty"><p>📡</p><p style="margin-top:10px">Sin datos aún.</p></div>
  {% endif %}
  <form method="POST" action="/logout" style="margin-top:12px">
    <button class="btn btn-o" type="submit" style="font-size:.82rem;padding:8px">Cerrar sesión</button>
  </form>
{% endif %}
</div>
</body>
</html>"""


@app.route("/")
def index():
    return app.redirect("/panel")

@app.route("/panel", methods=["GET", "POST"])
def panel():
    if request.method == "POST":
        pwd = request.form.get("password", "").strip()
        if pwd == Config.PANEL_PASSWORD:
            session.clear()
            session["authenticated"] = True
            logger.info("Login exitoso. IP: %s", request.remote_addr)
        else:
            logger.warning("Login fallido. IP: %s", request.remote_addr)
            return render_template_string(HTML, view="login", error=True, latest=None, history=[]), 401
    if not session.get("authenticated"):
        return render_template_string(HTML, view="login", error=False, latest=None, history=[])
    return render_template_string(HTML, view="dashboard", error=False,
                                  latest=db.latest(), history=db.history(10))

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return render_template_string(HTML, view="login", error=False, latest=None, history=[])

@app.route("/ubicacion", methods=["POST"])
@require_api_key
def recibir_ubicacion():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Se esperaba JSON válido."}), 400
    lat, lon = data.get("lat"), data.get("lon")
    if lat is None or lon is None:
        return jsonify({"error": "'lat' y 'lon' requeridos."}), 422
    try:
        lat, lon = round(float(lat), 7), round(float(lon), 7)
    except (ValueError, TypeError):
        return jsonify({"error": "lat/lon deben ser números."}), 422
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return jsonify({"error": "Coordenadas fuera de rango."}), 422
    entry = {
        "lat": lat, "lon": lon,
        "precision": data.get("precision"),
        "battery": data.get("bateria"),
        "ip": request.remote_addr,
        "created_at": now_utc(),
    }
    try:
        db.insert(entry)
    except Exception as e:
        logger.exception("Error en DB: %s", e)
        return jsonify({"error": "Error guardando datos."}), 500
    logger.info("Ubicación guardada: %.6f, %.6f bat=%s%%", lat, lon, entry["battery"])
    return jsonify({"ok": True, "created_at": entry["created_at"]}), 200

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "version": "4.1.0",
        "db_backend": Config.db_backend(),
        "total_registros": db.count(),
        "ultima": (db.latest() or {}).get("created_at"),
    })

@app.errorhandler(401)
def unauthorized(e): return jsonify({"error": "No autorizado."}), 401

@app.errorhandler(404)
def not_found(e): return jsonify({"error": "No encontrado."}), 404

@app.errorhandler(429)
def too_many(e): return jsonify({"error": "Demasiadas solicitudes."}), 429

@app.errorhandler(500)
def server_error(e):
    logger.exception("Error interno.")
    return jsonify({"error": "Error interno."}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

