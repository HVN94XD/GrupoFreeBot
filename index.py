import os
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
STORAGE_CHAT_ID = int(os.environ.get("STORAGE_CHAT_ID", "-1005372728688"))

TIEMPO_AUTO_ELIMINAR = 30
DB_PATH = "/tmp/archivos_bot.db"

app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

USER_STATE = {}
lock_db = threading.Lock()

# --- INICIALIZACIÓN BASE DE DATOS LOCAL (/tmp) ---
def init_db():
    with lock_db:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Usuarios con permiso para cargar listas
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS authorized_users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                authorized_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Listas / Categorías creadas
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                owner_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Líneas de texto asociadas a la lista
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                list_id INTEGER,
                line_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (list_id) REFERENCES lists(id) ON DELETE CASCADE
            )
        """)
        
        # Keys generadas (1 key por cada línea)
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
        
        # Control de reclamos: 1 reclamo por usuario por lista
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

# --- UTILIDADES DE MENSAJES ---
def auto_destruir_mensaje(chat_id, message_ids, delay=30):
    def tarea():
        time.sleep(delay)
        for msg_id in message_ids:
            try:
                bot.delete_message(chat_id, msg_id)
            except Exception:
                pass
    threading.Thread(target=tarea, daemon=True).start()

def borrar_comando(message):
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass

def enviar_temporal(chat_id, texto, markup=None, parse_mode="Markdown"):
    try:
        kwargs = {"parse_mode": parse_mode}
        if markup:
            kwargs["reply_markup"] = markup
        msg = bot.send_message(chat_id, texto, **kwargs)
        auto_destruir_mensaje(chat_id, [msg.message_id], delay=TIEMPO_AUTO_ELIMINAR)
        return msg
    except Exception:
        return None

# --- SEGURIDAD ---
def validar_seguridad_grupo(chat_id):
    if chat_id == STORAGE_CHAT_ID:
        return True
    try:
        admin_member = bot.get_chat_member(chat_id, ADMIN_ID)
        if admin_member.status in ['creator', 'administrator']:
            return True
    except Exception:
        pass

    try:
        alerta = (
            "🚨 **ACCESO NO AUTORIZADO** 🚨\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Este bot es de uso privado. El Administrador principal no está presente.\n"
            "Saliendo del chat..."
        )
        bot.send_message(chat_id, alerta, parse_mode="Markdown")
        bot.leave_chat(chat_id)
    except Exception:
        pass
    return False

def es_admin(user_id):
    return user_id == ADMIN_ID

def usuario_autorizado(user_id):
    if es_admin(user_id):
        return True
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM authorized_users WHERE telegram_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return bool(res)

def generar_llave(prefijo="KEY"):
    chars = string.ascii_uppercase + string.digits
    p1 = ''.join(secrets.choice(chars) for _ in range(4))
    p2 = ''.join(secrets.choice(chars) for _ in range(4))
    p3 = ''.join(secrets.choice(chars) for _ in range(4))
    return f"{prefijo}-{p1}-{p2}-{p3}"

# --- COMANDOS ADMINISTRATIVOS ---
@bot.message_handler(commands=['autorizar'])
def cmd_autorizar(message):
    borrar_comando(message)
    if not es_admin(message.from_user.id):
        return

    partes = message.text.split()
    target_id = None
    target_user = "Desconocido"

    if message.reply_to_message:
        target_id = message.reply_to_message.from_user.id
        target_user = message.reply_to_message.from_user.username or f"ID_{target_id}"
    elif len(partes) >= 2 and partes[1].replace("@", "").isdigit():
        target_id = int(partes[1].replace("@", ""))
        target_user = f"ID_{target_id}"
    elif len(partes) >= 2:
        target_user = partes[1].replace("@", "")
        # Si sólo pasa @username, guardamos un hash temporal si no responde a un mensaje
        target_id = hash(target_user) & 0xFFFFFFFF

    if not target_id:
        enviar_temporal(message.chat.id, "⚠️ Usa: `/autorizar ID_NUMÉRICO` o **responde al mensaje del usuario** con `/autorizar`.")
        return

    with lock_db:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO authorized_users (telegram_id, username, authorized_by) VALUES (?, ?, ?)",
                       (target_id, target_user, message.from_user.id))
        conn.commit()
        conn.close()

    bot.send_message(
        STORAGE_CHAT_ID,
        f"#AUTH_USER\nUSER_ID: {target_id}\nUSER: @{target_user}\nBY: {message.from_user.id}\nFECHA: {time.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    enviar_temporal(message.chat.id, f"✅ Usuario `@{target_user}` (`{target_id}`) autorizado para guardar listas.")

@bot.message_handler(commands=['guardar'])
def cmd_iniciar_guardado(message):
    borrar_comando(message)
    if not usuario_autorizado(message.from_user.id):
        enviar_temporal(message.chat.id, "🚫 No estás autorizado para agregar listas.")
        return
    USER_STATE[message.from_user.id] = {"paso": "esperando_nombre"}
    enviar_temporal(message.chat.id, "📝 **Ingresa el NOMBRE de la lista/botón (ej: HVN o HVN2):**")

# --- FLUJO DE CARGA Y GENERACIÓN AUTOMÁTICA DE KEYS ---
@bot.message_handler(func=lambda m: m.from_user.id in USER_STATE and m.chat.type in ['group', 'supergroup', 'private'])
def flujo_interactivo(message):
    user_id = message.from_user.id
    estado = USER_STATE.get(user_id, {})
    paso = estado.get("paso")

    # 1. Nombre de la Lista
    if paso == "esperando_nombre":
        nombre_cat = message.text.strip().upper().replace(" ", "_")
        borrar_comando(message)

        with lock_db:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT id, owner_id FROM lists WHERE name = ?", (nombre_cat,))
            existente = cursor.fetchone()

            if existente:
                if not es_admin(user_id) and existente[1] != user_id:
                    conn.close()
                    del USER_STATE[user_id]
                    enviar_temporal(message.chat.id, f"⛔ La lista `{nombre_cat}` pertenece a otro usuario.")
                    return
                list_id = existente[0]
            else:
                cursor.execute("INSERT INTO lists (name, owner_id) VALUES (?, ?)", (nombre_cat, user_id))
                list_id = cursor.lastrowid
                conn.commit()
            conn.close()

        USER_STATE[user_id] = {"paso": "esperando_lineas", "list_id": list_id, "nombre": nombre_cat}
        enviar_temporal(message.chat.id, f"✅ Lista: `{nombre_cat}`\n\nEnvía la **lista de líneas de texto** (una por renglón):")
        return

    # 2. Carga de líneas y generación de keys 1x1
    elif paso == "esperando_lineas":
        list_id = estado.get("list_id")
        nombre_cat = estado.get("nombre")
        lineas = [l.strip() for l in message.text.split("\n") if l.strip()]
        borrar_comando(message)
        del USER_STATE[user_id]

        if not lineas:
            enviar_temporal(message.chat.id, "⚠️ No enviaste líneas de texto válidas.")
            return

        with lock_db:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            keys_generadas = []

            for l in lineas:
                cursor.execute("INSERT INTO lines (list_id, line_text) VALUES (?, ?)", (list_id, l))
                line_id = cursor.lastrowid
                key_val = generar_llave(nombre_cat)
                cursor.execute("INSERT INTO keys (list_id, line_id, key_value) VALUES (?, ?, ?)", (list_id, line_id, key_val))
                keys_generadas.append(key_val)

            conn.commit()
            conn.close()

        # Registro de respaldo en el grupo de almacenamiento
        bot.send_message(
            STORAGE_CHAT_ID,
            f"#DATA_PACK\nCAT: {nombre_cat}\nOWNER_ID: {user_id}\nTOTAL: {len(lineas)}\n---\n" + "\n".join(lineas)
        )
        bot.send_message(
            STORAGE_CHAT_ID,
            f"#KEYS_GENERADAS\nCAT: {nombre_cat}\nCANTIDAD: {len(keys_generadas)}\n" + "\n".join(keys_generadas)
        )

        muestra_keys = "\n".join(keys_generadas[:10])
        if len(keys_generadas) > 10:
            muestra_keys += f"\n... y {len(keys_generadas) - 10} keys más en el storage."

        enviar_temporal(
            message.chat.id,
            f"✅ **Guardado con éxito**\n📁 Lista: `{nombre_cat}`\n🔢 Líneas: `{len(lineas)}`\n🔑 Keys generadas 1x1:\n\n`{muestra_keys}`"
        )
        return

    # 3. Canje de Key ingresada por un usuario
    elif paso == "esperando_key":
        list_id = estado.get("list_id")
        categoria = estado.get("categoria")
        key_ingresada = message.text.strip().upper()
        borrar_comando(message)
        del USER_STATE[user_id]

        with lock_db:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()

            # REGLA: 1 key por usuario por lista
            cursor.execute("SELECT id FROM claims WHERE user_id = ? AND list_id = ?", (user_id, list_id))
            if cursor.fetchone():
                conn.close()
                enviar_temporal(message.chat.id, f"❌ **Ya utilizaste una key de la lista `{categoria}`.**\nSolo puedes reclamar 1 vez por categoría.")
                return

            # Validación de la Key
            cursor.execute("""
                SELECT k.id, l.line_text 
                FROM keys k 
                JOIN lines l ON k.line_id = l.id 
                WHERE k.key_value = ? AND k.list_id = ? AND k.claimed = 0
            """, (key_ingresada, list_id))
            key_data = cursor.fetchone()

            if not key_data:
                conn.close()
                enviar_temporal(message.chat.id, f"❌ **Key inválida, ya reclamada o no pertenece a `{categoria}`.**")
                return

            k_id, linea_entregada = key_data
            cursor.execute("UPDATE keys SET claimed = 1, claimed_by = ?, claimed_at = CURRENT_TIMESTAMP WHERE id = ?", (user_id, k_id))
            cursor.execute("INSERT INTO claims (user_id, list_id, key_id) VALUES (?, ?, ?)", (user_id, list_id, k_id))
            conn.commit()
            conn.close()

        # Registro del canje en el Storage
        bot.send_message(
            STORAGE_CHAT_ID,
            f"#CANJE_VALIDADO\nUSER_ID: {user_id}\nUSER: @{message.from_user.username or 'Anon'}\nCAT: {categoria}\nKEY: {key_ingresada}\nFECHA: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

        texto_entrega = (
            f"🎉 **CANJE EXITOSO - LISTA {categoria}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 **Tu dato es:**\n`{linea_entregada}`\n\n"
            f"⏱ _Este mensaje se auto-eliminará en {TIEMPO_AUTO_ELIMINAR}s._"
        )
        enviar_temporal(message.chat.id, texto_entrega)
        return

# --- MENÚ Y NAVEGACIÓN ---
@bot.message_handler(commands=['start', 'menu'])
def cmd_menu(message):
    borrar_comando(message)
    if message.chat.type in ['group', 'supergroup'] and not validar_seguridad_grupo(message.chat.id):
        return

    user_id = message.from_user.id
    markup = types.InlineKeyboardMarkup(row_width=2)

    if es_admin(user_id):
        markup.add(
            types.InlineKeyboardButton("🎁 Cuenta FREE", callback_data="btn_cuentas_free"),
            types.InlineKeyboardButton("📦 Mis Listas", callback_data="mis_listas"),
            types.InlineKeyboardButton("📋 Todas las Listas", callback_data="todas_listas"),
            types.InlineKeyboardButton("➕ Guardar Lista", callback_data="cmd_guardar_btn")
        )
    elif usuario_autorizado(user_id):
        markup.add(
            types.InlineKeyboardButton("🎁 Cuenta FREE", callback_data="btn_cuentas_free"),
            types.InlineKeyboardButton("📦 Mis Listas", callback_data="mis_listas"),
            types.InlineKeyboardButton("➕ Guardar Lista", callback_data="cmd_guardar_btn")
        )
    else:
        markup.add(types.InlineKeyboardButton("🎁 Cuenta FREE / Canjear", callback_data="btn_cuentas_free"))

    enviar_temporal(message.chat.id, "👑 **PANEL DE CONTROL OFICIAL**\nSelecciona una opción:", markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callbacks_panel(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    data = call.data

    if call.message.chat.type in ['group', 'supergroup'] and not validar_seguridad_grupo(chat_id):
        return

    # 1. Menú Cuentas Free (Dinámico por categorías con stock)
    if data == "btn_cuentas_free":
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
            bot.answer_callback_query(call.id, "No hay listas con keys disponibles.", show_alert=True)
            return

        markup = types.InlineKeyboardMarkup(row_width=2)
        for l_id, l_name, stock in listas_activas:
            markup.add(types.InlineKeyboardButton(f"🔹 {l_name} ({stock})", callback_data=f"pedir_key_{l_id}_{l_name}"))

        bot.answer_callback_query(call.id)
        enviar_temporal(chat_id, "📌 **Elige la categoría a canjear:**", markup=markup)

    # 2. Solicitud de Key para la categoría elegida
    elif data.startswith("pedir_key_"):
        _, _, l_id, categoria = data.split("_", 3)
        USER_STATE[user_id] = {"paso": "esperando_key", "list_id": int(l_id), "categoria": categoria}
        bot.answer_callback_query(call.id)
        enviar_temporal(chat_id, f"🔑 **Pega tu KEY para `{categoria}`:**\n*(Límite: 1 key por usuario)*")

    # 3. Ver Mis Listas (Filtrado por Owner ID)
    elif data == "mis_listas":
        if not usuario_autorizado(user_id):
            bot.answer_callback_query(call.id, "No autorizado.", show_alert=True)
            return

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, name FROM lists WHERE owner_id = ?", (user_id,))
        mis_l = cursor.fetchall()
        conn.close()

        if not mis_l:
            bot.answer_callback_query(call.id, "No tienes listas registradas.", show_alert=True)
            return

        txt = "📦 **Tus Listas:**\n"
        for _, name in mis_l:
            txt += f"• `{name}`\n"

        bot.answer_callback_query(call.id)
        enviar_temporal(chat_id, txt)

    # 4. Ver Todas las Listas (Solo Admin)
    elif data == "todas_listas":
        if not es_admin(user_id):
            bot.answer_callback_query(call.id, "Acceso exclusivo para Administrador.", show_alert=True)
            return

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT name, owner_id FROM lists")
        todas = cursor.fetchall()
        conn.close()

        txt = "📋 **Todas las Listas Registradas:**\n"
        for name, owner in todas:
            txt += f"• `{name}` | Creador: `{owner}`\n"

        bot.answer_callback_query(call.id)
        enviar_temporal(chat_id, txt)

    # 5. Botón directo de guardar
    elif data == "cmd_guardar_btn":
        if not usuario_autorizado(user_id):
            bot.answer_callback_query(call.id, "No autorizado.", show_alert=True)
            return
        USER_STATE[user_id] = {"paso": "esperando_nombre"}
        bot.answer_callback_query(call.id)
        enviar_temporal(chat_id, "📝 **Ingresa el NOMBRE de la lista/botón (ej: HVN o HVN2):**")

# --- ENTRADA DEL SERVIDOR VERCEL ---
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
