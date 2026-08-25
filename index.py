import io
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

TIEMPO_AUTO_ELIMINAR = 60
DB_PATH = "/tmp/archivos_bot.db"

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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                list_id INTEGER,
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

# --- UTILIDADES ---
def auto_destruir_mensaje(chat_id, message_ids, delay=60):
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

# --- MENÚ PRINCIPAL CON BOTONES AMPLIOS ---
@bot.message_handler(commands=['start', 'menu'])
def cmd_menu(message):
    borrar_comando(message)
    user_id = message.from_user.id
    markup = types.InlineKeyboardMarkup(row_width=1)

    if usuario_autorizado(user_id):
        markup.add(
            types.InlineKeyboardButton("🎁 ┃ CANJEAR CUENTA FREE", callback_data="btn_cuentas_free"),
            types.InlineKeyboardButton("➕ ┃ CREAR / AGREGAR LÍNEAS", callback_data="btn_guardar_data"),
            types.InlineKeyboardButton("🔑 ┃ GENERAR KEYS DE LISTA", callback_data="btn_elegir_gen_keys"),
            types.InlineKeyboardButton("📦 ┃ MIS LISTAS Y STOCK", callback_data="btn_mis_listas")
        )
        txt = (
            "👑 **PANEL DE ADMINISTRACIÓN Y CONTROL**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔹 **Estado:** Autorizado / Administrador\n"
            "🔹 **Soporte masivo:** Texto directo o archivos `.txt`\n"
            "🔹 **Regla:** 1 Key por usuario por categoría\n\n"
            "👇 _Selecciona una opción del menú:_"
        )
    else:
        markup.add(types.InlineKeyboardButton("🎁 ┃ RECLAMAR CUENTA FREE", callback_data="btn_cuentas_free"))
        txt = (
            "💎 **PANEL DE RECLAMOS OFICIAL**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Reclama tus cuentas pegando tu KEY única de acceso.\n\n"
            "👇 _Toca el botón inferior para comenzar:_"
        )

    enviar_temporal(message.chat.id, txt, markup=markup)

