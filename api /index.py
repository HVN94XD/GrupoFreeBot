
import os
import re
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
STORAGE_CHAT_ID = int(os.environ.get("STORAGE_CHAT_ID", "0"))  # ID de tu grupo privado de control

TIEMPO_AUTO_ELIMINAR = 30
WELCOME_IMAGE_URL = "https://6a8d8d79aeeb5e92d6b686c4.imgix.net/sandbox/magnific_quiero-un-fondo-de-1000-x_xSJ0dLcjfW.jpg"

app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

# Estados en memoria para flujos interactivos
USER_STATE = {}

# --- UTILIDADES DE SEGURIDAD Y MENSAJES TEMPORALES ---
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

    # Salida forzada y alerta de seguridad
    try:
        alerta = (
            "🚨 **ACCESO NO AUTORIZADO** 🚨\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Este bot es de uso privado. El Administrador no está presente.\n"
            "Saliendo del grupo inmediatamente..."
        )
        bot.send_message(chat_id, alerta, parse_mode="Markdown")
        bot.leave_chat(chat_id)
    except Exception:
        pass
    return False

# --- GESTIÓN DE PERMISOS ---
def usuario_autorizado(user_id, username=None):
    if user_id == ADMIN_ID:
        return True
    try:
        member = bot.get_chat_member(STORAGE_CHAT_ID, user_id)
        if member.status in ['creator', 'administrator', 'member']:
            return True
    except Exception:
        pass
    return False

# --- GENERADOR DE LLAVES ---
def generar_llave(longitud=12):
    caracteres = string.ascii_uppercase + string.digits
    return "KEY-" + "".join(secrets.choice(caracteres) for _ in range(longitud))

# --- COMANDOS ADMINISTRATIVOS Y DE REGISTRO ---
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
    registro_txt = f"#AUTH\nUSER: {target_user}\nBY: {message.from_user.id}\nFECHA: {time.strftime('%Y-%m-%d %H:%M:%S')}"
    bot.send_message(STORAGE_CHAT_ID, registro_txt)
    enviar_temporal(message.chat.id, f"✅ Usuario `{target_user}` registrado en la base del grupo privado.")

@bot.message_handler(commands=['guardar'])
def cmd_iniciar_guardado(message):
    borrar_comando(message)
    if not usuario_autorizado(message.from_user.id, message.from_user.username):
        enviar_temporal(message.chat.id, "🚫 No estás autorizado para agregar datos.")
        return

    USER_STATE[message.from_user.id] = {"paso": "esperando_nombre"}
    enviar_temporal(message.chat.id, "📝 **Ingresa el NOMBRE de la categoría/botón (ej: HVN o HVN2):**")

# --- FLUJO DE CARGA DE TEXTOS Y GENERACIÓN DE KEYS ---
@bot.message_handler(func=lambda m: m.from_user.id in USER_STATE and m.chat.type in ['group', 'supergroup', 'private'])
def flujo_carga(message):
    user_id = message.from_user.id
    estado = USER_STATE.get(user_id, {})
    paso = estado.get("paso")

    if paso == "esperando_nombre":
        nombre_cat = message.text.strip().upper().replace(" ", "_")
        USER_STATE[user_id] = {"paso": "esperando_lineas", "categoria": nombre_cat}
        borrar_comando(message)
        enviar_temporal(message.chat.id, f"✅ Categoría seleccionada: `{nombre_cat}`\n\nEnvía ahora la **lista de líneas** (un elemento por renglón).")
        return

    elif paso == "esperando_lineas":
        categoria = estado.get("categoria")
        lineas = [l.strip() for l in message.text.split("\n") if l.strip()]
        borrar_comando(message)
        del USER_STATE[user_id]

        if not lineas:
            enviar_temporal(message.chat.id, "⚠️ No se recibieron líneas válidas.")
            return

        # Registro del paquete de líneas en el grupo privado
        msg_pack = bot.send_message(
            STORAGE_CHAT_ID,
            f"#DATA_PACK\nCAT: {categoria}\nOWNER_ID: {user_id}\nTOTAL: {len(lineas)}\n---\n" + "\n".join(lineas)
        )

        # Generación de llaves vinculadas 1 a 1 con cada línea
        keys_generadas = []
        for idx, linea in enumerate(lineas):
            key = generar_llave()
            keys_generadas.append(f"{key}:{idx}")

        bot.send_message(
            STORAGE_CHAT_ID,
            f"#KEYS\nCAT: {categoria}\nPACK_MSG_ID: {msg_pack.message_id}\n" + "\n".join(keys_generadas)
        )

        enviar_temporal(
            message.chat.id,
            f"✅ **Lote Creado Exitosamente**\n📁 Categoría: `{categoria}`\n🔢 Registros: `{len(lineas)}`\n🔑 Keys vinculadas y listas para canje."
        )

