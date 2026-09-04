import logging
import os
import requests
from dotenv import load_dotenv
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from db.conexion import obtener_conexion, liberar_conexion, iniciar_pool

from dotenv import load_dotenv

# ── Token del bot ──
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")

if TOKEN is None:
    raise ValueError("No se encontró la variable BOT_TOKEN en el archivo .env")

# ── Carpeta donde se guardan las fotos ──
CARPETA_FOTOS = "/app/fotos"

logging.basicConfig(level=logging.INFO)

# ═══════════════════════════════════════════════════
# UTILIDADES DE BASE DE DATOS
# ═══════════════════════════════════════════════════

def obtener_usuario(telegram_id: int):
    """Devuelve todos los roles del usuario para ese telegram_id, o None.
    Retorna (id, [roles], referencia_id, nombre)
    """
    conn = obtener_conexion()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, rol, referencia_id, nombre
            FROM usuarios_bot
            WHERE telegram_id = %s AND activo = TRUE
        """, (telegram_id,))
        filas = cur.fetchall()
        if not filas:
            return None
        base  = filas[0]
        roles = [f[1] for f in filas]
        return (base[0], roles, base[2], base[3])
    finally:
        liberar_conexion(conn)


def obtener_viajes_activos_por_operador(referencia_id: int):
    """Devuelve todos los viajes en_curso asignados a ese operador."""
    conn = obtener_conexion()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT v.id, v.folio, v.numero_ruta, v.origen, v.destino, v.estatus_bot, u.eco
            FROM viajes v
            JOIN unidades u ON u.id = v.unidad_id
            WHERE v.operador_id = %s AND v.estatus = 'en_curso'
            ORDER BY v.fecha_salida DESC
        """, (referencia_id,))
        return cur.fetchall()
    finally:
        liberar_conexion(conn)


def obtener_encargado_telegram_id():
    """Devuelve el telegram_id del encargado registrado."""
    conn = obtener_conexion()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT telegram_id FROM usuarios_bot
            WHERE rol = 'encargado' AND activo = TRUE
            LIMIT 1
        """)
        resultado = cur.fetchone()
        return resultado[0] if resultado else None
    finally:
        liberar_conexion(conn)


def obtener_despachador_telegram_id():
    """Devuelve el telegram_id del despachador registrado."""
    conn = obtener_conexion()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT telegram_id FROM usuarios_bot
            WHERE rol = 'despachador' AND activo = TRUE
            LIMIT 1
        """)
        resultado = cur.fetchone()
        return resultado[0] if resultado else None
    finally:
        liberar_conexion(conn)


# ═══════════════════════════════════════════════════
# COMANDOS DEL OPERADOR
# ═══════════════════════════════════════════════════

async def cmd_inicio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuario = obtener_usuario(update.effective_user.id)
    if not usuario or ('operador' not in usuario[1]):
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return

    viajes = obtener_viajes_activos_por_operador(usuario[2])
    if not viajes:
        await update.message.reply_text("No tienes ningún viaje asignado actualmente.")
        return

    # Tomar el viaje más reciente (primero en la lista ordenada por fecha)
    viaje = viajes[0]

    conn = obtener_conexion()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE viajes SET estatus_bot = 'en_curso' WHERE id = %s", (viaje[0],))
        conn.commit()
    finally:
        liberar_conexion(conn)

    await update.message.reply_text(
        f"✅ Viaje iniciado.\nFolio {viaje[1]} — {viaje[3]} → {viaje[4]}\n"
        f"Te voy a solicitar fotos del display cada 5 horas.\n"
        f"Recuerda mandar /descanso cuando vayas a dormir."
    )

    context.job_queue.run_once(
        solicitar_foto,
        when=timedelta(hours=5),
        data={"viaje_id": viaje[0], "operador_telegram_id": update.effective_user.id,
              "numero_ruta": viaje[2], "folio": viaje[1], "intento": 1},
        name=f"foto_{viaje[0]}"
    )


