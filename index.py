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
    "🔹 Sistema: Auto-entrega 1-Clic por privado.\n\n"
    "⚡ Selecciona una categoría disponible:"
)

app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

USER_STATE = {}
lock_db = threading.Lock()

# --- BASE DE DATOS ACTUALIZADA ---
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
            CREATE TABLE IF NOT EXISTS user_mappings (
                username TEXT PRIMARY KEY,
                telegram_id INTEGER
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                owner_id INTEGER,
                pack_code TEXT,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                list_id INTEGER,
                line_number INTEGER,
                line_text TEXT,
                claimed INTEGER DEFAULT 0,
                claimed_by INTEGER,
                claimed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                list_id INTEGER,
                line_id INTEGER,
                claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS extra_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                list_id INTEGER,
                allowed_count INTEGER DEFAULT 1,
                used_count INTEGER DEFAULT 0,
                UNIQUE(user_id, list_id)
            )
        """)
        conn.commit()
        conn.close()

init_db()

# --- SEGURIDAD Y HELPERS ---
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

def registrar_usuario(user):
    if not user:
        return
    u_id = user.id
    u_name = user.username
    if u_name:
        with lock_db:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO user_mappings (username, telegram_id) VALUES (?, ?)", (u_name.lower().replace("@", ""), u_id))
            conn.commit()
            conn.close()

# --- GENERADOR DE BOTONES PÚBLICOS CON RECARGA ---
def obtener_markup_canjes_grupo():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT l.id, l.name, COUNT(ln.id) 
        FROM lists l 
        JOIN lines ln ON l.id = ln.list_id 
        WHERE l.status = 'active' AND ln.claimed = 0 
        GROUP BY l.id
    """)
    listas_activas = cursor.fetchall()
    conn.close()

    markup = types.InlineKeyboardMarkup(row_width=1)
    if listas_activas:
        for l_id, l_name, stock in listas_activas:
            link_privado = f"https://t.me/{BOT_USERNAME}?start=reclamar_{l_id}"
            markup.add(types.InlineKeyboardButton(f"🎁 ┃ {l_name} — (Stock: {stock}) 🔒", url=link_privado))
    else:
        markup.add(types.InlineKeyboardButton("⚠️ No hay stock disponible", callback_data="btn_sin_stock"))
    
    markup.add(types.InlineKeyboardButton("🔄 ┃ RECARGAR / ACTUALIZAR LISTAS", callback_data="btn_recargar_grupo"))
    return markup

# --- COMANDOS /free, /panel, /start ---
@bot.message_handler(commands=['free', 'panel', 'start'])
def cmd_free(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    registrar_usuario(message.from_user)

    if not validar_seguridad_chat(chat_id):
        return

    texto = message.text or ""
    partes = texto.split()

    # Usuario entrando por enlace directo desde el grupo
    if chat_id > 0 and len(partes) > 1 and partes[1].startswith("reclamar_"):
        l_id = int(partes[1].replace("reclamar_", ""))
        entregar_linea_directa(chat_id, user_id, l_id)
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
            types.InlineKeyboardButton("🎁 ┃ RECLAMAR CUENTA FREE", callback_data="btn_ver_listas_privado"),
            types.InlineKeyboardButton("➕ ┃ CREAR NUEVA LISTA", callback_data="btn_crear_lista"),
            types.InlineKeyboardButton("📦 ┃ MIS LISTAS Y STOCK", callback_data="btn_mis_listas"),
            types.InlineKeyboardButton("🛑 ┃ FINALIZAR LISTA Y DESCARGAR SOBRANTES", callback_data="btn_finalizar_lista"),
            types.InlineKeyboardButton("⭐ ┃ AUTORIZAR CANJE EXTRA A USUARIO", callback_data="btn_autorizar_extra"),
            types.InlineKeyboardButton("👤 ┃ AUTORIZAR SUB-ADMIN", callback_data="btn_pedir_auth"),
            types.InlineKeyboardButton("👥 ┃ GESTIONAR SUB-ADMINS", callback_data="btn_gestionar_subadmins"),
            types.InlineKeyboardButton("🔄 ┃ ACTUALIZAR PANEL", callback_data="btn_volver_inicio")
        )
    elif es_subadmin(user_id):
        markup.add(
            types.InlineKeyboardButton("🎁 ┃ RECLAMAR CUENTA FREE", callback_data="btn_ver_listas_privado"),
            types.InlineKeyboardButton("➕ ┃ CREAR NUEVA LISTA", callback_data="btn_crear_lista"),
            types.InlineKeyboardButton("📦 ┃ MIS LISTAS Y STOCK", callback_data="btn_mis_listas"),
            types.InlineKeyboardButton("🛑 ┃ FINALIZAR LISTA Y DESCARGAR SOBRANTES", callback_data="btn_finalizar_lista"),
            types.InlineKeyboardButton("🔄 ┃ ACTUALIZAR PANEL", callback_data="btn_volver_inicio")
        )
    else:
        markup.add(types.InlineKeyboardButton("🎁 ┃ VER LISTAS DISPONIBLES", callback_data="btn_ver_listas_privado"))
        markup.add(types.InlineKeyboardButton("🔄 ┃ ACTUALIZAR PANEL", callback_data="btn_volver_inicio"))

    try:
        bot.send_photo(chat_id, WELCOME_IMAGE_URL, caption=BIOGRAFIA_TEXTO, reply_markup=markup)
    except Exception:
        bot.send_message(chat_id, BIOGRAFIA_TEXTO, reply_markup=markup)

