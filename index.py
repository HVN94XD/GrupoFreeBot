import io
import os
import re
import sqlite3
import secrets
import string
import threading
import time
from flask import Flask, request
import telebot
from telebot import types

# ================= CONFIGURACIÓN =================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
BOT_USERNAME = os.environ.get("BOT_USERNAME", "GrupoFreeBot")

raw_storage_id = os.environ.get("STORAGE_CHAT_ID", "-1004360797820").strip()
if raw_storage_id.startswith("-") and not raw_storage_id.startswith("-100"):
    raw_storage_id = "-100" + raw_storage_id[1:]
STORAGE_CHAT_ID = int(raw_storage_id)

TIEMPO_AUTO_ELIMINAR = 40
DB_PATH = "/tmp/archivos_bot.db"

WELCOME_IMAGE_URL = "https://6a8d8d79aeeb5e92d6b686c4.imgix.net/sandbox/magnific_quiero-un-fondo-de-1000-x_xSJ0dLcjfW.jpg"

BIOGRAFIA_TEXTO = (
    "👑 PANEL OFICIAL DE CANJES HVN94\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "🔹 Admin: @HVN94\n"
    "🔹 Sistema: Entrega y canje automatizado 1 a 1.\n\n"
    "⚡ Toca una categoría para canjear tu key por privado:"
)

app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

USER_STATE = {}
lock_db = threading.Lock()

# --- BASE DE DATOS ---
def init_db():
    with lock_db:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS authorized_users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                authorized_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                owner_id INTEGER,
                pack_code TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                list_id INTEGER,
                line_number INTEGER,
                line_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                list_id INTEGER,
                line_id INTEGER,
                key_value TEXT UNIQUE,
                claimed INTEGER DEFAULT 0,
                claimed_by INTEGER,
                claimed_at TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                list_id INTEGER,
                key_id INTEGER,
                claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, list_id)
            )
        """)
        conn.commit()
        conn.close()

init_db()

# --- VALIDACIONES Y SEGURIDAD ---
def auto_destruir_mensaje(chat_id, message_ids, delay=40):
    def tarea():
        time.sleep(delay)
        for msg_id in message_ids:
            try:
                bot.delete_message(chat_id, msg_id)
            except Exception:
                pass
    threading.Thread(target=tarea, daemon=True).start()

def enviar_temporal(chat_id, texto, markup=None):
    try:
        kwargs = {}
        if markup:
            kwargs["reply_markup"] = markup
        msg = bot.send_message(chat_id, texto, **kwargs)
        auto_destruir_mensaje(chat_id, [msg.message_id], delay=TIEMPO_AUTO_ELIMINAR)
        return msg
    except Exception:
        return None

def es_admin(user_id):
    return user_id == ADMIN_ID

def es_subadmin(user_id):
    if es_admin(user_id):
        return True
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM authorized_users WHERE telegram_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return bool(res)

def validar_seguridad_chat(chat_id):
    if chat_id > 0 or chat_id == STORAGE_CHAT_ID:
        return True
    
    try:
        admin_member = bot.get_chat_member(chat_id, ADMIN_ID)
        if admin_member.status in ['creator', 'administrator', 'member']:
            return True
    except Exception:
        pass

    try:
        bot.send_message(chat_id, "🐀 rata rata soy creado por @HVN94")
        bot.leave_chat(chat_id)
    except Exception:
        pass
    return False

def generar_llave(prefijo="KEY"):
    chars = string.ascii_uppercase + string.digits
    p1 = ''.join(secrets.choice(chars) for _ in range(4))
    p2 = ''.join(secrets.choice(chars) for _ in range(4))
    p3 = ''.join(secrets.choice(chars) for _ in range(4))
    return f"{prefijo}-{p1}-{p2}-{p3}"

# --- COMANDOS ADMIN ---
@bot.message_handler(commands=['desautorizar'])
def cmd_desautorizar(message):
    if message.chat.id < 0 or not es_admin(message.from_user.id):
        return
    partes = message.text.split()
    if len(partes) < 2 or not partes[1].isdigit():
        bot.send_message(message.chat.id, "⚠️ Usa: `/desautorizar TELEGRAM_ID`", parse_mode="Markdown")
        return

    target_id = int(partes[1])
    with lock_db:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM authorized_users WHERE telegram_id = ?", (target_id,))
        conn.commit()
        conn.close()

    bot.send_message(message.chat.id, f"🚫 Permisos revocados para `{target_id}`.", parse_mode="Markdown")

@bot.message_handler(commands=['reset_claims'])
def cmd_reset_claims(message):
    if not es_admin(message.from_user.id):
        return
    with lock_db:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM claims")
        conn.commit()
        conn.close()
    bot.send_message(message.chat.id, "🔄 Historial de reclamos reseteado con éxito.")

# --- GENERADOR DE BOTONES PÚBLICOS CON RECARGA ---
def obtener_markup_canjes_grupo():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT l.id, l.name, COUNT(k.id) 
        FROM lists l 
        JOIN keys k ON l.id = k.list_id 
        WHERE k.claimed = 0 
        GROUP BY l.id
    """)
    listas_activas = cursor.fetchall()
    conn.close()

    markup = types.InlineKeyboardMarkup(row_width=1)
    if listas_activas:
        for l_id, l_name, stock in listas_activas:
            link_privado = f"https://t.me/{BOT_USERNAME}?start=canjear_{l_id}"
            markup.add(types.InlineKeyboardButton(f"🎁 ┃ {l_name} — (Stock: {stock}) 🔒", url=link_privado))
    else:
        markup.add(types.InlineKeyboardButton("⚠️ No hay stock disponible", callback_data="btn_sin_stock"))
    
    # Botón de Recarga integrado
    markup.add(types.InlineKeyboardButton("🔄 ┃ RECARGAR / ACTUALIZAR LISTAS", callback_data="btn_recargar_grupo"))
    return markup