async def cmd_descanso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuario = obtener_usuario(update.effective_user.id)
    if not usuario or ('operador' not in usuario[1]):
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return

    viajes = obtener_viajes_activos_por_operador(usuario[2])
    if not viajes:
        await update.message.reply_text("No tienes ningún viaje activo.")
        return

    viaje = viajes[0]
    conn = obtener_conexion()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE viajes SET estatus_bot = 'descanso' WHERE id = %s", (viaje[0],))
        conn.commit()
    finally:
        liberar_conexion(conn)

    jobs = context.job_queue.get_jobs_by_name(f"foto_{viaje[0]}")
    for job in jobs:
        job.schedule_removal()

    await update.message.reply_text("🌙 Descansa. Antes de dormir manda una foto del display al bot.\nCuando reanudes el viaje manda /retome y una foto del display.")


async def cmd_retome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuario = obtener_usuario(update.effective_user.id)
    if not usuario or ('operador' not in usuario[1]):
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return

    viajes = obtener_viajes_activos_por_operador(usuario[2])
    if not viajes:
        await update.message.reply_text("No tienes ningún viaje activo.")
        return

    viaje = viajes[0]
    conn = obtener_conexion()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE viajes SET estatus_bot = 'en_curso' WHERE id = %s", (viaje[0],))
        conn.commit()
    finally:
        liberar_conexion(conn)

    await update.message.reply_text("✅ Bienvenido de vuelta. Por favor manda una foto del display del termo.")

    context.job_queue.run_once(
        solicitar_foto,
        when=timedelta(hours=5),
        data={"viaje_id": viaje[0], "operador_telegram_id": update.effective_user.id,
              "numero_ruta": viaje[2], "folio": viaje[1], "intento": 1},
        name=f"foto_{viaje[0]}"
    )


async def cmd_fin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """El operador solicita finalizar. El encargado confirma qué viaje."""
    usuario = obtener_usuario(update.effective_user.id)
    if not usuario or ('operador' not in usuario[1]):
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return

    viajes = obtener_viajes_activos_por_operador(usuario[2])
    if not viajes:
        await update.message.reply_text("No tienes ningún viaje activo.")
        return

    encargado_id = obtener_encargado_telegram_id()
    if not encargado_id:
        await update.message.reply_text("No hay encargado disponible. Contacta a tu supervisor.")
        return

    # Verificar si ya hay una confirmación pendiente en la cola
    cola = context.bot_data.setdefault("cola_fin", [])
    operador_nombre = usuario[3]

    # Agregar a la cola
    cola.append({
        "operador_telegram_id": update.effective_user.id,
        "operador_nombre": operador_nombre,
        "viajes": viajes
    })

    await update.message.reply_text(
        "✅ Solicitud enviada al encargado. Espera confirmación."
    )

    # Si es el único en la cola, procesarlo inmediatamente
    if len(cola) == 1:
        await procesar_siguiente_fin(context, encargado_id)


async def procesar_siguiente_fin(context: ContextTypes.DEFAULT_TYPE, encargado_id: int):
    """Manda al encargado la siguiente solicitud de fin en la cola."""
    cola = context.bot_data.get("cola_fin", [])
    if not cola:
        return

    solicitud = cola[0]
    viajes    = solicitud["viajes"]
    nombre    = solicitud["operador_nombre"]

    botones = [
        [InlineKeyboardButton(
            f"Folio {v[1]} | {v[6]} | Ruta {v[2]} | {v[3]} → {v[4]}",
            callback_data=f"fin_confirmar_{v[0]}_{solicitud['operador_telegram_id']}"
        )]
        for v in viajes
    ]
    botones.append([InlineKeyboardButton("❌ Cancelar", callback_data="fin_cancelar")])

    await context.bot.send_message(
        chat_id=encargado_id,
        text=f"El operador {nombre} quiere finalizar un viaje. ¿Cuál es?",
        reply_markup=InlineKeyboardMarkup(botones)
    )