# --- FLUJO DE GUARDADO (TEXTO Y DOCUMENTOS .TXT) ---
@bot.message_handler(content_types=['text', 'document'])
def procesar_mensajes_y_archivos(message):
    user_id = message.from_user.id
    if user_id not in USER_STATE:
        return

    estado = USER_STATE.get(user_id, {})
    paso = estado.get("paso")

    # Paso 1: Nombre de la lista
    if paso == "esperando_nombre":
        if not message.text:
            return
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
                    enviar_temporal(message.chat.id, f"⛔ La lista `{nombre_cat}` fue creada por otro usuario.")
                    return
                list_id = existente[0]
            else:
                cursor.execute("INSERT INTO lists (name, owner_id) VALUES (?, ?)", (nombre_cat, user_id))
                list_id = cursor.lastrowid
                conn.commit()
            conn.close()

        USER_STATE[user_id] = {"paso": "esperando_lineas", "list_id": list_id, "nombre": nombre_cat}
        txt = (
            f"✅ **Categoría seleccionada:** `{nombre_cat}`\n\n"
            "📥 **¿Cómo cargar tus datos masivos?**\n"
            "1. **Opción Recomendada:** Sube un **archivo `.txt`** con 200, 500 o 1,000+ líneas.\n"
            "2. **Opción Texto:** Pega el texto directamente aquí en el chat."
        )
        enviar_temporal(message.chat.id, txt)
        return

    # Paso 2: Procesar líneas masivas (Texto o Archivo)
    elif paso == "esperando_lineas":
        list_id = estado.get("list_id")
        nombre_cat = estado.get("nombre")
        lineas = []

        # Subida por archivo .txt
        if message.document:
            try:
                file_info = bot.get_file(message.document.file_id)
                downloaded_file = bot.download_file(file_info.file_path)
                contenido = downloaded_file.decode("utf-8", errors="ignore")
                lineas = [l.strip() for l in contenido.splitlines() if l.strip()]
            except Exception:
                enviar_temporal(message.chat.id, "❌ Error al leer el archivo. Sube un `.txt` válido.")
                return
        # Subida por texto pegado
        elif message.text:
            lineas = [l.strip() for l in message.text.splitlines() if l.strip()]

        borrar_comando(message)
        del USER_STATE[user_id]

        if not lineas:
            enviar_temporal(message.chat.id, "⚠️ No se detectaron líneas válidas para guardar.")
            return

        with lock_db:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            for l in lineas:
                cursor.execute("INSERT INTO lines (list_id, line_text) VALUES (?, ?)", (list_id, l))
            conn.commit()
            conn.close()

        # Enviar copia de respaldo al grupo Storage como archivo .txt para no romper límites
        try:
            buffer_txt = io.BytesIO("\n".join(lineas).encode('utf-8'))
            buffer_txt.name = f"{nombre_cat}_lineas_{len(lineas)}.txt"
            bot.send_document(
                STORAGE_CHAT_ID,
                buffer_txt,
                caption=f"#CARGA_MASIVA\n📁 LISTA: {nombre_cat}\n👤 OWNER: {user_id}\n🔢 TOTAL LÍNEAS: {len(lineas)}"
            )
        except Exception:
            pass

        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(f"🔑 GENERAR {len(lineas)} KEYS DE {nombre_cat}", callback_data=f"ejecutar_gen_{list_id}"),
            types.InlineKeyboardButton("🔙 VOLVER AL MENÚ", callback_data="btn_volver_inicio")
        )

        enviar_temporal(
            message.chat.id,
            f"✅ **Líneas Guardadas Correctamente**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📁 **Lista:** `{nombre_cat}`\n"
            f"📄 **Líneas añadidas:** `{len(lineas)}`\n\n"
            f"👉 _Toca el botón grande abajo para generar las llaves vinculadas 1x1:_",
            markup=markup
        )
        return

    # Paso 3: Canjear Key
    elif paso == "esperando_key":
        if not message.text:
            return
        list_id = estado.get("list_id")
        categoria = estado.get("categoria")
        key_ingresada = message.text.strip().upper()
        borrar_comando(message)
        del USER_STATE[user_id]

        with lock_db:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()

            # Comprobar 1 key por usuario por lista
            cursor.execute("SELECT id FROM claims WHERE user_id = ? AND list_id = ?", (user_id, list_id))
            if cursor.fetchone():
                conn.close()
                enviar_temporal(message.chat.id, f"❌ **Acceso denegado.** Ya canjeaste una key en `{categoria}`.")
                return

            cursor.execute("""
                SELECT k.id, l.line_text 
                FROM keys k 
                JOIN lines l ON k.line_id = l.id 
                WHERE k.key_value = ? AND k.list_id = ? AND k.claimed = 0
            """, (key_ingresada, list_id))
            key_data = cursor.fetchone()

            if not key_data:
                conn.close()
                enviar_temporal(message.chat.id, f"❌ Key inválida, agotada o no pertenece a `{categoria}`.")
                return

            k_id, linea_entregada = key_data
            cursor.execute("UPDATE keys SET claimed = 1, claimed_by = ?, claimed_at = CURRENT_TIMESTAMP WHERE id = ?", (user_id, k_id))
            cursor.execute("INSERT INTO claims (user_id, list_id, key_id) VALUES (?, ?, ?)", (user_id, list_id, k_id))
            conn.commit()
            conn.close()

        try:
            bot.send_message(
                STORAGE_CHAT_ID,
                f"#CANJE\nUSER_ID: {user_id}\nUSER: @{message.from_user.username or 'Anon'}\nLISTA: {categoria}\nKEY: {key_ingresada}"
            )
        except Exception:
            pass

        texto_exito = (
            f"🎉 **CANJE COMPLETADO - {categoria}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 **Tu dato entregado es:**\n\n"
            f"`{linea_entregada}`\n\n"
            f"⏱ _Este mensaje se autodestruirá en {TIEMPO_AUTO_ELIMINAR}s._"
        )
        enviar_temporal(message.chat.id, texto_exito)
        return