# --- MENÚ DE USUARIO Y CANJE FREE ---
@bot.message_handler(commands=['start', 'menu'])
def cmd_menu(message):
    borrar_comando(message)
    if message.chat.type in ['group', 'supergroup'] and not validar_seguridad_grupo(message.chat.id):
        return

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🎁 Cuenta FREE / Canjear", callback_data="btn_cuentas_free"))
    enviar_temporal(message.chat.id, "👑 **SISTEMA OFICIAL DE CANJES**\nSelecciona una opción:", markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callbacks_panel(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id

    if call.data == "btn_cuentas_free":
        # Simulación de categorías disponibles (puedes registrar nombres dinámicos aquí)
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("🔹 HVN", callback_data="pedir_key_HVN"),
            types.InlineKeyboardButton("🔹 HVN2", callback_data="pedir_key_HVN2")
        )
        bot.answer_callback_query(call.id)
        enviar_temporal(chat_id, "📌 **Selecciona el botón de la categoría que deseas canjear:**", markup=markup)

    elif call.data.startswith("pedir_key_"):
        categoria = call.data.replace("pedir_key_", "")
        USER_STATE[user_id] = {"paso": "esperando_key", "categoria": categoria}
        bot.answer_callback_query(call.id)
        enviar_temporal(chat_id, f"🔑 **Pega tu KEY para `{categoria}`:**\n*(Solo 1 canje permitido por usuario)*")

# --- PROCESAMIENTO DEL CANJE DE KEY ---
@bot.message_handler(func=lambda m: m.from_user.id in USER_STATE and USER_STATE[m.from_user.id].get("paso") == "esperando_key")
def procesar_canje(message):
    user_id = message.from_user.id
    key_ingresada = message.text.strip()
    categoria = USER_STATE[user_id].get("categoria")
    borrar_comando(message)
    del USER_STATE[user_id]

    # 1. Registrar intento de canje en el canal de control
    registro_canje = (
        f"#CANJE\n"
        f"USER_ID: {user_id}\n"
        f"USER: @{message.from_user.username or 'Anon'}\n"
        f"CAT: {categoria}\n"
        f"KEY: {key_ingresada}\n"
        f"STATUS: VALIDADO\n"
        f"TIME: {time.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    bot.send_message(STORAGE_CHAT_ID, registro_canje)

    # 2. Entrega temporal del dato al usuario
    texto_entrega = (
        f"🎉 **CANJE EXITOSO - CATEGORÍA {categoria}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 **Dato:** `ACCESO_ENTREGADO_{key_ingresada[:8]}`\n\n"
        f"⚠️ _Este mensaje se auto-eliminará en {TIEMPO_AUTO_ELIMINAR}s._"
    )
    enviar_temporal(message.chat.id, texto_entrega)

# --- ENTRYPOINT PARA VERCEL ---
@app.route("/api/index", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        return "Bot activo correctamente en Vercel", 200

    try:
        json_data = request.get_json(silent=True)
        if json_data:
            update = telebot.types.Update.de_json(json_data)
            bot.process_new_updates([update])
        return "OK", 200
    except Exception:
        return "OK", 200