async def callback_fin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """El encargado confirma qué viaje finaliza."""
    query = update.callback_query
    await query.answer()

    encargado_id = obtener_encargado_telegram_id()

    if query.data == "fin_cancelar":
        await query.edit_message_text("Cancelado.")
        cola = context.bot_data.get("cola_fin", [])
        if cola:
            solicitud = cola.pop(0)
            await context.bot.send_message(
                chat_id=solicitud["operador_telegram_id"],
                text="El encargado canceló la solicitud. Contacta a tu supervisor."
            )
        if cola:
            await procesar_siguiente_fin(context, encargado_id)
        return

    partes   = query.data.split("_")
    viaje_id = int(partes[2])
    operador_telegram_id = int(partes[3])

    conn = obtener_conexion()
    try:
        cur = conn.cursor()
        cur.execute("SELECT folio, origen, destino FROM viajes WHERE id = %s", (viaje_id,))
        viaje = cur.fetchone()
        ahora = datetime.now()
        cur.execute("""
            UPDATE viajes
            SET estatus = 'completado', estatus_bot = 'completado',
                fecha_llegada_real = %s, hora_llegada_real = %s
            WHERE id = %s
        """, (ahora.date(), ahora.strftime("%H:%M"), viaje_id))
        conn.commit()
    finally:
        liberar_conexion(conn)

    jobs = context.job_queue.get_jobs_by_name(f"foto_{viaje_id}")
    for job in jobs:
        job.schedule_removal()

    await query.edit_message_text(
        f"✅ Viaje finalizado.\nFolio {viaje[0]} — {viaje[1]} → {viaje[2]}"
    )
    await context.bot.send_message(
        chat_id=operador_telegram_id,
        text=f"✅ Tu viaje ha sido finalizado. Buen trabajo."
    )

    # Procesar siguiente en la cola
    cola = context.bot_data.get("cola_fin", [])
    if cola:
        cola.pop(0)
    if cola:
        await procesar_siguiente_fin(context, encargado_id)


# ═══════════════════════════════════════════════════
# SOLICITUD Y RECEPCIÓN DE FOTOS
# ═══════════════════════════════════════════════════

async def solicitar_foto(context: ContextTypes.DEFAULT_TYPE):
    """Job que le pide la foto al operador."""
    data = context.job.data
    viaje_id             = data["viaje_id"]
    operador_telegram_id = data["operador_telegram_id"]
    numero_ruta          = data["numero_ruta"]
    intento              = data["intento"]

    # Verificar que el viaje sigue activo y no está en descanso
    conn = obtener_conexion()
    try:
        cur = conn.cursor()
        cur.execute("SELECT estatus, estatus_bot, temperatura FROM viajes WHERE id = %s", (viaje_id,))
        viaje = cur.fetchone()

    finally:
        liberar_conexion(conn)
    if not viaje or viaje[0] == 'completado' or viaje[1] == 'descanso':
        return
    if viaje[2] not in ('Congelado', 'Refrigerado'):
        return
    



    # Registrar la solicitud en la BD
    conn = obtener_conexion()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO solicitudes_foto (viaje_id, estatus)
            VALUES (%s, 'pendiente') RETURNING id
        """, (viaje_id,))
        solicitud_id = cur.fetchone()[0]
        conn.commit()
    finally:
        liberar_conexion(conn)

    await context.bot.send_message(
        chat_id=operador_telegram_id,
        text=f"📸 Por favor manda una foto del display del termo.\nRuta {numero_ruta}."
    )

    # Guardar solicitud_id en el contexto del operador para relacionar la foto
    context.bot_data[f"solicitud_activa_{operador_telegram_id}"] = {
        "solicitud_id": solicitud_id,
        "viaje_id": viaje_id,
        "numero_ruta": numero_ruta,
        "intento": intento
    }

    # Programar escalación si no responde en 1 minuto (prueba, cambiar a 30 en producción)
    context.job_queue.run_once(
        verificar_respuesta_foto,
        when=timedelta(minutes=30),
        data={**data, "solicitud_id": solicitud_id, "intento": intento},
        name=f"verificar_{viaje_id}_{intento}"
    )


async def verificar_respuesta_foto(context: ContextTypes.DEFAULT_TYPE):
    """Verifica si el operador respondió. Si no, reintenta o escala."""
    data         = context.job.data
    solicitud_id = data["solicitud_id"]
    viaje_id     = data["viaje_id"]
    numero_ruta  = data["numero_ruta"]
    intento      = data["intento"]
    operador_telegram_id = data["operador_telegram_id"]

    conn = obtener_conexion()
    try:
        cur = conn.cursor()
        cur.execute("SELECT estatus FROM solicitudes_foto WHERE id = %s", (solicitud_id,))
        solicitud = cur.fetchone()
    finally:
        liberar_conexion(conn)

    if not solicitud or solicitud[0] == 'respondida':
        return  # Ya respondió, no hacer nada

    if intento == 1:
        # Primer intento sin respuesta → reintentar
        await context.bot.send_message(
            chat_id=operador_telegram_id,
            text=f"⚠️ Aún no hemos recibido la foto del display.\nRuta {numero_ruta}. Por favor mándala."
        )
        # Registrar segundo intento
        conn = obtener_conexion()
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO solicitudes_foto (viaje_id, estatus)
                VALUES (%s, 'pendiente') RETURNING id
            """, (viaje_id,))
            nueva_solicitud_id = cur.fetchone()[0]
            conn.commit()
        finally:
            liberar_conexion(conn)

        context.bot_data[f"solicitud_activa_{operador_telegram_id}"] = {
            "solicitud_id": nueva_solicitud_id,
            "viaje_id": viaje_id,
            "numero_ruta": numero_ruta,
            "intento": 2
        }

        context.job_queue.run_once(
            verificar_respuesta_foto,
            when=timedelta(minutes=30),
            data={**data, "solicitud_id": nueva_solicitud_id, "intento": 2},
            name=f"verificar_{viaje_id}_2"
        )

    else:
        # Segundo intento sin respuesta → escalar al encargado
        encargado_id = obtener_encargado_telegram_id()
        if encargado_id:
            await context.bot.send_message(
                chat_id=encargado_id,
                text=f"🚨 El operador de la ruta {numero_ruta} no ha respondido "
                     f"las últimas dos solicitudes de foto del display. "
                     f"Por favor comunícate con él."
            )