# --- CALLBACKS Y NAVEGACIÓN ---
@bot.callback_query_handler(func=lambda call: True)
def router_botones(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    data = call.data

    if data == "btn_guardar_data":
        if not usuario_autorizado(user_id):
            bot.answer_callback_query(call.id, "No tienes permisos.", show_alert=True)
            return
        USER_STATE[user_id] = {"paso": "esperando_nombre"}
        bot.answer_callback_query(call.id)
        enviar_temporal(chat_id, "📝 **Escribe el NOMBRE de la lista/botón (ej: HVN o HVN2):**")

    elif data == "btn_elegir_gen_keys":
        if not usuario_autorizado(user_id):
            bot.answer_callback_query(call.id, "No tienes permisos.", show_alert=True)
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
            bot.answer_callback_query(call.id, "No tienes listas creadas.", show_alert=True)
            return

        markup = types.InlineKeyboardMarkup(row_width=1)
        for l_id, l_name in listas:
            markup.add(types.InlineKeyboardButton(f"🔑 ┃ Generar Keys para: {l_name}", callback_data=f"ejecutar_gen_{l_id}"))
        markup.add(types.InlineKeyboardButton("🔙 ┃ Volver al Menú", callback_data="btn_volver_inicio"))

        bot.answer_callback_query(call.id)
        enviar_temporal(chat_id, "⚙️ **Elige la lista a la cual generar keys:**", markup=markup)

    elif data.startswith("ejecutar_gen_"):
        l_id = int(data.replace("ejecutar_gen_", ""))
        
        with lock_db:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM lists WHERE id = ?", (l_id,))
            res_n = cursor.fetchone()
            if not res_n:
                conn.close()
                bot.answer_callback_query(call.id, "Lista no encontrada.")
                return
            l_name = res_n[0]

            cursor.execute("""
                SELECT l.id FROM lines l 
                LEFT JOIN keys k ON l.id = k.line_id 
                WHERE l.list_id = ? AND k.id IS NULL
            """, (l_id,))
            lineas_sin_key = cursor.fetchall()

            if not lineas_sin_key:
                conn.close()
                bot.answer_callback_query(call.id, f"Todas las líneas de {l_name} ya tienen keys.", show_alert=True)
                return

            keys_generadas = []
            for (line_id,) in lineas_sin_key:
                k_val = generar_llave(l_name)
                cursor.execute("INSERT INTO keys (list_id, line_id, key_value) VALUES (?, ?, ?)", (l_id, line_id, k_val))
                keys_generadas.append(k_val)

            conn.commit()
            conn.close()

        bot.answer_callback_query(call.id, "¡Generación completada!")

        # Enviar archivo .txt con todas las keys si son muchas
        if len(keys_generadas) > 20:
            buffer_keys = io.BytesIO("\n".join(keys_generadas).encode('utf-8'))
            buffer_keys.name = f"KEYS_{l_name}_{len(keys_generadas)}.txt"
            doc_msg = bot.send_document(
                chat_id,
                buffer_keys,
                caption=f"🔑 **{len(keys_generadas)} Keys Generadas para `{l_name}`**\n_Descarga el archivo con tu lote completo._"
            )
            auto_destruir_mensaje(chat_id, [doc_msg.message_id], delay=TIEMPO_AUTO_ELIMINAR)
        else:
            txt_k = "\n".join(keys_generadas)
            enviar_temporal(chat_id, f"🔑 **{len(keys_generadas)} Keys Generadas para `{l_name}`:**\n\n`{txt_k}`")

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
            bot.answer_callback_query(call.id, "No hay keys disponibles.", show_alert=True)
            return

        markup = types.InlineKeyboardMarkup(row_width=1)
        for l_id, l_name, stock in listas_activas:
            markup.add(types.InlineKeyboardButton(f"🎁 ┃ {l_name} — (Disponibles: {stock})", callback_data=f"pedir_key_{l_id}_{l_name}"))
        markup.add(types.InlineKeyboardButton("🔙 ┃ Volver al Menú", callback_data="btn_volver_inicio"))

        bot.answer_callback_query(call.id)
        enviar_temporal(chat_id, "📌 **Selecciona la categoría que deseas canjear:**", markup=markup)

    elif data.startswith("pedir_key_"):
        _, _, l_id, categoria = data.split("_", 3)
        USER_STATE[user_id] = {"paso": "esperando_key", "list_id": int(l_id), "categoria": categoria}
        bot.answer_callback_query(call.id)
        enviar_temporal(chat_id, f"🔐 **Pega tu KEY para `{categoria}`:**\n*(Límite: 1 canje por usuario)*")

    elif data == "btn_mis_listas":
        if not usuario_autorizado(user_id):
            return

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        if es_admin(user_id):
            cursor.execute("""
                SELECT l.name, COUNT(DISTINCT ln.id), COUNT(DISTINCT k.id) 
                FROM lists l 
                LEFT JOIN lines ln ON l.id = ln.list_id 
                LEFT JOIN keys k ON l.id = k.list_id AND k.claimed = 0
                GROUP BY l.id
            """)
        else:
            cursor.execute("""
                SELECT l.name, COUNT(DISTINCT ln.id), COUNT(DISTINCT k.id) 
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

        txt = "📦 **LISTAS REGISTRADAS Y STOCK ACTUAL:**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        for name, total_l, stock_k in listas:
            txt += f"🔹 **{name}**\n   ├ 📄 Líneas totales: `{total_l}`\n   └ 🔑 Keys libres: `{stock_k}`\n\n"

        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton("🔙 ┃ Volver al Menú", callback_data="btn_volver_inicio"))

        bot.answer_callback_query(call.id)
        enviar_temporal(chat_id, txt, markup=markup)

    elif data == "btn_volver_inicio":
        bot.answer_callback_query(call.id)
        cmd_menu(call.message)

# --- ENTRYPOINT VERCEL ---
@app.route("/", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        return "Bot activo", 200

    try:
        json_data = request.get_json(silent=True)
        if json_data:
            update = telebot.types.Update.de_json(json_data)
            bot.process_new_updates([update])
        return "OK", 200
    except Exception:
        return "OK", 200
