import pandas as pd
import psycopg2
import logging
import sys
from datetime import datetime
from pathlib import Path
────────────────────────────────────────
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from db.conexion import DB_CONFIG

# ─────────────────────────────────────────
# MAPEO DE COLUMNAS (Google Sheet → código)
# El Forms 2 ya tiene columnas normalizadas,
# pero se define igual por consistencia.
# ─────────────────────────────────────────
COLUMNAS = {
    "marca_temporal":                   "marca_temporal",
    "folio":                            "folio",
    "fecha_salida_real":                "fecha_salida_real",
    "hora_salida_real":                 "hora_salida_real",
    "fecha_llegada_real":               "fecha_llegada_real",
    "hora_llegada_real":                "hora_llegada_real",
    "litros_diesel":                    "litros_diesel",
    "litros_diesel_rem":                "litros_diesel_rem",
    "importe_diesel":                   "importe_diesel",
    "importe_diesel_rem":               "importe_diesel_rem",
    "importe_casetas":                  "importe_casetas",
    "paradas":                          "paradas",
    "seguimiento":                      "seguimiento",
    "estadias":                         "estadias",
    "anticipos":                        "anticipos",
    "limpieza":                         "limpieza",
    "incidencias":                      "incidencias",
    "fitosanitaria":                    "fitosanitaria",
    "maniobras":                        "maniobras",
    "casetas_pagadas_operador":         "casetas_pagadas_operador",
    "otros_montos_pagados_operador":    "otros_montos_pagados_operador",
}

# ─────────────────────────────────────────
# LOGS
# ─────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)

log_file = f"logs/forms2_{datetime.now().strftime('%Y%m')}.log"

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

def parse_int(x):
    try:
        return int(x)
    except Exception:
        return None

# ─────────────────────────────────────────
# LÓGICA PRINCIPAL
# ─────────────────────────────────────────
def main():
    log.info("=" * 55)
    log.info("INICIO — bajar_forms2")

    # ── 1. Descargar Google Sheet ──────────────────────────
    log.info("Descargando datos del Forms 2...")
    try:
        df = pd.read_csv(FORMS2_URL, dtype={"folio": str})
    except Exception as e:
        log.error(f"No se pudo descargar el sheet: {e}")
        sys.exit(1)

    df.columns = df.columns.str.strip().str.replace("\n", "", regex=False)
    df = df.rename(columns=COLUMNAS)

    # Validar que todas las columnas esperadas estén presentes
    esperadas = set(COLUMNAS.values()) - {"marca_temporal"}
    faltantes = esperadas - set(df.columns)
    if faltantes:
        log.error(f"⚠️  Columnas esperadas no encontradas en el sheet: {faltantes}")
        log.error("Revisa si el Forms cambió el texto de alguna pregunta y actualiza el diccionario COLUMNAS.")
        sys.exit(1)

    if df.empty:
        log.info("El sheet está vacío. Sin registros que procesar.")
        log.info("FIN — bajar_forms2")
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
    actualizados  = 0
    ya_completos  = 0
    sin_folio     = 0
    con_error     = 0

    for idx, row in df.iterrows():
        try:
            folio = str(row["folio"]).strip()

            if not folio or folio == "nan":
                sin_folio += 1
                log.warning(f"Fila {idx} sin folio. Se omite.")
                continue

            # ── Verificar que el folio existe ──────────────
            cursor.execute("""
                SELECT id, estatus FROM viajes WHERE folio = %s
            """, (folio,))

            result = cursor.fetchone()

            if not result:
                log.warning(f"⚠️  Folio no encontrado en BD: {folio}")
                con_error += 1
                continue

            viaje_id, estatus_actual = result

            # ── Evitar sobreescribir viajes ya completados ─
            if estatus_actual == "completado":
                ya_completos += 1
                log.debug(f"Folio {folio} ya estaba completado. Se omite.")
                continue

            # ── Parsear campos ─────────────────────────────
            fecha_salida_real  = parse_fecha(row["fecha_salida_real"])
            hora_salida_real   = parse_hora(row["hora_salida_real"])
            fecha_llegada_real = parse_fecha(row["fecha_llegada_real"])
            hora_llegada_real  = parse_hora(row["hora_llegada_real"])

            litros_diesel      = parse_float(row["litros_diesel"])
            litros_diesel_rem  = parse_float(row["litros_diesel_rem"])
            importe_diesel     = parse_float(row["importe_diesel"])
            importe_diesel_rem = parse_float(row["importe_diesel_rem"])
            importe_casetas    = parse_float(row["importe_casetas"])

            paradas            = parse_int(row["paradas"])
            conteo_ex_vel      = None
            vel_max            = None

            seguimiento        = row["seguimiento"]
            incidencias        = row["incidencias"]

            estadias           = parse_float(row["estadias"])
            anticipos          = parse_float(row["anticipos"])
            limpieza           = row["limpieza"]
            fitosanitaria      = parse_float(row["fitosanitaria"])
            maniobras          = parse_float(row["maniobras"])
            casetas_pagadas_operador        = parse_float(row["casetas_pagadas_operador"])
            otros_montos_pagados_operador   = parse_float(row["otros_montos_pagados_operador"])

            # ── Actualizar ─────────────────────────────────
            cursor.execute("""
                UPDATE viajes
                SET
                    fecha_salida_real              = %s,
                    hora_salida_real               = %s,
                    fecha_llegada_real             = %s,
                    hora_llegada_real              = %s,
                    litros_diesel                  = %s,
                    litros_diesel_rem              = %s,
                    importe_diesel                 = %s,
                    importe_diesel_rem             = %s,
                    importe_casetas                = %s,
                    paradas                        = %s,
                    conteo_ex_vel                  = %s,
                    vel_max                        = %s,
                    seguimiento                    = %s,
                    estadias                       = %s,
                    anticipos                      = %s,
                    limpieza                       = %s,
                    incidencias                    = %s,
                    fitosanitaria                  = %s,
                    maniobras                      = %s,
                    casetas_pagadas_operador       = %s,
                    otros_montos_pagados_operador  = %s,
                    estatus                        = 'completado'
                WHERE folio = %s
            """, (
                fecha_salida_real, hora_salida_real,
                fecha_llegada_real, hora_llegada_real,
                litros_diesel, litros_diesel_rem,
                importe_diesel, importe_diesel_rem,
                importe_casetas,
                paradas, conteo_ex_vel, vel_max,
                seguimiento,
                estadias, anticipos, limpieza,
                incidencias, fitosanitaria,
                maniobras, casetas_pagadas_operador,
                otros_montos_pagados_operador,
                folio
            ))

            conn.commit()
            actualizados += 1
            log.info(f"✅ Actualizado — folio {folio} → estatus: completado")

        except Exception as e:
            conn.rollback()
            con_error += 1
            log.error(f"❌ Error en fila {idx} (folio={row.get('folio', '?')}): {e}")

    # ── 4. Cerrar conexión ─────────────────────────────────
    cursor.close()
    conn.close()
    log.info("Conexión cerrada.")

    # ── 5. Resumen ─────────────────────────────────────────
    log.info("-" * 55)
    log.info(f"RESUMEN → Actualizados: {actualizados} | Ya completos: {ya_completos} | Sin folio: {sin_folio} | Errores: {con_error}")
    log.info("FIN — bajar_forms2")
    log.info("=" * 55)


if __name__ == "__main__":
    main()
