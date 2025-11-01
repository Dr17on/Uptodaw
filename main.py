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
import random
from keep_alive import keep_alive

# Iniciar servidor keep-alive
keep_alive()

# Configuración
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    raise Exception("❌ No se ha configurado TELEGRAM_BOT_TOKEN en Secrets")

UPTODOWN_URL = "https://www.uptodown.com"
MAX_REQUESTS_PER_MINUTE = 10
MAX_SEARCH_RESULTS = 8
CACHE_DURATION = 300

# Configurar logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Estructuras de datos
user_requests = {}
search_cache = {}
download_cache = {}

class UptodownParser:
    def __init__(self):
        self.session = requests.Session()
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        ]
        self.update_headers()

    def update_headers(self):
        """Actualiza los headers con User-Agent aleatorio"""
        self.session.headers.update({
            'User-Agent': random.choice(self.user_agents),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0',
        })

    def validate_url(self, url: str) -> bool:
        """Valida que la URL sea de Uptodown"""
        try:
            parsed = urlparse(url)
            allowed_domains = ['uptodown.com', 'www.uptodown.com']
            return parsed.netloc in allowed_domains
        except Exception:
            return False

    def search_apps(self, query: str):
        """Busca aplicaciones en Uptodown con múltiples selectors"""
        try:
            logger.info(f"🔍 Buscando: {query}")
            
            # Limpiar query
            query = re.sub(r'[^\w\s-]', '', query).strip()
            if len(query) < 2:
                raise Exception("Búsqueda demasiado corta")

            url = f"{UPTODOWN_URL}/search"
            params = {"q": query}
            
            self.update_headers()  # Cambiar User-Agent
            response = self.session.get(url, params=params, timeout=15)
            response.raise_for_status()
            
            logger.info(f"✅ Página descargada - Status: {response.status_code}")
            
            soup = BeautifulSoup(response.text, 'html.parser')
            results = []
            
            # Múltiples selectors para mayor compatibilidad
            selectors = [
                '.app[data-url]',
                '.app-card',
                '.item[data-url]',
                'div[data-url]',
                '.name a',
                '.app-name a',
                'a[href*="/android/"]',
                '.result-item'
            ]
            
            for selector in selectors:
                elements = soup.select(selector)
                logger.info(f"Selector '{selector}' encontró {len(elements)} elementos")
                
                for element in elements[:MAX_SEARCH_RESULTS]:
                    try:
                        if element.name == 'a':
                            app_url = element.get('href')
                            app_name = element.get_text(strip=True)
                        else:
                            link = element.find('a')
                            if link:
                                app_url = link.get('href')
                                app_name = link.get_text(strip=True)
                            else:
                                app_url = element.get('data-url')
                                app_name = element.get_text(strip=True)
                        
                        if app_url and app_name:
                            # Asegurar URL completa
                            if not app_url.startswith('http'):
                                app_url = urljoin(UPTODOWN_URL, app_url)
                            
                            if self.validate_url(app_url) and len(app_name) > 2:
                                results.append({
                                    "name": app_name[:100],
                                    "url": app_url,
                                    "description": app_name
                                })
                                logger.info(f"✅ App encontrada: {app_name}")
                                
                                if len(results) >= MAX_SEARCH_RESULTS:
                                    break
                    
                    except Exception as e:
                        logger.warning(f"Error procesando elemento: {e}")
                        continue
                
                if results:
                    break
            
            # Si no encontramos con selectors, buscar manualmente en enlaces
            if not results:
                logger.info("Buscando enlaces manualmente...")
                all_links = soup.find_all('a', href=True)
                for link in all_links:
                    href = link['href']
                    text = link.get_text(strip=True)
                    if '/android/' in href and text and len(text) > 2:
                        if not href.startswith('http'):
                            href = urljoin(UPTODOWN_URL, href)
                        
                        if self.validate_url(href):
                            results.append({
                                "name": text[:100],
                                "url": href,
                                "description": text
                            })
                            if len(results) >= MAX_SEARCH_RESULTS:
                                break
            
            logger.info(f"📊 Total de resultados encontrados: {len(results)}")
            return results
            
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Error de red en búsqueda: {e}")
            raise Exception(f"Error de conexión: {str(e)}")
        except Exception as e:
            logger.error(f"❌ Error inesperado en búsqueda: {e}")
            raise Exception(f"Error en la búsqueda: {str(e)}")

    def get_download_url(self, app_url: str):
        """Obtiene URL de descarga con múltiples estrategias"""
        try:
            logger.info(f"📥 Obteniendo descarga para: {app_url}")
            
            if not self.validate_url(app_url):
                raise Exception("URL no válida")

            # Asegurar que tenemos la URL de descarga
            if not app_url.endswith('/download'):
                app_url = app_url.rstrip('/') + '/download'
            
            self.update_headers()
            response = self.session.get(app_url, timeout=20)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Múltiples selectors para el botón de descarga
            download_selectors = [
                'a[data-url*="download"]',
                'a.download[href]',
                'a.button.download',
                '.download-link',
                'a[href*="/download/"]',
                'button[data-url*="download"]',
                '.download-button a',
                'a.dl[href]'
            ]
            
            for selector in download_selectors:
                download_element = soup.select_one(selector)
                if download_element:
                    download_url = download_element.get('data-url') or download_element.get('href')
                    if download_url:
                        if not download_url.startswith('http'):
                            download_url = urljoin(UPTODOWN_URL, download_url)
                        
                        if self.validate_url(download_url):
                            logger.info(f"✅ Enlace de descarga encontrado: {download_url}")
                            return download_url
            
            # Buscar manualmente en enlaces que contengan "download"
            download_links = soup.find_all('a', href=re.compile(r'download', re.I))
            for link in download_links:
                href = link.get('href')
                if href and ('download' in href.lower() or 'apk' in href.lower()):
                    if not href.startswith('http'):
                        href = urljoin(UPTODOWN_URL, href)
                    
                    if self.validate_url(href):
                        logger.info(f"✅ Enlace de descarga manual: {href}")
                        return href
            
            logger.error("❌ No se pudo encontrar enlace de descarga")
            return None
            
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Error de red en descarga: {e}")
            raise Exception(f"Error de conexión en descarga: {str(e)}")
        except Exception as e:
            logger.error(f"❌ Error inesperado en descarga: {e}")
            raise Exception(f"Error al obtener descarga: {str(e)}")