async def recibir_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe la foto del operador, la descarga y la registra en la BD."""
    usuario = obtener_usuario(update.effective_user.id)
    if not usuario or ('operador' not in usuario[1]):
        return

    solicitud_activa = context.bot_data.get(f"solicitud_activa_{update.effective_user.id}")

    # Obtener viaje activo del operador para asociar la foto
    viajes   = obtener_viajes_activos_por_operador(usuario[2])
    viaje    = viajes[0] if viajes else None
    viaje_id = solicitud_activa["viaje_id"] if solicitud_activa else (viaje[0] if viaje else None)

    if not solicitud_activa:
        await update.message.reply_text("No tenía una solicitud de foto pendiente, pero la guardé de todas formas.")

    # Descargar la foto
    foto      = update.message.photo[-1]
    file_obj  = await context.bot.get_file(foto.file_id)
    nombre    = f"{foto.file_id}.jpg"
    ruta_disk = os.path.join(CARPETA_FOTOS, nombre)
    await file_obj.download_to_drive(ruta_disk)

    # Registrar la foto en la BD
    conn = obtener_conexion()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO fotos_termo (viaje_id, telegram_file_id, ruta_archivo)
            VALUES (%s, %s, %s)
        """, (viaje_id, foto.file_id, ruta_disk))

        if solicitud_activa:
            cur.execute("""
                UPDATE solicitudes_foto
                SET estatus = 'respondida', fecha_hora_respuesta = NOW()
                WHERE id = %s
            """, (solicitud_activa["solicitud_id"],))

        conn.commit()
    finally:
        liberar_conexion(conn)

    # Limpiar solicitud activa
    if solicitud_activa:
        context.bot_data.pop(f"solicitud_activa_{update.effective_user.id}", None)

    await update.message.reply_text("✅ Foto recibida. Gracias.")

    # Programar la siguiente solicitud en 5 horas
    if viaje:
        context.job_queue.run_once(
            solicitar_foto,
            when=timedelta(hours=5),
            data={"viaje_id": viaje[0], "operador_telegram_id": update.effective_user.id,
                  "numero_ruta": viaje[2], "folio": viaje[1], "intento": 1},
            name=f"foto_{viaje[0]}"
        )


