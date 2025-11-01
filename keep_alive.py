from flask import Flask
from threading import Thread
import time

app = Flask('')

@app.route('/')
def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>🤖 Bot Uptodown</title>
        <style>
            body { 
                font-family: Arial, sans-serif; 
                text-align: center; 
                padding: 50px; 
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
            }
            .container {
                background: rgba(255,255,255,0.1);
                padding: 30px;
                border-radius: 15px;
                backdrop-filter: blur(10px);
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Bot Uptodown Telegram</h1>
            <p>Estado: <strong>🟢 EN LÍNEA</strong></p>
            <p>🌐 Servidor keep-alive activo</p>
            <p>⏰ Última actualización: {time}</p>
        </div>
    </body>
    </html>
    """.format(time=time.strftime("%Y-%m-%d %H:%M:%S"))

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    server = Thread(target=run)
    server.daemon = True
    server.start()
    print("🟢 Servidor keep-alive iniciado en puerto 8080")
