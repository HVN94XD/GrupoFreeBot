import os
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

app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

USER_STATE = {}

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
            "Este bot es privado. El Administrador principal no está en el grupo.\n"
            "Saliendo..."
        )
        bot.send_message(chat_id, alerta, parse_mode="Markdown")
        bot.leave_chat(chat_id)
    except Exception:
        pass
    return False

def usuario_autorizado(user_id):
    if user_id == ADMIN_ID:
        return True
    try:
        member = bot.get_chat_member(STORAGE_CHAT_ID, user_id)
        if member.status in ['creator', 'administrator', 'member']:
            return True
    except Exception:
        pass
    return False

def generar_llave(longitud=12):
    caracteres = string.ascii_uppercase + string.digits
    return "KEY-" + "".join(secrets.choice(caracteres) for _ in range(longitud))

# --- COMANDOS ---
@bot.message_handler(commands=['autorizar'])
def cmd_autorizar(message):
    borrar_comando(message)
    if message.from_user.id != ADMIN_ID:
        return
    partes = message.text.split()
    if len(partes) < 2:
        enviar_temporal(message.chat.id, "⚠️ Usa: `/autorizar @usuario`")
        return
    target_user = partes[1].strip()
    registro_txt = f"#AUTH\nUSER: {target_user}\nBY_ADMIN: {message.from_user.id}\nFECHA: {time.strftime('%Y-%m-%d %H:%M:%S')}"
    bot.send_message(STORAGE_CHAT_ID, registro_txt)
    enviar_temporal(message.chat.id, f"✅ Usuario `{target_user}` autorizado.")

@bot.message_handler(commands=['guardar'])
def cmd_iniciar_guardado(message):
    borrar_comando(message)
    if not usuario_autorizado(message.from_user.id):
        enviar_temporal(message.chat.id, "🚫 No estás autorizado.")
        return
    USER_STATE[message.from_user.id] = {"paso": "esperando_nombre"}
    enviar_temporal(message.chat.id, "📝 **Ingresa el NOMBRE de la categoría/botón (ej: HVN o HVN2):**")

@bot.message_handler(func=lambda m: m.from_user.id in USER_STATE and m.chat.type in ['group', 'supergroup', 'private'])
def flujo_carga(message):
    user_id = message.from_user.id
    estado = USER_STATE.get(user_id, {})
    paso = estado.get("paso")

    if paso == "esperando_nombre":
        nombre_cat = message.text.strip().upper().replace(" ", "_")
        USER_STATE[user_id] = {"paso": "esperando_lineas", "categoria": nombre_cat}
        borrar_comando(message)
        enviar_temporal(message.chat.id, f"✅ Categoría: `{nombre_cat}`\n\nEnvía la **lista de líneas** (una por renglón):")
        return

    elif paso == "esperando_lineas":
        categoria = estado.get("categoria")
        lineas = [l.strip() for l in message.text.split("\n") if l.strip()]
        borrar_comando(message)
        del USER_STATE[user_id]

        if not lineas:
            enviar_temporal(message.chat.id, "⚠️ No enviaste líneas válidas.")
            return

        msg_pack = bot.send_message(
            STORAGE_CHAT_ID,
            f"#DATA_PACK\nCAT: {categoria}\nOWNER_ID: {user_id}\nTOTAL: {len(lineas)}\n---\n" + "\n".join(lineas)
        )

        keys_generadas = [f"{generar_llave()}:{idx}" for idx, _ in enumerate(lineas)]

        bot.send_message(
            STORAGE_CHAT_ID,
            f"#KEYS\nCAT: {categoria}\nPACK_MSG_ID: {msg_pack.message_id}\n" + "\n".join(keys_generadas)
        )

        enviar_temporal(message.chat.id, f"✅ **Guardado con éxito**\nCategoría: `{categoria}`\nLíneas: `{len(lineas)}`\nKeys generadas 1x1.")

@bot.message_handler(commands=['start', 'menu'])
def cmd_menu(message):
    borrar_comando(message)
    if message.chat.type in ['group', 'supergroup'] and not validar_seguridad_grupo(message.chat.id):
        return
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🎁 Cuenta FREE / Canjear", callback_data="btn_cuentas_free"))
    enviar_temporal(message.chat.id, "👑 **PANEL DE CONTROL**\nSelecciona una opción:", markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callbacks_panel(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id

    if call.data == "btn_cuentas_free":
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("🔹 HVN", callback_data="pedir_key_HVN"),
            types.InlineKeyboardButton("🔹 HVN2", callback_data="pedir_key_HVN2")
        )
        bot.answer_callback_query(call.id)
        enviar_temporal(chat_id, "📌 **Elige la categoría a canjear:**", markup=markup)

    elif call.data.startswith("pedir_key_"):
        categoria = call.data.replace("pedir_key_", "")
        USER_STATE[user_id] = {"paso": "esperando_key", "categoria": categoria}
        bot.answer_callback_query(call.id)
        enviar_temporal(chat_id, f"🔑 **Pega tu KEY para `{categoria}`:**")

@bot.message_handler(func=lambda m: m.from_user.id in USER_STATE and USER_STATE[m.from_user.id].get("paso") == "esperando_key")
def procesar_canje(message):
    user_id = message.from_user.id
    key_ingresada = message.text.strip()
    categoria = USER_STATE[user_id].get("categoria")
    borrar_comando(message)
    del USER_STATE[user_id]

    bot.send_message(
        STORAGE_CHAT_ID,
        f"#CANJE\nUSER_ID: {user_id}\nUSER: @{message.from_user.username or 'Anon'}\nCAT: {categoria}\nKEY: {key_ingresada}\nTIME: {time.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    enviar_temporal(message.chat.id, f"🎉 **CANJE EXITOSO - {categoria}**\n📋 Entrega: `ACCESO_VALIDO_{key_ingresada[:6]}`\n\n⏱ Se auto-elimina en {TIEMPO_AUTO_ELIMINAR}s.")

# --- ENTRADA DEL SERVIDOR ---
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