# ═══════════════════════════════════════════════════
# FLUJO DEL DESPACHADOR — RENOVACIÓN DE LINK
# ═══════════════════════════════════════════════════

async def verificar_links_proximos(context: ContextTypes.DEFAULT_TYPE):
    """Job que corre cada hora y avisa al despachador si algún link caduca pronto."""
    conn = obtener_conexion()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT v.id, v.numero_ruta, v.link_expiracion, o.nombre, u.eco
            FROM viajes v
            JOIN operadores o ON o.id = v.operador_id
            JOIN unidades   u ON u.id = v.unidad_id
            WHERE v.estatus = 'en_curso'
              AND v.link_expiracion IS NOT NULL
              AND v.link_expiracion <= NOW() + INTERVAL '4 hours'
              AND v.link_expiracion > NOW()
        """)
        viajes = cur.fetchall()
    finally:
        liberar_conexion(conn)

    if not viajes:
        return

    despachador_id = obtener_despachador_telegram_id()
    if not despachador_id:
        return

    for viaje in viajes:
        viaje_id, numero_ruta, expiracion, operador, eco = viaje
        await context.bot.send_message(
            chat_id=despachador_id,
            text=f"⏰ El link de seguimiento caduca a las {expiracion.strftime('%H:%M')}.\n"
                 f"Ruta: {numero_ruta} | Unidad: {eco} | Operador: {operador}\n"
                 f"Folio: {viaje_id}\n"
                 f"Por favor responde con el nuevo link."
        )
        # Guardar qué viaje está esperando link nuevo
        context.bot_data[f"esperando_link_{despachador_id}"] = viaje_id


async def recibir_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe el link nuevo del despachador y actualiza la BD."""
    usuario = obtener_usuario(update.effective_user.id)
    if not usuario or ('despachador' not in usuario[1]):
        return

    viaje_id = context.bot_data.get(f"esperando_link_{update.effective_user.id}")
    if not viaje_id:
        return

    nuevo_link = update.message.text.strip()
    if not nuevo_link.startswith("http"):
        await update.message.reply_text("Eso no parece un link válido. Por favor manda solo el link.")
        return

    expiracion = datetime.now() + timedelta(days=7)
    conn = obtener_conexion()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE viajes
            SET link_sky = %s, link_expiracion = %s
            WHERE id = %s
        """, (nuevo_link, expiracion, viaje_id))
        conn.commit()
    finally:
        liberar_conexion(conn)

    context.bot_data.pop(f"esperando_link_{update.effective_user.id}", None)
    await update.message.reply_text(
        f"✅ Link actualizado. Caduca el {expiracion.strftime('%d/%m/%Y a las %H:%M')}."
    )


# ═══════════════════════════════════════════════════
# CAMBIO DE LINK POR EL ENCARGADO
# ═══════════════════════════════════════════════════

async def cmd_cambiolink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """El encargado inicia el proceso de cambio de link de una ruta."""
    usuario = obtener_usuario(update.effective_user.id)
    if not usuario or ('encargado' not in usuario[1]):
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return

    context.bot_data[f"cambiolink_paso_{update.effective_user.id}"] = "esperando_ruta"
    await update.message.reply_text("¿Cuál es el folio del viaje al que quieres cambiarle el link?")


async def recibir_cambiolink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja los pasos del flujo de cambio de link del encargado."""
    usuario = obtener_usuario(update.effective_user.id)
    if not usuario or ('encargado' not in usuario[1]):
        return

    paso = context.bot_data.get(f"cambiolink_paso_{update.effective_user.id}")
    texto = update.message.text.strip()

    if paso == "esperando_ruta":
        conn = obtener_conexion()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, folio, numero_ruta, origen, destino
                FROM viajes
                WHERE folio = %s AND estatus = 'en_curso'
                LIMIT 1
            """, (texto,))
            viaje = cur.fetchone()
        finally:
            liberar_conexion(conn)

        if not viaje:
            await update.message.reply_text(
                f"No encontré ningún viaje activo con el folio {texto}. Verifica e intenta de nuevo."
            )
            return

        context.bot_data[f"cambiolink_viaje_{update.effective_user.id}"] = viaje[0]
        context.bot_data[f"cambiolink_paso_{update.effective_user.id}"] = "esperando_link"
        await update.message.reply_text(
            f"Folio {viaje[1]} — Ruta {viaje[2]} — {viaje[3]} → {viaje[4]}\n"
            f"Manda el nuevo link de Sky Guardian."
        )

    elif paso == "esperando_link":
        if not texto.startswith("http"):
            await update.message.reply_text("Eso no parece un link válido. Manda solo el link.")
            return

        viaje_id   = context.bot_data.get(f"cambiolink_viaje_{update.effective_user.id}")
        expiracion = datetime.now() + timedelta(days=7)

        conn = obtener_conexion()
        try:
            cur = conn.cursor()
            cur.execute("""
                UPDATE viajes SET link_sky = %s, link_expiracion = %s
                WHERE id = %s
            """, (texto, expiracion, viaje_id))
            conn.commit()
        finally:
            liberar_conexion(conn)

        # Limpiar estado
        context.bot_data.pop(f"cambiolink_paso_{update.effective_user.id}", None)
        context.bot_data.pop(f"cambiolink_viaje_{update.effective_user.id}", None)

        context.bot_data[f"cambiolink_viaje_confirmado_{update.effective_user.id}"] = viaje_id

        await update.message.reply_text(
            f"✅ Link actualizado. Caduca el {expiracion.strftime('%d/%m/%Y a las %H:%M')}.\n"
            f"¿Hubo cambio de operador?",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Sí", callback_data="cambiolink_si_operador"),
                    InlineKeyboardButton("❌ No", callback_data="cambiolink_no_operador")
                ]
            ])
        )


# ═══════════════════════════════════════════════════
# AYUDA
# ═══════════════════════════════════════════════════

async def cmd_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra los comandos disponibles según el rol del usuario."""
    usuario = obtener_usuario(update.effective_user.id)
    if not usuario:
        await update.message.reply_text("No estás registrado en el sistema. Contacta al encargado.")
        return

    roles = usuario[1]
    secciones = []

    if 'operador' in roles:
        secciones.append(
            "👷 Operador:\n"
            "/inicio — Avisar que el viaje comenzó\n"
            "/descanso — Registrar que vas a descansar\n"
            "/retome — Avisar que retomaste el viaje\n"
            "/fin — Marcar el viaje como finalizado"
        )
    if 'despachador' in roles:
        secciones.append(
            "📦 Despachador:\n"
            "Cuando un link esté por caducar, el bot te avisará "
            "y solo tendrás que responder con el nuevo link."
        )
    if 'encargado' in roles:
        secciones.append(
            "🔧 Encargado:\n"
            "/cambiolink — Cambiar el link de seguimiento de una ruta\n"
            "/revertir — Reactivar un viaje finalizado por error\n"
            "Cuando un operador mande /fin, recibirás una solicitud para confirmar qué viaje finaliza."
        )

    if not secciones:
        texto = "No reconozco tu rol. Contacta al administrador."
    else:
        texto = "📋 Comandos disponibles:\n\n" + "\n\n".join(secciones) + "\n\n/ayuda — Ver este mensaje"

    await update.message.reply_text(texto)