# --- COMANDOS /free, /panel Y /start ---
@bot.message_handler(commands=['free', 'panel', 'start'])
def cmd_free(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    texto = message.text or ""
    partes = texto.split()

    if not validar_seguridad_chat(chat_id):
        return

    # Redirección directa desde el enlace del grupo
    if chat_id > 0 and len(partes) > 1 and partes[1].startswith("canjear_"):
        l_id = int(partes[1].replace("canjear_", ""))
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM lists WHERE id = ?", (l_id,))
        res = cursor.fetchone()
        conn.close()
        
        cat_nom = res[0] if res else "SELECCIONADA"
        USER_STATE[user_id] = {"paso": "esperando_key", "list_id": l_id, "categoria": cat_nom}
        bot.send_message(chat_id, f"🔐 Pega aquí tu KEY para {cat_nom}:\n(Límite: 1 canje por usuario)")
        return

    # EN GRUPOS
    if chat_id < 0 and chat_id != STORAGE_CHAT_ID:
        markup = obtener_markup_canjes_grupo()
        try:
            bot.send_photo(chat_id, WELCOME_IMAGE_URL, caption=BIOGRAFIA_TEXTO, reply_markup=markup)
        except Exception:
            bot.send_message(chat_id, BIOGRAFIA_TEXTO, reply_markup=markup)
        return

    # EN PRIVADO
    markup = types.InlineKeyboardMarkup(row_width=1)
    if es_admin(user_id):
        markup.add(
            types.InlineKeyboardButton("🎁 ┃ CANJEAR CUENTA FREE", callback_data="btn_cuentas_free"),
            types.InlineKeyboardButton("➕ ┃ CREAR NUEVA LISTA", callback_data="btn_guardar_data"),
            types.InlineKeyboardButton("🔑 ┃ GENERAR KEYS DE LISTA", callback_data="btn_elegir_gen_keys"),
            types.InlineKeyboardButton("📋 ┃ VER / ELIMINAR LISTAS", callback_data="btn_admin_listas"),
            types.InlineKeyboardButton("👤 ┃ AUTORIZAR SUB-ADMIN", callback_data="btn_pedir_auth"),
            types.InlineKeyboardButton("👥 ┃ GESTIONAR SUB-ADMINS", callback_data="btn_gestionar_subadmins"),
            types.InlineKeyboardButton("🔄 ┃ ACTUALIZAR PANEL", callback_data="btn_volver_inicio")
        )
    elif es_subadmin(user_id):
        markup.add(
            types.InlineKeyboardButton("🎁 ┃ CANJEAR CUENTA FREE", callback_data="btn_cuentas_free"),
            types.InlineKeyboardButton("➕ ┃ CREAR NUEVA LISTA", callback_data="btn_guardar_data"),
            types.InlineKeyboardButton("🔑 ┃ GENERAR KEYS DE LISTA", callback_data="btn_elegir_gen_keys"),
            types.InlineKeyboardButton("📦 ┃ MIS LISTAS Y STOCK", callback_data="btn_mis_listas"),
            types.InlineKeyboardButton("🔄 ┃ ACTUALIZAR PANEL", callback_data="btn_volver_inicio")
        )
    else:
        markup.add(types.InlineKeyboardButton("🎁 ┃ VER LISTAS DISPONIBLES", callback_data="btn_cuentas_free"))
        markup.add(types.InlineKeyboardButton("🔄 ┃ ACTUALIZAR PANEL", callback_data="btn_volver_inicio"))

    try:
        bot.send_photo(chat_id, WELCOME_IMAGE_URL, caption=BIOGRAFIA_TEXTO, reply_markup=markup)
    except Exception:
        bot.send_message(chat_id, BIOGRAFIA_TEXTO, reply_markup=markup)
