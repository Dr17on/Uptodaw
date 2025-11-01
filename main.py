import os
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from bs4 import BeautifulSoup
import logging
import time
import hashlib
import re
from urllib.parse import urljoin, urlparse
from keep_alive import keep_alive

# Iniciar servidor keep-alive
keep_alive()

# Configuración desde variables de entorno
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    raise Exception("No se ha configurado TELEGRAM_BOT_TOKEN en Secrets")

UPTODOWN_URL = "https://www.uptodown.com"
MAX_REQUESTS_PER_MINUTE = 10
MAX_SEARCH_RESULTS = 8
CACHE_DURATION = 300

# Configurar logging para Replit
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Estructuras de datos
user_requests = {}
search_cache = {}
download_cache = {}

class RateLimitExceeded(Exception):
    pass

class SecurityException(Exception):
    pass

class UptodownParser:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        })

    def validate_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            allowed_domains = ['uptodown.com', 'www.uptodown.com']
            return parsed.netloc in allowed_domains and re.match(r'^/[a-zA-Z0-9\-/]+$', parsed.path)
        except Exception:
            return False

    def sanitize_input(self, text: str) -> str:
        if len(text) > 100:
            raise SecurityException("Búsqueda demasiado larga")
        sanitized = re.sub(r'[<>{};]', '', text).strip()
        if not sanitized or len(sanitized) < 2:
            raise SecurityException("Búsqueda inválida")
        return sanitized

    def search_apps(self, query: str):
        try:
            query = self.sanitize_input(query)
            response = self.session.get(
                f"{UPTODOWN_URL}/search", 
                params={"q": query},
                timeout=10
            )
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            results = []
            
            for app in soup.find_all('div', class_='name')[:MAX_SEARCH_RESULTS]:
                link = app.find('a')
                if link and link.get('href'):
                    app_url = urljoin(UPTODOWN_URL, link['href'])
                    if self.validate_url(app_url):
                        results.append({
                            "name": link.text.strip()[:50],
                            "url": app_url
                        })
            return results
            
        except Exception as e:
            logger.error(f"Error en búsqueda: {e}")
            raise Exception("Error en la búsqueda")

    def get_download_url(self, app_url: str):
        try:
            if not self.validate_url(app_url):
                raise SecurityException("URL no válida")
            
            response = self.session.get(app_url + "/download", timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            selectors = ['a.button.download', '.download-link', 'a[href*="/download/"]']
            for selector in selectors:
                download_button = soup.select_one(selector)
                if download_button and download_button.get('href'):
                    download_url = urljoin(UPTODOWN_URL, download_button['href'])
                    if self.validate_url(download_url):
                        return download_url
            return None
            
        except Exception as e:
            logger.error(f"Error en descarga: {e}")
            raise Exception("Error al obtener enlace")

# Instancia global
parser = UptodownParser()

def check_rate_limit(user_id: int) -> bool:
    now = time.time()
    if user_id not in user_requests:
        user_requests[user_id] = []
    
    user_requests[user_id] = [req_time for req_time in user_requests[user_id] if now - req_time < 60]
    
    if len(user_requests[user_id]) >= MAX_REQUESTS_PER_MINUTE:
        return False
    
    user_requests[user_id].append(now)
    return True

def get_cache_key(query: str) -> str:
    return hashlib.md5(query.lower().encode()).hexdigest()

def get_cached_search(query: str):
    cache_key = get_cache_key(query)
    if cache_key in search_cache:
        cached_data = search_cache[cache_key]
        if time.time() - cached_data['timestamp'] < CACHE_DURATION:
            return cached_data['results']
    return None

def set_cached_search(query: str, results):
    cache_key = get_cache_key(query)
    search_cache[cache_key] = {'results': results, 'timestamp': time.time()}

# Handlers de Telegram
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = """
🤖 *Bot de Búsqueda Uptodown*

*Comandos:*
/search <app> - Buscar aplicaciones
/help - Ayuda
/stats - Estadísticas

*Normas:*
- Máximo 10 búsquedas/minuto
- Solo uso personal
- Fines educativos
    """
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def search_app(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not check_rate_limit(user_id):
        await update.message.reply_text("⏰ Límite excedido. Espera 1 minuto.", parse_mode='Markdown')
        return
    
    if not context.args:
        await update.message.reply_text("ℹ️ Uso: `/search nombre_app`", parse_mode='Markdown')
        return

    query = " ".join(context.args)
    
    try:
        cached_results = get_cached_search(query)
        if cached_results:
            results = cached_results
            cache_msg = " (cache)"
        else:
            await update.message.reply_text(f"🔍 Buscando: `{query}`...", parse_mode='Markdown')
            results = parser.search_apps(query)
            set_cached_search(query, results)
            cache_msg = ""
        
        if not results:
            await update.message.reply_text("❌ No se encontraron resultados")
            return

        keyboard = [[InlineKeyboardButton(
            app["name"] if len(app["name"]) <= 30 else app["name"][:27] + "...", 
            callback_data=app["url"]
        )] for app in results]
        
        await update.message.reply_text(
            f"📱 *Resultados para* `{query}`{cache_msg}:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

    except Exception as e:
        await update.message.reply_text(f"😵 Error: {str(e)}")

async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    app_url = query.data
    await query.answer()
    
    try:
        await query.edit_message_text("📥 Obteniendo enlace...")
        download_url = parser.get_download_url(app_url)
        
        if download_url:
            filename = app_url.split('/')[-1] + ".apk"
            await query.message.reply_document(
                document=download_url,
                filename=filename,
                caption="⬇️ Descarga completada"
            )
            await query.edit_message_text("✅ Descarga enviada")
        else:
            await query.edit_message_text("❌ Error en descarga")
            
    except Exception as e:
        await query.edit_message_text(f"😵 Error: {str(e)}")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_users = len(user_requests)
    cached_searches = len(search_cache)
    stats_text = f"""
*📊 Estadísticas*
Usuarios únicos: {total_users}
Búsquedas en cache: {cached_searches}
    """
    await update.message.reply_text(stats_text, parse_mode='Markdown')

def cleanup_old_cache():
    now = time.time()
    global search_cache, download_cache
    
    search_cache = {k: v for k, v in search_cache.items() if now - v['timestamp'] < CACHE_DURATION}
    download_cache = {k: v for k, v in download_cache.items() if now - v['timestamp'] < CACHE_DURATION}

async def periodic_cleanup(context: ContextTypes.DEFAULT_TYPE):
    cleanup_old_cache()

def main():
    try:
        application = Application.builder().token(TOKEN).build()
        
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("search", search_app))
        application.add_handler(CommandHandler("stats", stats_command))
        application.add_handler(CallbackQueryHandler(handle_button))
        
        job_queue = application.job_queue
        job_queue.run_repeating(periodic_cleanup, interval=600, first=10)
        
        logger.info("🤖 Bot iniciado en Replit")
        print("=== Bot Uptodown funcionando ===")
        print("🌐 Keep-alive activo")
        print("📱 Envía /start a tu bot en Telegram")
        
        application.run_polling()
        
    except Exception as e:
        logger.critical(f"Error crítico: {e}")
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()