# Instancia global del parser
parser = UptodownParser()

def check_rate_limit(user_id: int) -> bool:
    """Verifica rate limiting por usuario"""
    now = time.time()
    if user_id not in user_requests:
        user_requests[user_id] = []
    
    # Limpiar requests antiguos
    user_requests[user_id] = [req_time for req_time in user_requests[user_id] if now - req_time < 60]
    
    if len(user_requests[user_id]) >= MAX_REQUESTS_PER_MINUTE:
        return False
    
    user_requests[user_id].append(now)
    return True

def get_cache_key(query: str) -> str:
    """Genera clave para cache"""
    return hashlib.md5(query.lower().encode()).hexdigest()

def get_cached_search(query: str):
    """Obtiene resultados del cache"""
    cache_key = get_cache_key(query)
    if cache_key in search_cache:
        cached_data = search_cache[cache_key]
        if time.time() - cached_data['timestamp'] < CACHE_DURATION:
            return cached_data['results']
    return None

def set_cached_search(query: str, results):
    """Guarda resultados en cache"""
    cache_key = get_cache_key(query)
    search_cache[cache_key] = {
        'results': results,
        'timestamp': time.time()
    }

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start"""
    welcome_text = """
🤖 *Bot de Búsqueda Uptodown*

*Comandos disponibles:*
/search <nombre> - Buscar aplicaciones
/help - Mostrar ayuda
/stats - Estadísticas

*Ejemplos:*
`/search whatsapp`
`/search minecraft`
`/search facebook lite`

🌐 *Desarrollado para fines educativos*
    """
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def search_app(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manejador de búsqueda"""
    user_id = update.effective_user.id
    
    # Verificar rate limit
    if not check_rate_limit(user_id):
        await update.message.reply_text(
            "⏰ *Límite de tasa excedido*\nPor favor espera 1 minuto antes de otra búsqueda.",
            parse_mode='Markdown'
        )
        return
    
    if not context.args:
        await update.message.reply_text(
            "ℹ️ *Uso:* `/search <nombre de la aplicación>`\nEjemplo: `/search whatsapp`",
            parse_mode='Markdown'
        )
        return

    query = " ".join(context.args)
    
    try:
        # Verificar cache primero
        cached_results = get_cached_search(query)
        if cached_results:
            results = cached_results
            cache_msg = " (desde cache)"
            logger.info(f"✅ Cache hit para: {query}")
        else:
            await update.message.reply_text(f"🔍 *Buscando:* `{query}`...", parse_mode='Markdown')
            results = parser.search_apps(query)
            set_cached_search(query, results)
            cache_msg = ""
            logger.info(f"✅ Búsqueda completada para: {query}")

        if not results:
            await update.message.reply_text(
                "❌ *No se encontraron resultados*\n\n"
                "💡 *Sugerencias:*\n"
                "• Verifica el nombre de la aplicación\n"
                "• Intenta con términos más específicos\n"
                "• La aplicación podría no estar en Uptodown",
                parse_mode='Markdown'
            )
            return

        # Crear teclado con resultados
        keyboard = []
        for app in results:
            button_text = app["name"]
            if len(button_text) > 30:
                button_text = button_text[:27] + "..."
            keyboard.append([InlineKeyboardButton(button_text, callback_data=app["url"])])

        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"📱 *Resultados para* `{query}`{cache_msg}:\n"
            f"*Encontrados:* {len(results)} aplicaciones\n"
            f"Selecciona una para descargar:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ Error en búsqueda: {error_msg}")
        
        await update.message.reply_text(
            f"😵 *Error en la búsqueda*\n\n"
            f"*Detalles:* {error_msg}\n\n"
            f"💡 *Posibles soluciones:*\n"
            f"• Verifica tu conexión\n"
            f"• Intenta más tarde\n"
            f"• Usa términos de búsqueda diferentes",
            parse_mode='Markdown'
        )

