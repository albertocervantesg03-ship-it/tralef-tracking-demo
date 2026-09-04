import pandas as pd
import psycopg2
import logging
import sys
from datetime import datetime
from pathlib import Path

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from db.conexion import DB_CONFIG

# ─────────────────────────────────────────
# MAPEO DE COLUMNAS (Google Sheet → código)
# Actualiza la clave izquierda si el Forms
# cambia el texto de alguna pregunta.
# ─────────────────────────────────────────
COLUMNAS = {
    "Marca temporal":                                   "timestamp",
    "Nombre del operador":                              "operador",
    "Número de la unidad (tractocamión)":               "eco",
    "Número de Remolque":                               "remolque",
    "Cliente":                                          "cliente",
    "Origen":                                           "origen",
    "Destino":                                          "destino",
    "Tipo de viaje":                                    "tipo_viaje",
    "Temperatura":                                      "temperatura",
    "Fecha de salida (programada)":                     "fecha_salida",
    "Hora de cita (programada)":                        "hora_salida",
    "Fecha de llegada (programada)":                    "fecha_llegada",
    "Hora de llegada (programada)":                     "hora_llegada",
    "Distancia en kilómetros (únicamente poner números)": "distancia",
    "Tarifa  (únicamente poner números)":               "tarifa",
    "Sueldo (únicamente poner números)":                "sueldo",
    "Número de ruta":                                   "numero_ruta",
    "Link Sky Guardian":                                "link_sky",
}

# ─────────────────────────────────────────
# LOGS
# ─────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)

log_file = f"logs/forms1_{datetime.now().strftime('%Y%m')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

log = logging.getLogger(__name__)

# ─────────────────────────────────────────
# FUNCIONES DE LIMPIEZA
# ─────────────────────────────────────────
def parse_fecha(x):
    try:
        return pd.to_datetime(x).date()
    except Exception:
        return None

def parse_hora(x):
    try:
        return pd.to_datetime(x).time()
    except Exception:
        return None

def parse_float(x):
    try:
        return float(x)
    except Exception:
        return None

def obtener_o_crear_id(cursor, tabla, columna, valor):
    """
    Busca el id de un registro en una tabla catálogo.
    Si no existe, lo inserta y devuelve el nuevo id.
    """
    valor = str(valor).strip()

    cursor.execute(f"SELECT id FROM {tabla} WHERE {columna} = %s", (valor,))
    result = cursor.fetchone()

    if result:
        return result[0], False  # id, es_nuevo

    cursor.execute(
        f"INSERT INTO {tabla} ({columna}) VALUES (%s) RETURNING id",
        (valor,)
    )
    nuevo_id = cursor.fetchone()[0]
    return nuevo_id, True  # id, es_nuevo

