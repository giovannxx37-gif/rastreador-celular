from flask import Flask, request, jsonify, render_template_string
from datetime import datetime
import os

app = Flask(__name__)

PASSWORD = os.environ.get("PANEL_PASSWORD", "miclave123")
API_KEY = os.environ.get("API_KEY", "miapiclave456")

ultima_ubicacion = {
    "lat": None,
    "lon": None,
    "precision": None,
    "fecha": None,
    "bateria": None
}

HTML_PANEL = """
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Rastreador de Celular</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: Arial, sans-serif; background: #1a1a2e; color: #eee; }
    .header { background: #16213e; padding: 15px 20px; text-align: center; }
    .header h1 { color: #e94560; font-size: 1.4em; }
    .info-box { background: #16213e; margin: 15px; padding: 15px; border-radius: 10px; border-left: 4px solid #e94560; }
    .info-box p { margin: 6px 0; font-size: 0.95em; }
    .info-box span { color: #e94560; font-weight: bold; }
    #map { height: 400px; margin: 0 15px 15px; border-radius: 10px; overflow: hidden; }
    .btn { display: block; margin: 0 15px 15px; padding: 12px; background: #e94560; color: white; border: none; border-radius: 8px; font-size: 1em; cursor: pointer; text-align: center; text-decoration: none; }
    .login-box { max-width: 320px; margin: 80px auto; background: #16213e; padding: 30px; border-radius: 12px; }
    .login-box h2 { text-align: center; margin-bottom: 20px; color: #e94560; }
    .login-box input { width: 100%; padding: 10px; margin: 8px 0; border-radius: 6px; border: 1px solid #333; background: #1a1a2e; color: white; font-size: 1em; }
    .login-box button { width: 100%; padding: 12px; background: #e94560; color: white; border: none; border-radius: 6px; font-size: 1em; cursor: pointer; margin-top: 10px; }
  </style>
</head>
<body>
{% if not autenticado %}
<div class="login-box">
  <h2>🔒 Panel Seguro</h2>
  <form method="POST" action="/panel">
    <input type="password" name="password" placeholder="Contraseña" required>
    <button type="submit">Entrar</button>
  </form>
  {% if error %}<p style="color:red;text-align:center;margin-top:10px;">Contraseña incorrecta</p>{% endif %}
</div>
{% else %}
<div class="header"><h1>📍 Rastreador de Celular</h1></div>
{% if lat %}
<div class="info-box">
  <p>🗓 Última actualización: <span>{{ fecha }}</span></p>
  <p>📍 Latitud: <span>{{ lat }}</span></p>
  <p>📍 Longitud: <span>{{ lon }}</span></p>
  <p>🎯 Precisión: <span>{{ precision }}m</span></p>
  <p>🔋 Batería: <span>{{ bateria }}%</span></p>
</div>
<div id="map">
  <iframe width="100%" height="400" frameborder="0" style="border:0"
    src="https://www.openstreetmap.org/export/embed.html?bbox={{ lon|float - 0.005 }},{{ lat|float - 0.005 }},{{ lon|float + 0.005 }},{{ lat|float + 0.005 }}&layer=mapnik&marker={{ lat }},{{ lon }}"
    allowfullscreen></iframe>
</div>
<a class="btn" href="https://maps.google.com/?q={{ lat }},{{ lon }}" target="_blank">🗺 Abrir en Google Maps</a>
<a class="btn" href="/panel" style="background:#0f3460;">🔄 Actualizar</a>
{% else %}
<div style="text-align:center;padding:40px;color:#888;">
  <p style="font-size:3em;">📡</p>
  <p style="margin-top:15px;">Aún no se recibió ninguna ubicación.</p>
</div>
{% endif %}
{% endif %}
</body>
</html>
"""

@app.route("/panel", methods=["GET", "POST"])
def panel():
    autenticado = False
    error = False
    if request.method == "POST":
        if request.form.get("password") == PASSWORD:
            autenticado = True
        else:
            error = True
    elif request.args.get("pwd") == PASSWORD:
        autenticado = True
    return render_template_string(HTML_PANEL,
        autenticado=autenticado, error=error,
        lat=ultima_ubicacion["lat"], lon=ultima_ubicacion["lon"],
        precision=ultima_ubicacion["precision"],
        fecha=ultima_ubicacion["fecha"], bateria=ultima_ubicacion["bateria"])

@app.route("/ubicacion", methods=["POST"])
def recibir_ubicacion():
    if request.headers.get("X-API-Key") != API_KEY:
        return jsonify({"error": "No autorizado"}), 401
    data = request.json
    if not data:
        return jsonify({"error": "Sin datos"}), 400
    ultima_ubicacion["lat"] = data.get("lat")
    ultima_ubicacion["lon"] = data.get("lon")
    ultima_ubicacion["precision"] = data.get("precision", "?")
    ultima_ubicacion["bateria"] = data.get("bateria", "?")
    ultima_ubicacion["fecha"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    return jsonify({"ok": True, "fecha": ultima_ubicacion["fecha"]})

@app.route("/")
def index():
    return '<meta http-equiv="refresh" content="0;url=/panel">'

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
