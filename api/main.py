from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from datetime import datetime, timedelta
from fastapi.responses import FileResponse
from db.conexion import obtener_conexion, liberar_conexion, iniciar_pool

app = FastAPI()

@app.on_event("startup")
def startup():
    iniciar_pool()

# ─────────────────────────────────────────
# Rutas activas de un cliente
# ─────────────────────────────────────────

@app.get("/api/rutas/{token}")
def obtener_rutas(token: str):
    """
    Devuelve todas las rutas activas del cliente que corresponde a ese token.
    La página del cliente llama esto cada 60 segundos.
    """
    conn = obtener_conexion()
    try:
        cur = conn.cursor()

        # Verificar que el token existe y está activo
        cur.execute("""
            SELECT ac.cliente_id
            FROM accesos_cliente ac
            WHERE ac.token = %s AND ac.activo = TRUE
        """, (token,))
        resultado = cur.fetchone()

        if not resultado:
            raise HTTPException(status_code=404, detail="Token inválido")

        cliente_id = resultado[0]

        # Obtener rutas activas de ese cliente
        cur.execute("""
            SELECT
                v.id,
                v.numero_ruta,
                v.origen,
                v.destino,
                v.link_sky,
                v.link_expiracion,
                v.estatus_bot,
                v.temperatura
            FROM viajes v
            WHERE v.cliente_id = %s
              AND v.estatus = 'en_curso'
            ORDER BY v.fecha_salida_real DESC
        """, (cliente_id,))

        columnas = ["id", "numero_ruta", "origen", "destino",
                    "link_sky", "link_expiracion", "estatus_bot", "temperatura"]
        rutas = [dict(zip(columnas, fila)) for fila in cur.fetchall()]

        # Agregar la última foto a cada ruta
        for ruta in rutas:
            cur.execute("""
                SELECT telegram_file_id, fecha_hora
                FROM fotos_termo
                WHERE viaje_id = %s
                ORDER BY fecha_hora DESC
                LIMIT 1
            """, (ruta["id"],))
            foto = cur.fetchone()
            ruta["ultima_foto"] = {
                "file_id": foto[0],
                "fecha_hora": (foto[1] - timedelta(hours=6)).strftime("%d/%m/%Y %H:%M") if foto else None
            } if foto else None

            # Convertir link_expiracion a string para JSON
            if ruta["link_expiracion"]:
                ruta["link_expiracion"] = ruta["link_expiracion"].strftime("%d/%m/%Y %H:%M")

        return {"rutas": rutas}

    finally:
        liberar_conexion(conn)


# ─────────────────────────────────────────
# Actualizar link de Sky Guardian
# ─────────────────────────────────────────

@app.post("/api/rutas/{viaje_id}/link")
def actualizar_link(viaje_id: int, body: dict):
    """
    Recibe el nuevo link del despachador y calcula la expiración automáticamente.
    El bot llama esto cuando el despachador responde con el link nuevo.
    """
    nuevo_link = body.get("link")
    if not nuevo_link:
        raise HTTPException(status_code=400, detail="Falta el link")

    conn = obtener_conexion()
    try:
        cur = conn.cursor()
        expiracion = datetime.now() + timedelta(hours=24)
        cur.execute("""
            UPDATE viajes
            SET link_sky = %s,
                link_expiracion = %s
            WHERE id = %s
        """, (nuevo_link, expiracion, viaje_id))
        conn.commit()
        return {"ok": True, "expiracion": expiracion.strftime("%d/%m/%Y %H:%M")}
    finally:
        liberar_conexion(conn)


# ─────────────────────────────────────────
# Webhook Forms 1 — insertar viaje nuevo
# ─────────────────────────────────────────

@app.post("/api/forms1")
async def webhook_forms1():
    import subprocess
    resultado = subprocess.run(
        ["python", "-m", "pipelines.bajar_forms1"],
        capture_output=True, text=True
    )
    print("STDOUT:", resultado.stdout)
    print("STDERR:", resultado.stderr)
    print("RETURNCODE:", resultado.returncode)
    if resultado.returncode != 0:
        raise HTTPException(status_code=500, detail=resultado.stderr)
    return {"ok": True, "log": resultado.stdout}


# ─────────────────────────────────────────
# Webhook Forms 2 — actualizar viaje
# ─────────────────────────────────────────

@app.post("/api/forms2")
async def webhook_forms2():
    """
    Google Apps Script llama este endpoint cuando se envía el Forms 2.
    Corre bajar_forms2.py para actualizar el viaje en la BD.
    """
    import subprocess
    resultado = subprocess.run(
        ["python", "-m", "pipelines.bajar_forms2"],
        capture_output=True, text=True
    )
    if resultado.returncode != 0:
        raise HTTPException(status_code=500, detail=resultado.stderr)
    return {"ok": True, "log": resultado.stdout}


# ─────────────────────────────────────────
# Servir la página estática del cliente
# ─────────────────────────────────────────
@app.get("/seguimiento/{token}")
def pagina_cliente(token: str):
    return FileResponse("/app/static/index.html")

app.mount("/fotos", StaticFiles(directory="/app/fotos"), name="fotos")
app.mount("/", StaticFiles(directory="static", html=True), name="static")
