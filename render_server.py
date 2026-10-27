# language: Python, file: render_server.py, target: Render.com / Cloud
import os
import time
from aiohttp import web
from logger import log

HTML_PAGE = """<!DOCTYPE html>
<html lang="hu">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OrbHunter — Discord Quest Bot</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #0b0e14;
            --card-bg: rgba(22, 27, 34, 0.85);
            --accent: #5865f2;
            --green: #23a55a;
            --text: #f2f3f5;
            --muted: #949ba4;
            --border: rgba(255, 255, 255, 0.08);
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Outfit', sans-serif;
            background: radial-gradient(circle at 50% 0%, #1e1f38 0%, var(--bg) 75%);
            color: var(--text);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .container {
            width: 100%;
            max-width: 580px;
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 20px;
            padding: 36px 32px;
            backdrop-filter: blur(16px);
            box-shadow: 0 20px 50px rgba(0, 0, 0, 0.5);
            text-align: center;
        }
        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: rgba(35, 165, 90, 0.15);
            color: var(--green);
            padding: 6px 14px;
            border-radius: 999px;
            font-size: 0.85rem;
            font-weight: 600;
            border: 1px solid rgba(35, 165, 90, 0.3);
            margin-bottom: 20px;
        }
        .status-dot {
            width: 8px;
            height: 8px;
            background: var(--green);
            border-radius: 50%;
            box-shadow: 0 0 10px var(--green);
        }
        h1 {
            font-size: 1.85rem;
            font-weight: 700;
            margin-bottom: 8px;
            background: linear-gradient(135deg, #ffffff 40%, var(--accent) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        p.subtitle {
            color: var(--muted);
            font-size: 0.95rem;
            margin-bottom: 28px;
        }
        .grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
            margin-bottom: 28px;
            text-align: left;
        }
        .card {
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid var(--border);
            padding: 16px;
            border-radius: 12px;
        }
        .card-label {
            font-size: 0.75rem;
            color: var(--muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 6px;
        }
        .card-value {
            font-size: 1.05rem;
            font-weight: 600;
            color: var(--text);
        }
        .footer {
            font-size: 0.8rem;
            color: var(--muted);
            border-top: 1px solid var(--border);
            padding-top: 18px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="status-badge">
            <span class="status-dot"></span>
            Online & Működik (Render.com)
        </div>
        <h1>OrbHunter Quest Bot</h1>
        <p class="subtitle">Automatikus Discord Küldetésteljesítő Rendszer</p>
        
        <div class="grid">
            <div class="card">
                <div class="card-label">Hitelesítés</div>
                <div class="card-value">📱 Remote Auth v2 (QR)</div>
            </div>
            <div class="card">
                <div class="card-label">Biztonság</div>
                <div class="card-value">🔒 Zéró Token Tárolás</div>
            </div>
            <div class="card">
                <div class="card-label">Platform</div>
                <div class="card-value">☁️ Render Cloud Web</div>
            </div>
            <div class="card">
                <div class="card-label">Health Check</div>
                <div class="card-value">✅ 200 OK (/health)</div>
            </div>
        </div>

        <div class="footer">
            Discord Quest Auto-Completer • Render.com Web Service Ready
        </div>
    </div>
</body>
</html>"""


async def start_render_web_server(bot, start_time: float):
    """Render.com HTTP szerver indítása a port-kötéshez és egészségellenőrzéshez."""
    port_env = os.getenv("PORT")
    port = int(port_env) if port_env and port_env.isdigit() else 10000

    app = web.Application()

    async def index_handler(request):
        return web.Response(text=HTML_PAGE, content_type="text/html", charset="utf-8")

    async def health_handler(request):
        data = {
            "status": "healthy",
            "bot": str(bot.user) if getattr(bot, "user", None) else "Starting",
            "uptime_seconds": int(time.time() - start_time),
            "guilds": len(bot.guilds) if getattr(bot, "guilds", None) else 0,
            "version": "4.0.0"
        }
        return web.json_response(data)

    app.router.add_get("/", index_handler)
    app.router.add_get("/health", health_handler)

    try:
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        log(f"Render.com HTTP szerver elindítva: 0.0.0.0:{port} (/health ellenőrzés kész)", "ok")
    except Exception as e:
        log(f"Nem sikerült elindítani a Render HTTP szervert a(z) {port} porton: {e}", "warn")
