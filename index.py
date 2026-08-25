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

raw_storage_id = os.environ.get("STORAGE_CHAT_ID", "-1004360797820").strip()
if raw_storage_id.startswith("-") and not raw_storage_id.startswith("-100"):
    raw_storage_id = "-100" + raw_storage_id[1:]
STORAGE_CHAT_ID = int(raw_storage_id)

TIEMPO_AUTO_ELIMINAR = 40
DB_PATH = "/tmp/archivos_bot.db"

WELCOME_IMAGE_URL = "https://6a8d8d79aeeb5e92d6b686c4.imgix.net/sandbox/magnific_quiero-un-fondo-de-1000-x_xSJ0dLcjfW.jpg"

BIOGRAFIA_TEXTO = (
    "👑 **PANEL DE CANJES OFICIAL HVN94**\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "🔹 **Admin:** @HVN94\n"
    "🔹 **Sistema:** Entrega y canje automatizado 1 a 1.\n\n"
    "⚡ _Selecciona una categoría para canjear tu key:_"
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
                name TEXT UNIQUE,
                owner_id INTEGER,
                pack_code TEXT UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                list_id INTEGER,
                line_number INTEGER,
                line_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (list_id) REFERENCES lists(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                list_id INTEGER,
                line_id INTEGER UNIQUE,
                key_value TEXT UNIQUE,
                claimed INTEGER DEFAULT 0,
                claimed_by INTEGER,
                claimed_at TIMESTAMP,
                FOREIGN KEY (list_id) REFERENCES lists(id) ON DELETE CASCADE,
                FOREIGN KEY (line_id) REFERENCES lines(id) ON DELETE CASCADE
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
    if not es_admin(message.from_user.id):
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

    try:
        bot.send_message(STORAGE_CHAT_ID, f"🚫 #SUBADMIN_REVOCADO\nID: {target_id}\nPor: {message.from_user.id}")
    except Exception:
        pass
    bot.send_message(message.chat.id, f"🚫 Permisos de Sub-Admin revocados para `{target_id}`.", parse_mode="Markdown")

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

# --- COMANDO PRINCIPAL /free y /start ---
@bot.message_handler(commands=['free', 'start'])
def cmd_free(message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not validar_seguridad_chat(chat_id):
        return

    markup = types.InlineKeyboardMarkup(row_width=1)

    if es_admin(user_id):
        markup.add(
            types.InlineKeyboardButton("🎁 ┃ CANJEAR CUENTA FREE", callback_data="btn_cuentas_free"),
            types.InlineKeyboardButton("➕ ┃ CREAR / AGREGAR LÍNEAS", callback_data="btn_guardar_data"),
            types.InlineKeyboardButton("🔑 ┃ GENERAR KEYS DE LISTA", callback_data="btn_elegir_gen_keys"),
            types.InlineKeyboardButton("📋 ┃ VER / ELIMINAR LISTAS", callback_data="btn_admin_listas"),
            types.InlineKeyboardButton("👤 ┃ AUTORIZAR SUB-ADMIN", callback_data="btn_pedir_auth"),
            types.InlineKeyboardButton("👥 ┃ GESTIONAR / QUITAR SUB-ADMIN", callback_data="btn_gestionar_subadmins")
        )
    elif es_subadmin(user_id):
        markup.add(
            types.InlineKeyboardButton("🎁 ┃ CANJEAR CUENTA FREE", callback_data="btn_cuentas_free"),
            types.InlineKeyboardButton("➕ ┃ CREAR / AGREGAR LÍNEAS", callback_data="btn_guardar_data"),
            types.InlineKeyboardButton("🔑 ┃ GENERAR KEYS DE LISTA", callback_data="btn_elegir_gen_keys"),
            types.InlineKeyboardButton("📦 ┃ MIS LISTAS Y STOCK", callback_data="btn_mis_listas")
        )
    else:
        markup.add(types.InlineKeyboardButton("🎁 ┃ VER LISTAS DISPONIBLES", callback_data="btn_cuentas_free"))

    try:
        if WELCOME_IMAGE_URL:
            bot.send_photo(
                chat_id,
                WELCOME_IMAGE_URL,
                caption=BIOGRAFIA_TEXTO,
                reply_markup=markup,
                parse_mode="Markdown"
            )
        else:
            bot.send_message(chat_id, BIOGRAFIA_TEXTO, reply_markup=markup, parse_mode="Markdown")
    except Exception:
        bot.send_message(chat_id, BIOGRAFIA_TEXTO, reply_markup=markup, parse_mode="Markdown")

# --- PROCESAMIENTO DE ARCHIVOS Y RECICLAJE/REENVÍOS ---
@bot.message_handler(content_types=['document'])
def procesar_archivos_y_reenvios(message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not validar_seguridad_chat(chat_id):
        return

    doc_name = message.document.file_name or ""
    caption = message.caption or ""
    texto_total = f"{doc_name} {caption}"

    if "PACK_" in texto_total.upper() or "_PACK_" in doc_name:
        if not es_subadmin(user_id):
            return

        pack_match = re.search(r"PACK_[A-Za-z0-9_]+", texto_total, re.IGNORECASE)
        pack_code = pack_match.group(0).upper() if pack_match else f"PACK_RECICLADO_{int(time.time())}"

        lista_match = re.search(r"Lista:\s*([A-Za-z0-9_]+)", caption, re.IGNORECASE)
        if lista_match:
            nombre_cat = lista_match.group(1).upper()
        elif "_PACK_" in doc_name:
            nombre_cat = doc_name.split("_PACK_")[0].upper()
        else:
            nombre_cat = "LISTA"

        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            contenido = downloaded_file.decode("utf-8", errors="ignore")
            lineas = [l.strip() for l in contenido.splitlines() if l.strip()]
        except Exception:
            bot.send_message(chat_id, "❌ Error al descargar el archivo .txt.")
            return

        if not lineas:
            bot.send_message(chat_id, "⚠️ El archivo reenviado no contiene líneas válidas.")
            return

        with lock_db:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("SELECT id FROM lists WHERE name = ?", (nombre_cat,))
            res_l = cursor.fetchone()
            
            if res_l:
                list_id = res_l[0]
                cursor.execute("UPDATE lists SET pack_code = ? WHERE id = ?", (pack_code, list_id))
            else:
                cursor.execute("INSERT INTO lists (name, owner_id, pack_code) VALUES (?, ?, ?)", (nombre_cat, user_id, pack_code))
                list_id = cursor.lastrowid

            cursor.execute("SELECT COUNT(*) FROM lines WHERE list_id = ?", (list_id,))
            contador_base = cursor.fetchone()[0]

            for idx, l in enumerate(lineas, start=contador_base + 1):
                cursor.execute("INSERT INTO lines (list_id, line_number, line_text) VALUES (?, ?, ?)", (list_id, idx, l))
            
            conn.commit()
            conn.close()

        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(f"🔑 GENERAR KEYS DE {nombre_cat}", callback_data=f"ejecutar_gen_{list_id}"),
            types.InlineKeyboardButton("🔙 VOLVER AL MENÚ", callback_data="btn_volver_inicio")
        )

        bot.send_message(
            chat_id,
            f"♻️ Lote Restaurado con Éxito\n━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷 Pack ID: #{pack_code}\n"
            f"📁 Lista: {nombre_cat}\n"
            f"📄 Líneas detectadas: {len(lineas)}",
            reply_markup=markup
        )
        return

    estado = USER_STATE.get(user_id, {})
    if estado.get("paso") == "esperando_lineas":
        list_id = estado.get("list_id")
        nombre_cat = estado.get("nombre")
        pack_code = estado.get("pack_code")
        del USER_STATE[user_id]

        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            contenido = downloaded_file.decode("utf-8", errors="ignore")
            lineas = [l.strip() for l in contenido.splitlines() if l.strip()]
        except Exception:
            bot.send_message(chat_id, "❌ Error al leer el archivo adjunto.")
            return

        if not lineas:
            bot.send_message(chat_id, "⚠️ El archivo está vacío.")
            return

        with lock_db:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM lines WHERE list_id = ?", (list_id,))
            contador_base = cursor.fetchone()[0]

            for idx, l in enumerate(lineas, start=contador_base + 1):
                cursor.execute("INSERT INTO lines (list_id, line_number, line_text) VALUES (?, ?, ?)", (list_id, idx, l))
            conn.commit()
            conn.close()

        try:
            buffer_txt = io.BytesIO("\n".join(lineas).encode('utf-8'))
            buffer_txt.name = f"{nombre_cat}_{pack_code}.txt"
            bot.send_document(
                STORAGE_CHAT_ID,
                buffer_txt,
                caption=(
                    f"📦 #NUEVO_LOTE_REGISTRADO\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🏷 Registro: #{pack_code}\n"
                    f"📁 Lista: {nombre_cat}\n"
                    f"👤 Owner: {user_id}\n"
                    f"🔢 Líneas: {len(lineas)}\n"
                    f"📅 Fecha: {time.strftime('%Y-%m-%d %H:%M:%S')}"
                )
            )
        except Exception:
            pass

        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(f"🔑 GENERAR KEYS DE {nombre_cat}", callback_data=f"ejecutar_gen_{list_id}"),
            types.InlineKeyboardButton("🔙 VOLVER AL MENÚ", callback_data="btn_volver_inicio")
        )

        bot.send_message(
            chat_id,
            f"✅ Lote Guardado en el Storage\n━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷 Pack ID: #{pack_code}\n"
            f"📁 Lista: {nombre_cat}\n"
            f"📄 Líneas cargadas: {len(lineas)}",
            reply_markup=markup
        )

# --- FLUJOS DE TEXTO ---
@bot.message_handler(content_types=['text'])
def procesar_mensajes_texto(message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not validar_seguridad_chat(chat_id):
        return

    texto_ingresado = message.text.strip().upper()
    
    # Canje directo si pega una key
    if "-" in texto_ingresado and len(texto_ingresado) >= 12 and user_id not in USER_STATE:
        with lock_db:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT k.id, k.list_id, l.name, li.line_number, li.line_text, l.pack_code, k.claimed
                FROM keys k
                JOIN lists l ON k.list_id = l.id
                JOIN lines li ON k.line_id = li.id
                WHERE k.key_value = ?
            """, (texto_ingresado,))
            res_key = cursor.fetchone()

            if res_key:
                k_id, list_id, l_name, line_num, line_txt, p_code, claimed = res_key

                if chat_id < 0:
                    try:
                        bot.delete_message(chat_id, message.message_id)
                    except Exception:
                        pass

                cursor.execute("SELECT id FROM claims WHERE user_id = ? AND list_id = ?", (user_id, list_id))
                if cursor.fetchone():
                    conn.close()
                    enviar_temporal(chat_id, f"❌ @{message.from_user.username or 'Usuario'}, ya reclamaste en la categoría {l_name}.")
                    return

                if claimed == 1:
                    conn.close()
                    enviar_temporal(chat_id, "❌ Esta Key ya fue utilizada.")
                    return

                cursor.execute("UPDATE keys SET claimed = 1, claimed_by = ?, claimed_at = CURRENT_TIMESTAMP WHERE id = ?", (user_id, k_id))
                cursor.execute("INSERT INTO claims (user_id, list_id, key_id) VALUES (?, ?, ?)", (user_id, list_id, k_id))
                conn.commit()
                conn.close()

                try:
                    bot.send_message(
                        STORAGE_CHAT_ID,
                        f"🎟 #CANJE_REGISTRADO\n"
                        f"🏷 Pack: #{p_code}\n"
                        f"🔑 Key: {texto_ingresado}\n"
                        f"📍 Línea: #{line_num}\n"
                        f"👤 Usuario: @{message.from_user.username or 'Anon'} ({user_id})"
                    )
                except Exception:
                    pass

                try:
                    bot.send_message(user_id, f"🎉 CANJE EXITOSO - {l_name}\n━━━━━━━━━━━━━━━━━━━━━━\n📋 Tu dato:\n\n{line_txt}")
                    if chat_id < 0:
                        enviar_temporal(chat_id, f"✅ @{message.from_user.username or 'Usuario'}, te enviamos tu dato por **Mensaje Privado** 📩")
                except Exception:
                    enviar_temporal(chat_id, f"⚠️ Inicia el bot por privado (@GrupoFreeBot) para poder enviarte tu cuenta.")
                return
            conn.close()

    if user_id not in USER_STATE:
        return

    estado = USER_STATE.get(user_id, {})
    paso = estado.get("paso")

    # Autorizar Sub-Admin
    if paso == "esperando_subadmin_id":
        if not es_admin(user_id):
            return
        del USER_STATE[user_id]
        target_str = message.text.strip().replace("@", "")

        if not target_str.isdigit():
            bot.send_message(chat_id, "⚠️ ID Inválido. Usa @userinfobot para ver tu ID numérico.")
            return

        target_id = int(target_str)
        with lock_db:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO authorized_users (telegram_id, username, authorized_by) VALUES (?, ?, ?)",
                           (target_id, f"ID_{target_id}", user_id))
            conn.commit()
            conn.close()

        try:
            bot.send_message(STORAGE_CHAT_ID, f"👤 #SUBADMIN_AUTORIZADO\nID: {target_id}\nPor: {user_id}")
        except Exception:
            pass
        bot.send_message(chat_id, f"✅ Sub-Admin {target_id} autorizado exitosamente.")
        return

    # Nombre de lista nueva
    elif paso == "esperando_nombre":
        if not es_subadmin(user_id):
            return
        nombre_cat = message.text.strip().upper().replace(" ", "_")
        pack_code = f"PACK_{nombre_cat}_{int(time.time())}"

        with lock_db:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT id, owner_id, pack_code FROM lists WHERE name = ?", (nombre_cat,))
            existente = cursor.fetchone()

            if existente:
                if not es_admin(user_id) and existente[1] != user_id:
                    conn.close()
                    del USER_STATE[user_id]
                    bot.send_message(chat_id, f"⛔ La lista {nombre_cat} fue creada por otro usuario.")
                    return
                list_id = existente[0]
                pack_code = existente[2]
            else:
                cursor.execute("INSERT INTO lists (name, owner_id, pack_code) VALUES (?, ?, ?)", (nombre_cat, user_id, pack_code))
                list_id = cursor.lastrowid
                conn.commit()
            conn.close()

        USER_STATE[user_id] = {"paso": "esperando_lineas", "list_id": list_id, "nombre": nombre_cat, "pack_code": pack_code}
        bot.send_message(chat_id, f"✅ Lista: {nombre_cat}\n🏷 ID: #{pack_code}\n\nEnvía tu archivo .txt o pega las líneas:")
        return

    # Guardar líneas por texto
    elif paso == "esperando_lineas":
        list_id = estado.get("list_id")
        nombre_cat = estado.get("nombre")
        pack_code = estado.get("pack_code")
        del USER_STATE[user_id]

        lineas = [l.strip() for l in message.text.splitlines() if l.strip()]
        if not lineas:
            bot.send_message(chat_id, "⚠️ No se detectaron líneas válidas.")
            return

        with lock_db:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM lines WHERE list_id = ?", (list_id,))
            contador_base = cursor.fetchone()[0]

            for idx, l in enumerate(lineas, start=contador_base + 1):
                cursor.execute("INSERT INTO lines (list_id, line_number, line_text) VALUES (?, ?, ?)", (list_id, idx, l))
            conn.commit()
            conn.close()

        try:
            buffer_txt = io.BytesIO("\n".join(lineas).encode('utf-8'))
            buffer_txt.name = f"{nombre_cat}_{pack_code}.txt"
            bot.send_document(
                STORAGE_CHAT_ID,
                buffer_txt,
                caption=(
                    f"📦 #NUEVO_LOTE_REGISTRADO\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🏷 Registro: #{pack_code}\n"
                    f"📁 Lista: {nombre_cat}\n"
                    f"👤 Owner: {user_id}\n"
                    f"🔢 Líneas: {len(lineas)}\n"
                    f"📅 Fecha: {time.strftime('%Y-%m-%d %H:%M:%S')}"
                )
            )
        except Exception:
            pass

        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(f"🔑 GENERAR KEYS DE {nombre_cat}", callback_data=f"ejecutar_gen_{list_id}"),
            types.InlineKeyboardButton("🔙 VOLVER AL MENÚ", callback_data="btn_volver_inicio")
        )

        bot.send_message(
            chat_id,
            f"✅ Lote Guardado en el Storage\n━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷 Pack ID: #{pack_code}\n"
            f"📁 Lista: {nombre_cat}\n"
            f"📄 Líneas cargadas: {len(lineas)}",
            reply_markup=markup
        )
        return

    # Canje por estado
    elif paso == "esperando_key":
        list_id = estado.get("list_id")
        categoria = estado.get("categoria")
        key_ingresada = message.text.strip().upper()
        del USER_STATE[user_id]

        if chat_id < 0:
            try:
                bot.delete_message(chat_id, message.message_id)
            except Exception:
                pass

        with lock_db:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()

            cursor.execute("SELECT id FROM claims WHERE user_id = ? AND list_id = ?", (user_id, list_id))
            if cursor.fetchone():
                conn.close()
                enviar_temporal(chat_id, f"❌ Ya reclamaste una key en la categoría {categoria}.")
                return

            cursor.execute("""
                SELECT k.id, l.line_number, l.line_text, li.pack_code 
                FROM keys k 
                JOIN lines l ON k.line_id = l.id 
                JOIN lists li ON k.list_id = li.id
                WHERE k.key_value = ? AND k.list_id = ? AND k.claimed = 0
            """, (key_ingresada, list_id))
            key_data = cursor.fetchone()

            if not key_data:
                conn.close()
                enviar_temporal(chat_id, f"❌ Key inválida, ya utilizada o no pertenece a {categoria}.")
                return

            k_id, line_num, linea_entregada, pack_code = key_data
            cursor.execute("UPDATE keys SET claimed = 1, claimed_by = ?, claimed_at = CURRENT_TIMESTAMP WHERE id = ?", (user_id, k_id))
            cursor.execute("INSERT INTO claims (user_id, list_id, key_id) VALUES (?, ?, ?)", (user_id, list_id, k_id))
            conn.commit()
            conn.close()

        try:
            bot.send_message(
                STORAGE_CHAT_ID,
                f"🎟 #CANJE_REGISTRADO\n"
                f"🏷 Pack: #{pack_code}\n"
                f"🔑 Key: {key_ingresada}\n"
                f"📍 Línea: #{line_num}\n"
                f"👤 Usuario: @{message.from_user.username or 'Anon'} ({user_id})"
            )
        except Exception:
            pass

        try:
            bot.send_message(user_id, f"🎉 CANJE EXITOSO - {categoria}\n━━━━━━━━━━━━━━━━━━━━━━\n📋 Tu dato:\n\n{linea_entregada}")
            if chat_id < 0:
                enviar_temporal(chat_id, f"✅ @{message.from_user.username or 'Usuario'}, tu cuenta fue entregada por **Privado** 📩")
        except Exception:
            enviar_temporal(chat_id, f"⚠️ Inicia el chat con @GrupoFreeBot por privado para recibir tu entrega.")
        return

# --- CALLBACKS Y GESTIÓN DE ROLES ---
@bot.callback_query_handler(func=lambda call: True)
def router_callbacks(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    data = call.data

    if not validar_seguridad_chat(chat_id):
        bot.answer_callback_query(call.id, "No tienes acceso a este bot.", show_alert=True)
        return

    # 1. Panel para listar y eliminar Sub-Admins
    if data == "btn_gestionar_subadmins":
        if not es_admin(user_id):
            bot.answer_callback_query(call.id, "Solo el Administrador puede gestionar Sub-Admins.", show_alert=True)
            return

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_id, username FROM authorized_users")
        subs = cursor.fetchall()
        conn.close()

        if not subs:
            bot.answer_callback_query(call.id, "No hay Sub-Admins registrados actualmente.", show_alert=True)
            return

        markup = types.InlineKeyboardMarkup(row_width=1)
        for s_id, s_name in subs:
            markup.add(types.InlineKeyboardButton(f"🚫 Quitar: {s_name} ({s_id})", callback_data=f"del_sub_{s_id}"))
        markup.add(types.InlineKeyboardButton("🔙 ┃ Volver al Menú", callback_data="btn_volver_inicio"))

        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "👥 **GESTIÓN DE SUB-ADMINS:**\n_Toca a un usuario para revocarle el acceso de inmediato:_", reply_markup=markup, parse_mode="Markdown")

    # 2. Quitar un Sub-Admin específico
    elif data.startswith("del_sub_"):
        if not es_admin(user_id):
            bot.answer_callback_query(call.id, "No autorizado.", show_alert=True)
            return

        target_id = int(data.replace("del_sub_", ""))
        with lock_db:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM authorized_users WHERE telegram_id = ?", (target_id,))
            conn.commit()
            conn.close()

        try:
            bot.send_message(STORAGE_CHAT_ID, f"🚫 #SUBADMIN_REVOCADO\nID: {target_id}\nPor: {user_id}")
        except Exception:
            pass

        bot.answer_callback_query(call.id, f"Sub-Admin {target_id} eliminado.", show_alert=True)
        bot.send_message(chat_id, f"🚫 **Permisos revocados para el usuario `{target_id}`.**", parse_mode="Markdown")

    elif data == "btn_admin_listas":
        if not es_admin(user_id):
            bot.answer_callback_query(call.id, "Solo el Administrador puede gestionar listas.", show_alert=True)
            return

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, pack_code FROM lists")
        listas = cursor.fetchall()
        conn.close()

        if not listas:
            bot.answer_callback_query(call.id, "No hay listas creadas.", show_alert=True)
            return

        markup = types.InlineKeyboardMarkup(row_width=1)
        for l_id, l_name, p_code in listas:
            markup.add(types.InlineKeyboardButton(f"🗑️ Eliminar: {l_name} ({p_code})", callback_data=f"del_lista_{l_id}"))
        markup.add(types.InlineKeyboardButton("🔙 ┃ Volver al Menú", callback_data="btn_volver_inicio"))

        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "⚙️ PANEL DE ELIMINACIÓN (ADMIN):\nToca una lista para borrarla:", reply_markup=markup)

    elif data.startswith("del_lista_"):
        if not es_admin(user_id):
            bot.answer_callback_query(call.id, "No autorizado.", show_alert=True)
            return

        l_id = int(data.replace("del_lista_", ""))
        with lock_db:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM lists WHERE id = ?", (l_id,))
            res_nom = cursor.fetchone()
            nom = res_nom[0] if res_nom else f"ID_{l_id}"
            
            cursor.execute("DELETE FROM lists WHERE id = ?", (l_id,))
            cursor.execute("DELETE FROM lines WHERE list_id = ?", (l_id,))
            cursor.execute("DELETE FROM keys WHERE list_id = ?", (l_id,))
            cursor.execute("DELETE FROM claims WHERE list_id = ?", (l_id,))
            conn.commit()
            conn.close()

        bot.answer_callback_query(call.id, f"Lista {nom} eliminada.", show_alert=True)
        bot.send_message(chat_id, f"🗑️ Lista {nom} eliminada por el Administrador.")

    elif data == "btn_pedir_auth":
        if not es_admin(user_id):
            bot.answer_callback_query(call.id, "Solo el Administrador puede autorizar.", show_alert=True)
            return
        USER_STATE[user_id] = {"paso": "esperando_subadmin_id"}
        bot.answer_callback_query(call.id)
        txt = (
            "👤 AUTORIZAR NUEVO SUB-ADMIN\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Envía el Telegram ID numérico del usuario.\n\n"
            "💡 Para saber tu ID entra al bot @userinfobot, dale /start y cópialo aquí."
        )
        bot.send_message(chat_id, txt)

    elif data == "btn_guardar_data":
        if not es_subadmin(user_id):
            bot.answer_callback_query(call.id, "No autorizado.", show_alert=True)
            return
        USER_STATE[user_id] = {"paso": "esperando_nombre"}
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "📝 Ingresa el NOMBRE de la lista/categoría (ej: HVN o HVN2):")

    elif data == "btn_elegir_gen_keys":
        if not es_subadmin(user_id):
            bot.answer_callback_query(call.id, "No autorizado.", show_alert=True)
            return

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        if es_admin(user_id):
            cursor.execute("SELECT id, name FROM lists")
        else:
            cursor.execute("SELECT id, name FROM lists WHERE owner_id = ?", (user_id,))
        listas = cursor.fetchall()
        conn.close()

        if not listas:
            bot.answer_callback_query(call.id, "No hay listas creadas.", show_alert=True)
            return

        markup = types.InlineKeyboardMarkup(row_width=1)
        for l_id, l_name in listas:
            markup.add(types.InlineKeyboardButton(f"🔑 ┃ Generar Keys para: {l_name}", callback_data=f"ejecutar_gen_{l_id}"))
        markup.add(types.InlineKeyboardButton("🔙 ┃ Volver al Menú", callback_data="btn_volver_inicio"))

        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "⚙️ Elige la lista para generar keys:", reply_markup=markup)

    elif data.startswith("ejecutar_gen_"):
        l_id = int(data.replace("ejecutar_gen_", ""))
        
        with lock_db:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT name, pack_code FROM lists WHERE id = ?", (l_id,))
            res_n = cursor.fetchone()
            if not res_n:
                conn.close()
                bot.answer_callback_query(call.id, "Lista no encontrada.")
                return
            l_name, pack_code = res_n

            cursor.execute("""
                SELECT l.id FROM lines l 
                LEFT JOIN keys k ON l.id = k.line_id 
                WHERE l.list_id = ? AND k.id IS NULL
            """, (l_id,))
            lineas_sin_key = cursor.fetchall()

            if not lineas_sin_key:
                conn.close()
                bot.answer_callback_query(call.id, f"Todas las líneas de {l_name} ya tienen keys asignadas.", show_alert=True)
                return

            keys_generadas = []
            for (line_id,) in lineas_sin_key:
                k_val = generar_llave(l_name)
                cursor.execute("INSERT INTO keys (list_id, line_id, key_value) VALUES (?, ?, ?)", (l_id, line_id, k_val))
                keys_generadas.append(k_val)

            conn.commit()
            conn.close()

        try:
            buffer_keys = io.BytesIO("\n".join(keys_generadas).encode('utf-8'))
            buffer_keys.name = f"KEYS_{l_name}_{pack_code}.txt"
            bot.send_document(
                STORAGE_CHAT_ID,
                buffer_keys,
                caption=f"🔑 #KEYS_GENERADAS\n🏷 Pack: #{pack_code}\n📁 Lista: {l_name}\n🔢 Total: {len(keys_generadas)}"
            )
        except Exception:
            pass

        bot.answer_callback_query(call.id, "Keys generadas.")

        if len(keys_generadas) > 15:
            buffer_user = io.BytesIO("\n".join(keys_generadas).encode('utf-8'))
            buffer_user.name = f"KEYS_{l_name}_{len(keys_generadas)}.txt"
            bot.send_document(
                chat_id,
                buffer_user,
                caption=f"🔑 {len(keys_generadas)} Keys Generadas para {l_name}"
            )
        else:
            txt_k = "\n".join(keys_generadas)
            bot.send_message(chat_id, f"🔑 {len(keys_generadas)} Keys de {l_name}:\n\n{txt_k}")

    elif data == "btn_cuentas_free":
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

        if not listas_activas:
            bot.answer_callback_query(call.id, "No hay keys disponibles por el momento.", show_alert=True)
            return

        markup = types.InlineKeyboardMarkup(row_width=1)
        for l_id, l_name, stock in listas_activas:
            markup.add(types.InlineKeyboardButton(f"🎁 ┃ {l_name} — (Stock: {stock})", callback_data=f"pedir_key_{l_id}_{l_name}"))
        markup.add(types.InlineKeyboardButton("🔙 ┃ Volver al Menú", callback_data="btn_volver_inicio"))

        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "📌 Elige la categoría a canjear:", reply_markup=markup)

    elif data.startswith("pedir_key_"):
        _, _, l_id, categoria = data.split("_", 3)
        USER_STATE[user_id] = {"paso": "esperando_key", "list_id": int(l_id), "categoria": categoria}
        bot.answer_callback_query(call.id)
        enviar_temporal(chat_id, f"🔐 @{call.from_user.username or 'Usuario'}, pega tu KEY para {categoria} aquí:\n_(Tu mensaje se borrará al enviarlo por seguridad)_")

    elif data == "btn_mis_listas":
        if not es_subadmin(user_id):
            return

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        if es_admin(user_id):
            cursor.execute("""
                SELECT l.name, l.pack_code, COUNT(DISTINCT ln.id), COUNT(DISTINCT k.id) 
                FROM lists l 
                LEFT JOIN lines ln ON l.id = ln.list_id 
                LEFT JOIN keys k ON l.id = k.list_id AND k.claimed = 0
                GROUP BY l.id
            """)
        else:
            cursor.execute("""
                SELECT l.name, l.pack_code, COUNT(DISTINCT ln.id), COUNT(DISTINCT k.id) 
                FROM lists l 
                LEFT JOIN lines ln ON l.id = ln.list_id 
                LEFT JOIN keys k ON l.id = k.list_id AND k.claimed = 0
                WHERE l.owner_id = ?
                GROUP BY l.id
            """, (user_id,))
        listas = cursor.fetchall()
        conn.close()

        if not listas:
            bot.answer_callback_query(call.id, "No tienes listas creadas.", show_alert=True)
            return

        txt = "📦 LISTAS Y REGISTROS EN STORAGE:\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        for name, p_code, total_l, stock_k in listas:
            txt += f"🔹 {name} (#{p_code})\n   ├ 📄 Líneas: {total_l}\n   └ 🔑 Keys libres: {stock_k}\n\n"

        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton("🔙 ┃ Volver al Menú", callback_data="btn_volver_inicio"))

        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, txt, reply_markup=markup)

    elif data == "btn_volver_inicio":
        bot.answer_callback_query(call.id)
        cmd_free(call.message)

# --- ENTRYPOINT FLASK / WEBHOOK ---
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