# --- FUNCIÓN DE AUTO-ENTREGA 1-CLIC ---
def entregar_linea_directa(chat_id, user_id, list_id):
    with lock_db:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("SELECT name, pack_code, status FROM lists WHERE id = ?", (list_id,))
        res_list = cursor.fetchone()
        if not res_list or res_list[2] != 'active':
            conn.close()
            bot.send_message(chat_id, "❌ Esta lista ya no está activa o fue finalizada.")
            return

        l_name, p_code, _ = res_list

        # Validar si ya reclamó y si tiene reclamo extra autorizado
        cursor.execute("SELECT COUNT(*) FROM claims WHERE user_id = ? AND list_id = ?", (user_id, list_id))
        veces_reclamadas = cursor.fetchone()[0]

        cursor.execute("SELECT allowed_count, used_count FROM extra_claims WHERE user_id = ? AND list_id = ?", (user_id, list_id))
        res_extra = cursor.fetchone()

        max_permitidos = 1
        if res_extra:
            max_permitidos += res_extra[0]

        if veces_reclamadas >= max_permitidos:
            conn.close()
            bot.send_message(chat_id, f"❌ Ya has reclamado tu cuenta disponible en la categoría {l_name}.")
            return

        # Buscar línea libre
        cursor.execute("SELECT id, line_number, line_text FROM lines WHERE list_id = ? AND claimed = 0 ORDER BY id ASC LIMIT 1", (list_id,))
        res_line = cursor.fetchone()

        if not res_line:
            conn.close()
            bot.send_message(chat_id, f"⚠️ Lo sentimos, ya no queda stock disponible en {l_name}.")
            return

        line_id, line_num, line_txt = res_line

        # Marcar línea como reclamada
        cursor.execute("UPDATE lines SET claimed = 1, claimed_by = ?, claimed_at = CURRENT_TIMESTAMP WHERE id = ?", (user_id, line_id))
        cursor.execute("INSERT INTO claims (user_id, list_id, line_id) VALUES (?, ?, ?)", (user_id, list_id, line_id))

        if res_extra and veces_reclamadas >= 1:
            cursor.execute("UPDATE extra_claims SET used_count = used_count + 1 WHERE user_id = ? AND list_id = ?", (user_id, list_id))

        conn.commit()
        conn.close()

    # Notificar al Storage
    try:
        bot.send_message(
            STORAGE_CHAT_ID,
            f"🎟 #ENTREGA_1CLIC_REGISTRADA\n"
            f"🏷 Pack: #{p_code}\n"
            f"📁 Lista: {l_name}\n"
            f"📍 Línea: #{line_num}\n"
            f"👤 Usuario: {user_id}"
        )
    except Exception:
        pass

    texto_entrega = (
        f"🎉 CANJE EXITOSO - {l_name}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Tu dato entregado (Línea #{line_num}):\n\n"
        f"{line_txt}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💬 ¡Muchas gracias por usar nuestro servicio!\n"
        f"Puedes agradecer o dejar tus capturas en el grupo oficial para seguir subiendo más cuentas y listas. 🔥"
    )
    bot.send_message(chat_id, texto_entrega)