async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manejador de botones de descarga"""
    query = update.callback_query
    app_url = query.data
    user_id = update.effective_user.id
    
    await query.answer()
    
    try:
        logger.info(f"🔄 Iniciando descarga para usuario {user_id}: {app_url}")
        await query.edit_message_text("📥 *Procesando descarga...*", parse_mode='Markdown')
        
        download_url = parser.get_download_url(app_url)
        
        if download_url:
            # Obtener nombre del archivo
            app_name = app_url.split('/')[-1] or "aplicacion"
            filename = f"{app_name}.apk"
            
            logger.info(f"✅ Enviando archivo: {filename}")
            
            # Enviar el documento
            await query.message.reply_document(
                document=download_url,
                filename=filename,
                caption=f"📦 *{app_name}*\n⬇️ Descarga completada desde Uptodown"
            )
            await query.edit_message_text("✅ *Descarga completada y enviada*")
            logger.info(f"✅ Descarga enviada exitosamente para usuario {user_id}")
            
        else:
            await query.edit_message_text(
                "❌ *No se pudo obtener el enlace de descarga*\n\n"
                "💡 *Posibles causas:*\n"
                "• La aplicación no está disponible\n"
                "• Error temporal del servidor\n"
                "• Intenta más tarde",
                parse_mode='Markdown'
            )
            logger.error(f"❌ No se pudo obtener enlace para: {app_url}")

    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ Error en descarga: {error_msg}")
        
        await query.edit_message_text(
            f"😵 *Error en la descarga*\n\n"
            f"*Detalles:* {error_msg}\n\n"
            f"💡 *Intenta de nuevo más tarde*",
            parse_mode='Markdown'
        )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /stats"""
    total_users = len(user_requests)
    cached_searches = len(search_cache)
    
    stats_text = f"""
📊 *Estadísticas del Bot*

👥 *Usuarios únicos:* {total_users}
💾 *Búsquedas en cache:* {cached_searches}
⚡ *Rate Limit:* {MAX_REQUESTS_PER_MINUTE}/minuto

🛠 *Estado:* ✅ Operativo
    """
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /help"""
    help_text = """
🆘 *Ayuda - Bot Uptodown*

🔍 *Cómo buscar:*
`/search nombre_app` - Buscar aplicaciones
Ejemplo: `/search whatsapp`

📥 *Cómo descargar:*
1. Usa `/search` para encontrar apps
2. Haz clic en el botón de la app
3. El bot enviará el archivo APK

⚡ *Limitaciones:*
• Máximo {max_req} búsquedas por minuto
• Solo aplicaciones Android
• Tamaño máximo: 50MB (límite de Telegram)

🛠 *Soporte:*
Si encuentras errores:
• Verifica tu conexión
• Intenta con otra aplicación
• Espera unos minutos
    """.format(max_req=MAX_REQUESTS_PER_MINUTE)
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

def main():
    """Función principal"""
    try:
        # Crear aplicación de Telegram
        application = Application.builder().token(TOKEN).build()
        
        # Añadir handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("search", search_app))
        application.add_handler(CommandHandler("stats", stats_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CallbackQueryHandler(handle_button))
        
        # Iniciar bot
        logger.info("🤖 Iniciando Bot Uptodown en Replit...")
        print("=" * 50)
        print("🚀 BOT UPTODOWN INICIADO")
        print("🌐 Keep-alive activo")
        print("📱 Busca tu bot en Telegram y envía /start")
        print("=" * 50)
        
        application.run_polling()
        
    except Exception as e:
        logger.critical(f"❌ Error crítico al iniciar bot: {e}")
        print(f"❌ ERROR: {e}")

if __name__ == "__main__":
    main()