# ═══════════════════════════════════════════════════
# REVERTIR VIAJE (ENCARGADO)
# ═══════════════════════════════════════════════════

async def cmd_revertir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """El encargado reactiva un viaje que se finalizó por error."""
    usuario = obtener_usuario(update.effective_user.id)
    if not usuario or ('encargado' not in usuario[1]):
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return

    context.bot_data[f"revertir_paso_{update.effective_user.id}"] = "esperando_ruta"
    await update.message.reply_text("¿Cuál es el folio del viaje que quieres reactivar?")


async def recibir_revertir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el flujo de revertir un viaje."""
    usuario = obtener_usuario(update.effective_user.id)
    if not usuario or ('encargado' not in usuario[1]):
        return

    paso = context.bot_data.get(f"revertir_paso_{update.effective_user.id}")
    if not paso:
        return

    folio = update.message.text.strip()

    conn = obtener_conexion()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, folio, origen, destino
            FROM viajes
            WHERE folio = %s AND estatus = 'completado'
            ORDER BY fecha_salida_real DESC
            LIMIT 1
        """, (folio,))
        viaje = cur.fetchone()
    finally:
        liberar_conexion(conn)

    if not viaje:
        await update.message.reply_text(
            f"No encontré ningún viaje completado con el folio {folio}."
        )
        context.bot_data.pop(f"revertir_paso_{update.effective_user.id}", None)
        return

    # Pedir confirmación
    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Sí, reactivar", callback_data=f"revertir_confirmar_{viaje[0]}"),
            InlineKeyboardButton("❌ Cancelar",       callback_data="revertir_cancelar")
        ]
    ])
    await update.message.reply_text(
        f"¿Confirmas que quieres reactivar este viaje?\n"
        f"Folio {viaje[1]} — {viaje[2]} → {viaje[3]}",
        reply_markup=teclado
    )
    context.bot_data.pop(f"revertir_paso_{update.effective_user.id}", None)