# --- PROCESAMIENTO DE ARCHIVOS (SOLO PRIVADO) ---
@bot.message_handler(content_types=['document'])
def procesar_archivos(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    registrar_usuario(message.from_user)

    if not validar_seguridad_chat(chat_id) or chat_id < 0:
        return

    estado = USER_STATE.get(user_id, {})
    if estado.get("paso") == "esperando_lineas":
        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            contenido = downloaded_file.decode("utf-8", errors="ignore")
            nuevas = [l.strip() for l in contenido.splitlines() if l.strip()]
        except Exception:
            bot.send_message(chat_id, "❌ Error al leer el archivo adjunto.")
            return

        if not nuevas:
            bot.send_message(chat_id, "⚠️ El archivo no contiene líneas válidas.")
            return

        estado.setdefault("lineas_acumuladas", []).extend(nuevas)
        total = len(estado["lineas_acumuladas"])
        bot.send_message(
            chat_id,
            f"📥 Se agregaron {len(nuevas)} líneas desde el archivo.\n"
            f"📊 Total acumulado: {total} líneas.\n\n"
            f"• Envía más líneas o archivos si deseas.\n"
            f"• O escribe /okey para finalizar y guardar la lista."
        )

# --- FLUJOS DE TEXTO (ACUMULADOR /okey, ADMIN, EXTRAS) ---
@bot.message_handler(content_types=['text'])
def procesar_mensajes_texto(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    registrar_usuario(message.from_user)

    if not validar_seguridad_chat(chat_id) or chat_id < 0:
        return

    texto_ingresado = message.text.strip()
    estado = USER_STATE.get(user_id, {})
    paso = estado.get("paso")

    # 1. ACUMULADOR DE LÍNEAS CON /okey
    if paso == "esperando_lineas":
        if texto_ingresado.lower() == "/okey":
            lineas = estado.get("lineas_acumuladas", [])
            if not lineas:
                bot.send_message(chat_id, "⚠️ No has agregado ninguna línea todavía. Envía líneas primero o escribe /cancelar.")
                return

            estado["paso"] = "confirmar_okey"
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("✅ SÍ, CREAR LISTA", callback_data="btn_confirmar_creacion"),
                types.InlineKeyboardButton("❌ CANCELAR", callback_data="btn_cancelar_creacion")
            )
            bot.send_message(
                chat_id,
                f"📋 Has agregado {len(lineas)} líneas.\n\n"
                f"¿Quieres confirmar la creación de la lista **{estado.get('nombre')}** con estas {len(lineas)} líneas?",
                reply_markup=markup,
                parse_mode="Markdown"
            )
            return
        elif texto_ingresado.lower() == "/cancelar":
            del USER_STATE[user_id]
            bot.send_message(chat_id, "❌ Creación de lista cancelada.")
            return
        else:
            # Acumular líneas enviadas como texto (una a una o en bloque)
            nuevas = [l.strip() for l in texto_ingresado.splitlines() if l.strip()]
            if nuevas:
                estado.setdefault("lineas_acumuladas", []).extend(nuevas)
                total = len(estado["lineas_acumuladas"])
                bot.send_message(
                    chat_id,
                    f"➕ {len(nuevas)} línea(s) agregada(s). (Total acumulado: {total})\n"
                    f"Sigue enviando líneas o escribe /okey cuando termines."
                )
            return

    # 2. Nombre de la lista
    if paso == "esperando_nombre_lista":
        if not es_subadmin(user_id):
            return
        nombre_cat = texto_ingresado.upper()
        pack_code = f"PACK_{nombre_cat.replace(' ', '_')}_{int(time.time())}"

        USER_STATE[user_id] = {
            "paso": "esperando_lineas",
            "nombre": nombre_cat,
            "pack_code": pack_code,
            "lineas_acumuladas": []
        }
        bot.send_message(
            chat_id,
            f"📝 Creando lista: **{nombre_cat}**\n━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Envía las líneas (una por una, en bloque o en archivo .txt).\n\n"
            f"⚡ Cuando termines de enviar todas las líneas, escribe: **/okey**",
            parse_mode="Markdown"
        )
        return

    # 3. Autorizar Sub-Admin
    if paso == "esperando_subadmin_id":
        if not es_admin(user_id):
            return
        del USER_STATE[user_id]
        target_str = texto_ingresado.replace("@", "")

        target_id = None
        if target_str.isdigit():
            target_id = int(target_str)
        else:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT telegram_id FROM user_mappings WHERE username = ?", (target_str.lower(),))
            res = cursor.fetchone()
            conn.close()
            if res:
                target_id = res[0]

        if not target_id:
            bot.send_message(chat_id, "⚠️ No se encontró el usuario. Pídele su ID numérico mediante @userinfobot.")
            return

        with lock_db:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO authorized_users (telegram_id, username, authorized_by) VALUES (?, ?, ?)",
                           (target_id, f"ID_{target_id}", user_id))
            conn.commit()
            conn.close()

        bot.send_message(chat_id, f"✅ Sub-Admin `{target_id}` autorizado exitosamente.")
        return

    # 4. Autorizar Canje Extra a Usuario
    if paso == "esperando_usuario_extra":
        if not es_admin(user_id):
            return
        target_str = texto_ingresado.replace("@", "")
        target_id = None

        if target_str.isdigit():
            target_id = int(target_str)
        else:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT telegram_id FROM user_mappings WHERE username = ?", (target_str.lower(),))
            res = cursor.fetchone()
            conn.close()
            if res:
                target_id = res[0]

        if not target_id:
            bot.send_message(chat_id, "⚠️ Usuario no identificado. Pídele que envíe `/start` al bot o envía su ID numérico.")
            return

        USER_STATE[user_id] = {"paso": "eligiendo_lista_extra", "target_id": target_id}

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, name FROM lists WHERE status = 'active'")
        listas = cursor.fetchall()
        conn.close()

        if not listas:
            del USER_STATE[user_id]
            bot.send_message(chat_id, "❌ No hay listas activas.")
            return

        markup = types.InlineKeyboardMarkup(row_width=1)
        for l_id, l_name in listas:
            markup.add(types.InlineKeyboardButton(f"⭐ Autorizar en: {l_name}", callback_data=f"dar_extra_{l_id}_{target_id}"))
        markup.add(types.InlineKeyboardButton("🔙 Cancelar", callback_data="btn_volver_inicio"))

        bot.send_message(chat_id, f"👤 Usuario seleccionado: `{target_id}`\nElige la lista donde tendrá canje extra:", reply_markup=markup, parse_mode="Markdown")
        return