# ─────────────────────────────────────────
# LÓGICA PRINCIPAL
# ─────────────────────────────────────────
def main():
    log.info("=" * 55)
    log.info("INICIO — bajar_forms1")

    # ── 1. Descargar Google Sheet ──────────────────────────
    log.info("Descargando datos del Forms 1...")
    try:
        df = pd.read_csv(FORMS1_URL)
    except Exception as e:
        log.error(f"No se pudo descargar el sheet: {e}")
        sys.exit(1)

    df.columns = df.columns.str.strip().str.replace("\n", "", regex=False)
    df = df.rename(columns=COLUMNAS)

    # Validar que todas las columnas esperadas estén presentes
    esperadas = set(COLUMNAS.values()) - {"timestamp"}
    faltantes = esperadas - set(df.columns)
    if faltantes:
        log.error(f"⚠️  Columnas esperadas no encontradas en el sheet: {faltantes}")
        log.error("Revisa si el Forms cambió el texto de alguna pregunta y actualiza el diccionario COLUMNAS.")
        sys.exit(1)

    if df.empty:
        log.info("El sheet está vacío. Sin registros que procesar.")
        log.info("FIN — bajar_forms1")
        return

    log.info(f"Filas descargadas: {len(df)}")

    # ── 2. Conectar a PostgreSQL ───────────────────────────
    log.info("Conectando a PostgreSQL...")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
    except Exception as e:
        log.error(f"No se pudo conectar a la base de datos: {e}")
        sys.exit(1)

    # ── 3. Procesar filas ──────────────────────────────────
    insertados = 0
    duplicados = 0
    con_error  = 0

    for idx, row in df.iterrows():
        try:
            # ── Obtener o crear en catálogos ───────────────
            operador_id, op_nuevo  = obtener_o_crear_id(cursor, "operadores", "nombre", row["operador"])
            unidad_id,   un_nuevo  = obtener_o_crear_id(cursor, "unidades",   "eco",    row["eco"])
            remolque_id, rem_nuevo = obtener_o_crear_id(cursor, "remolques",  "numero", row["remolque"])
            cliente_id,  cl_nuevo  = obtener_o_crear_id(cursor, "clientes",   "nombre", row["cliente"])

            if op_nuevo:  log.info(f"🆕 Nuevo operador registrado: '{row['operador']}'")
            if un_nuevo:  log.info(f"🆕 Nueva unidad registrada: eco '{row['eco']}'")
            if rem_nuevo: log.info(f"🆕 Nuevo remolque registrado: '{row['remolque']}'")
            if cl_nuevo:  log.info(f"🆕 Nuevo cliente registrado: '{row['cliente']}'")

            origen            = row["origen"]
            destino           = row["destino"]
            tipo              = row["tipo_viaje"]
            temp              = row["temperatura"]
            numero_ruta = str(int(float(row["numero_ruta"]))) if pd.notna(row.get("numero_ruta")) else None
            link_sky = str(row["link_sky"]).strip() if pd.notna(row.get("link_sky")) else None

            fecha_salida  = parse_fecha(row["fecha_salida"])
            hora_salida   = parse_hora(row["hora_salida"])
            fecha_llegada = parse_fecha(row["fecha_llegada"])
            hora_llegada  = parse_hora(row["hora_llegada"])

            distancia = parse_float(row["distancia"])
            tarifa    = parse_float(row["tarifa"])
            sueldo    = parse_float(row["sueldo"])

            # ── Evitar duplicados ──────────────────────────
            cursor.execute("""
                SELECT COUNT(*) FROM viajes
                WHERE unidad_id   = %s
                  AND fecha_salida = %s
                  AND hora_salida  = %s
            """, (unidad_id, fecha_salida, hora_salida))

            if cursor.fetchone()[0] > 0:
                duplicados += 1
                log.debug(f"Fila {idx} duplicada (eco={row['eco']}, fecha={fecha_salida}). Se omite.")
                continue

            # ── Insertar viaje ─────────────────────────────
            cursor.execute("""
                INSERT INTO viajes (
                    operador_id, unidad_id, remolque_id, cliente_id,
                    origen, destino, tipo_viaje, temperatura,
                    fecha_salida, hora_salida,
                    fecha_llegada, hora_llegada,
                    distancia, tarifa, sueldo,
                    numero_ruta, link_sky, estatus, estatus_bot
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'en_curso','en_curso')
                RETURNING id;
            """, (
                operador_id, unidad_id, remolque_id, cliente_id,
                origen, destino, tipo, temp,
                fecha_salida, hora_salida,
                fecha_llegada, hora_llegada,
                distancia, tarifa, sueldo,
                numero_ruta, link_sky
            ))

            viaje_id = cursor.fetchone()[0]
            folio    = str(viaje_id).zfill(5)

            cursor.execute("UPDATE viajes SET folio = %s WHERE id = %s", (folio, viaje_id))
            conn.commit()

            insertados += 1
            log.info(f"✅ Insertado — folio {folio} | eco {row['eco']} | {origen} → {destino}")

        except Exception as e:
            conn.rollback()
            con_error += 1
            log.error(f"❌ Error en fila {idx}: {e}")

    # ── 4. Cerrar conexión ─────────────────────────────────
    cursor.close()
    conn.close()
    log.info("Conexión cerrada.")

    # ── 5. Resumen ─────────────────────────────────────────
    log.info("-" * 55)
    log.info(f"RESUMEN → Insertados: {insertados} | Duplicados: {duplicados} | Errores: {con_error}")
    log.info("FIN — bajar_forms1")
    log.info("=" * 55)


if __name__ == "__main__":
    main()