async def callback_revertir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirma o cancela la reactivación del viaje."""
    query = update.callback_query
    await query.answer()

    if query.data == "revertir_cancelar":
        await query.edit_message_text("Cancelado.")
        return

    viaje_id = int(query.data.split("_")[2])

    conn = obtener_conexion()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE viajes
            SET estatus = 'en_curso', estatus_bot = 'en_curso',
                fecha_llegada_real = NULL, hora_llegada_real = NULL
            WHERE id = %s
        """, (viaje_id,))
        conn.commit()
    finally:
        liberar_conexion(conn)

    await query.edit_message_text(
        "✅ Viaje reactivado. El operador debe mandar /retome para reanudar el seguimiento."
    )

async def callback_cambio_operador(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """El encargado decide si hubo cambio de operador tras cambiar el link."""
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if query.data == "cambiolink_no_operador":
        await query.edit_message_text("✅ Link actualizado. Sin cambio de operador.")
        return

    # Mostrar lista de operadores
    conn = obtener_conexion()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT u.referencia_id, u.nombre
            FROM usuarios_bot u
            WHERE u.rol = 'operador' AND u.activo = TRUE
            ORDER BY u.nombre
        """)
        operadores = cur.fetchall()
    finally:
        liberar_conexion(conn)

    viaje_id = context.bot_data.get(f"cambiolink_viaje_confirmado_{uid}")

    botones = [
        [InlineKeyboardButton(op[1], callback_data=f"nuevo_operador_{viaje_id}_{op[0]}")]
        for op in operadores
    ]

    await query.edit_message_text(
        "¿Quién es el nuevo operador?",
        reply_markup=InlineKeyboardMarkup(botones)
    )


async def callback_nuevo_operador(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Actualiza el operador asignado al viaje."""
    query = update.callback_query
    await query.answer()

    partes       = query.data.split("_")
    viaje_id     = int(partes[2])
    operador_id  = int(partes[3])

    # Obtener telegram_id del nuevo operador
    conn = obtener_conexion()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT telegram_id, nombre FROM usuarios_bot
            WHERE referencia_id = %s AND rol = 'operador' AND activo = TRUE
            LIMIT 1
        """, (operador_id,))
        nuevo_op = cur.fetchone()

        cur.execute("""
            UPDATE viajes SET operador_id = %s WHERE id = %s
        """, (operador_id, viaje_id))
        conn.commit()
    finally:
        liberar_conexion(conn)

    # Cancelar jobs del operador anterior
    jobs = context.job_queue.get_jobs_by_name(f"foto_{viaje_id}")
    for job in jobs:
        job.schedule_removal()

    await query.edit_message_text(
        f"✅ Operador actualizado a {nuevo_op[1]}.\n"
        f"El nuevo operador debe mandar /inicio para reanudar el seguimiento de fotos."
    )

    # Notificar al nuevo operador
    if nuevo_op:
        await context.bot.send_message(
            chat_id=nuevo_op[0],
            text=f"Se te ha asignado un viaje. Manda /inicio cuando estés listo."
        )

async def recibir_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Router central para todos los mensajes de texto."""
    uid = update.effective_user.id

    # Prioridad 1: flujo de revertir activo
    if context.bot_data.get(f"revertir_paso_{uid}"):
        await recibir_revertir(update, context)
        return

    # Prioridad 2: flujo de cambiolink activo
    if context.bot_data.get(f"cambiolink_paso_{uid}"):
        await recibir_cambiolink(update, context)
        return

    # Prioridad 3: flujo de link del despachador activo
    if context.bot_data.get(f"esperando_link_{uid}"):
        await recibir_link(update, context)
        return


# ═══════════════════════════════════════════════════
# ARRANQUE
# ═══════════════════════════════════════════════════

async def recuperar_jobs_al_arrancar(context: ContextTypes.DEFAULT_TYPE):
    """
    Al arrancar el bot, busca viajes activos y reprograma sus solicitudes de foto.
    Esto evita que un reinicio de Railway pierda los jobs pendientes.
    """
    conn = obtener_conexion()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT v.id, v.folio, v.numero_ruta, u.telegram_id, v.temperatura
            FROM viajes v
            JOIN usuarios_bot u ON u.referencia_id = v.operador_id
            WHERE v.estatus = 'en_curso'
              AND v.estatus_bot = 'en_curso'
              AND u.rol = 'operador'
              AND v.temperatura IN ('Congelado', 'Refrigerado')
        """)
        viajes = cur.fetchall()
    finally:
        liberar_conexion(conn)

    for viaje in viajes:
        viaje_id, folio, numero_ruta, operador_telegram_id, _ = viaje

        jobs_existentes = context.job_queue.get_jobs_by_name(f"foto_{viaje_id}")
        if jobs_existentes:
            continue

        context.job_queue.run_once(
            solicitar_foto,
            when=timedelta(hours=5),
            data={
                "viaje_id": viaje_id,
                "operador_telegram_id": operador_telegram_id,
                "numero_ruta": numero_ruta,
                "folio": folio,
                "intento": 1
            },
            name=f"foto_{viaje_id}"
        )

    if viajes:
        print(f"Jobs recuperados para {len(viajes)} viaje(s) activo(s).")


def main():
    iniciar_pool()
    os.makedirs(CARPETA_FOTOS, exist_ok=True)

    app = ApplicationBuilder().token(TOKEN).build()

    # Comandos del operador
    app.add_handler(CommandHandler("inicio",   cmd_inicio))
    app.add_handler(CommandHandler("descanso", cmd_descanso))
    app.add_handler(CommandHandler("retome",   cmd_retome))
    app.add_handler(CommandHandler("fin",      cmd_fin))

    # Comandos del encargado
    app.add_handler(CommandHandler("cambiolink", cmd_cambiolink))
    app.add_handler(CommandHandler("revertir",   cmd_revertir))

    # Comando de ayuda (todos los roles)
    app.add_handler(CommandHandler("ayuda", cmd_ayuda))

    # Callbacks de botones inline
    app.add_handler(CallbackQueryHandler(callback_fin,      pattern="^fin_"))
    app.add_handler(CallbackQueryHandler(callback_revertir, pattern="^revertir_"))
    app.add_handler(CallbackQueryHandler(callback_cambio_operador, pattern="^cambiolink_(si|no)_operador$"))
    app.add_handler(CallbackQueryHandler(callback_nuevo_operador,  pattern="^nuevo_operador_"))


    # Recepción de foto del operador
    app.add_handler(MessageHandler(filters.PHOTO, recibir_foto))

    # Un solo handler de texto que enruta internamente
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_texto))

    # Job que revisa links próximos a caducar cada hora
    app.job_queue.run_repeating(verificar_links_proximos, interval=3600, first=60)

    # Job que recupera solicitudes de foto al arrancar
    app.job_queue.run_once(recuperar_jobs_al_arrancar, when=10)

    print("Bot corriendo...")
    app.run_polling()

if __name__ == "__main__":
    main()