# --- CALLBACKS Y GESTIÓN GENERAL ---
@bot.callback_query_handler(func=lambda call: True)
def router_callbacks(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    data = call.data
    registrar_usuario(call.from_user)

    if not validar_seguridad_chat(chat_id):
        bot.answer_callback_query(call.id, "No tienes acceso.", show_alert=True)
        return

    if data == "btn_recargar_grupo":
        nuevo_markup = obtener_markup_canjes_grupo()
        try:
            bot.edit_message_reply_markup(chat_id=chat_id, message_id=call.message.message_id, reply_markup=nuevo_markup)
            bot.answer_callback_query(call.id, "✅ Listas y Stock actualizados.")
        except Exception:
            bot.answer_callback_query(call.id, "Las listas ya están actualizadas.")
        return

    if data == "btn_sin_stock":
        bot.answer_callback_query(call.id, "No hay stock disponible en este momento.", show_alert=True)
        return

    # Confirmar creación de lista tras /okey
    if data == "btn_confirmar_creacion":
        estado = USER_STATE.get(user_id, {})
        lineas = estado.get("lineas_acumuladas", [])
        nombre_cat = estado.get("nombre", "LISTA")
        pack_code = estado.get("pack_code", f"PACK_{int(time.time())}")
        del USER_STATE[user_id]

        with lock_db:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("INSERT INTO lists (name, owner_id, pack_code, status) VALUES (?, ?, ?, 'active')", (nombre_cat, user_id, pack_code))
            list_id = cursor.lastrowid

            for idx, l in enumerate(lineas, start=1):
                cursor.execute("INSERT INTO lines (list_id, line_number, line_text) VALUES (?, ?, ?)", (list_id, idx, l))
            conn.commit()
            conn.close()

        try:
            buffer_txt = io.BytesIO("\n".join(lineas).encode('utf-8'))
            buffer_txt.name = f"{nombre_cat.replace(' ', '_')}_{pack_code}.txt"
            bot.send_document(
                STORAGE_CHAT_ID,
                buffer_txt,
                caption=f"📦 #NUEVA_LISTA_CREADA\n🏷 Pack: #{pack_code}\n📁 Lista: {nombre_cat}\n🔢 Total Líneas: {len(lineas)}"
            )
        except Exception:
            pass

        bot.answer_callback_query(call.id, "✅ Lista guardada.")
        bot.send_message(chat_id, f"✅ **Lista `{nombre_cat}` creada con éxito.**\n📄 **Total Líneas:** `{len(lineas)}`\n🏷 **Pack ID:** `#{pack_code}`", parse_mode="Markdown")
        return

    if data == "btn_cancelar_creacion":
        if user_id in USER_STATE:
            del USER_STATE[user_id]
        bot.answer_callback_query(call.id, "Cancelado.")
        bot.send_message(chat_id, "❌ Creación de lista cancelada.")
        return

    if data == "btn_crear_lista":
        if not es_subadmin(user_id):
            return
        USER_STATE[user_id] = {"paso": "esperando_nombre_lista"}
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "📝 Ingresa el NOMBRE de la nueva lista (ej: IZZI GO o FORMULA 1 TV):")
        return

    # Ver listas desde Privado para reclamar
    if data == "btn_ver_listas_privado":
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT l.id, l.name, COUNT(ln.id) 
            FROM lists l 
            JOIN lines ln ON l.id = ln.list_id 
            WHERE l.status = 'active' AND ln.claimed = 0 
            GROUP BY l.id
        """)
        listas_activas = cursor.fetchall()
        conn.close()

        if not listas_activas:
            bot.answer_callback_query(call.id, "No hay listas con stock.", show_alert=True)
            return

        markup = types.InlineKeyboardMarkup(row_width=1)
        for l_id, l_name, stock in listas_activas:
            markup.add(types.InlineKeyboardButton(f"🎁 ┃ {l_name} — (Stock: {stock})", callback_data=f"auto_reclamar_{l_id}"))
        markup.add(types.InlineKeyboardButton("🔙 ┃ Volver al Menú", callback_data="btn_volver_inicio"))

        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "📌 Toca la categoría para reclamar tu cuenta directamente:", reply_markup=markup)
        return

    if data.startswith("auto_reclamar_"):
        l_id = int(data.replace("auto_reclamar_", ""))
        bot.answer_callback_query(call.id)
        entregar_linea_directa(chat_id, user_id, l_id)
        return

    # Finalizar lista y descargar sobrantes
    if data == "btn_finalizar_lista":
        if not es_subadmin(user_id):
            return
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, name FROM lists WHERE status = 'active'")
        listas = cursor.fetchall()
        conn.close()

        if not listas:
            bot.answer_callback_query(call.id, "No hay listas activas.", show_alert=True)
            return

        markup = types.InlineKeyboardMarkup(row_width=1)
        for l_id, l_name in listas:
            markup.add(types.InlineKeyboardButton(f"🛑 Finalizar: {l_name}", callback_data=f"ejecutar_finalizar_{l_id}"))
        markup.add(types.InlineKeyboardButton("🔙 ┃ Volver al Menú", callback_data="btn_volver_inicio"))

        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "🛑 Selecciona la lista que deseas finalizar y descargar sus sobrantes:", reply_markup=markup)
        return

    if data.startswith("ejecutar_finalizar_"):
        l_id = int(data.replace("ejecutar_finalizar_", ""))
        with lock_db:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT name, pack_code FROM lists WHERE id = ?", (l_id,))
            res_l = cursor.fetchone()
            if not res_l:
                conn.close()
                bot.answer_callback_query(call.id, "Lista no encontrada.")
                return
            l_name, p_code = res_l

            cursor.execute("SELECT line_text FROM lines WHERE list_id = ? AND claimed = 0 ORDER BY id ASC", (l_id,))
            sobrantes = [row[0] for row in cursor.fetchall()]

            cursor.execute("UPDATE lists SET status = 'finished' WHERE id = ?", (l_id,))
            conn.commit()
            conn.close()

        bot.answer_callback_query(call.id, "Lista finalizada.")

        if sobrantes:
            buffer_sobrantes = io.BytesIO("\n".join(sobrantes).encode('utf-8'))
            buffer_sobrantes.name = f"SOBRANTES_{l_name.replace(' ', '_')}_{len(sobrantes)}.txt"
            bot.send_document(
                chat_id,
                buffer_sobrantes,
                caption=f"📁 **Líneas Sobrantes de `{l_name}`**\n🔢 **Total no reclamadas:** `{len(sobrantes)}`",
                parse_mode="Markdown"
            )
        else:
            bot.send_message(chat_id, f"🛑 La lista **{l_name}** fue finalizada (no quedaron líneas sobrantes).")
        return

    # Autorizar canje extra a usuario
    if data == "btn_autorizar_extra":
        if not es_admin(user_id):
            return
        USER_STATE[user_id] = {"paso": "esperando_usuario_extra"}
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "👤 Ingresa el @usuario o Telegram ID al que deseas autorizar un canje extra:")
        return

    if data.startswith("dar_extra_"):
        _, _, l_id, target_id = data.split("_")
        l_id, target_id = int(l_id), int(target_id)
        if user_id in USER_STATE:
            del USER_STATE[user_id]

        with lock_db:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO extra_claims (user_id, list_id, allowed_count, used_count)
                VALUES (?, ?, 1, 0)
                ON CONFLICT(user_id, list_id) DO UPDATE SET allowed_count = allowed_count + 1
            """, (target_id, l_id))
            cursor.execute("SELECT name FROM lists WHERE id = ?", (l_id,))
            res = cursor.fetchone()
            l_name = res[0] if res else "Lista"
            conn.commit()
            conn.close()

        bot.answer_callback_query(call.id, "Autorizado.")
        bot.send_message(chat_id, f"⭐ Usuario `{target_id}` autorizado con éxito para reclamar 1 cuenta adicional en **{l_name}**.", parse_mode="Markdown")
        return

    # Gestión de Sub-Admins y Listas
    if data == "btn_gestionar_subadmins":
        if not es_admin(user_id):
            return
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_id, username FROM authorized_users")
        subs = cursor.fetchall()
        conn.close()

        if not subs:
            bot.answer_callback_query(call.id, "No hay Sub-Admins registrados.", show_alert=True)
            return

        markup = types.InlineKeyboardMarkup(row_width=1)
        for s_id, s_name in subs:
            markup.add(types.InlineKeyboardButton(f"🚫 Quitar: {s_name} ({s_id})", callback_data=f"del_sub_{s_id}"))
        markup.add(types.InlineKeyboardButton("🔙 ┃ Volver al Menú", callback_data="btn_volver_inicio"))

        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "👥 GESTIÓN DE SUB-ADMINS:", reply_markup=markup)

    elif data.startswith("del_sub_"):
        if not es_admin(user_id):
            return
        target_id = int(data.replace("del_sub_", ""))
        with lock_db:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM authorized_users WHERE telegram_id = ?", (target_id,))
            conn.commit()
            conn.close()
        bot.answer_callback_query(call.id, "Sub-Admin eliminado.")
        bot.send_message(chat_id, f"🚫 Permisos revocados para {target_id}.")

    elif data == "btn_pedir_auth":
        if not es_admin(user_id):
            return
        USER_STATE[user_id] = {"paso": "esperando_subadmin_id"}
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "👤 Envía el Telegram ID o @usuario a autorizar como Sub-Admin:")

    elif data == "btn_mis_listas":
        if not es_subadmin(user_id):
            return
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT l.name, l.pack_code, COUNT(ln.id), SUM(CASE WHEN ln.claimed = 0 THEN 1 ELSE 0 END)
            FROM lists l 
            LEFT JOIN lines ln ON l.id = ln.list_id 
            WHERE l.status = 'active'
            GROUP BY l.id
        """)
        listas = cursor.fetchall()
        conn.close()

        if not listas:
            bot.answer_callback_query(call.id, "No hay listas activas.", show_alert=True)
            return

        txt = "📦 LISTAS ACTIVAS Y STOCK:\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        for name, p_code, total_l, stock_l in listas:
            stock_val = stock_l if stock_l is not None else 0
            txt += f"🔹 {name} (#{p_code})\n   ├ 📄 Líneas totales: {total_l}\n   └ 🎁 Libres para canjear: {stock_val}\n\n"

        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton("🔙 ┃ Volver al Menú", callback_data="btn_volver_inicio"))

        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, txt, reply_markup=markup)

    elif data == "btn_volver_inicio":
        bot.answer_callback_query(call.id)
        cmd_free(call.message)

# --- WEBHOOK ENTRYPOINT ---
@app.route("/", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        return "Bot activo en Vercel", 200

    try:
        json_data = request.get_json(silent=True)
        if json_data:
            update = telebot.types.Update.de_json(json_data)
            bot.process_new_updates([update])
        return "OK", 200
    except Exception:
        return "OK", 200
