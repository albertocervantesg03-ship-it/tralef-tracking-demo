import psycopg2
from psycopg2 import pool

# Cambia solo DB_PASSWORD por tu contraseña real
DB_CONFIG = {
    "host":     "thomas.proxy.rlwy.net",
    "database": "railway",
    "user":     "postgres",
    "password": "RnsXfbGotAcFeREpgHwbnrArAVAPDacK",
    "port":     19376
}
# Pool de conexiones: mínimo 1, máximo 10 simultáneas
_pool = None

def iniciar_pool():
    """Llama esto una sola vez al arrancar el bot o la API."""
    global _pool
    _pool = psycopg2.pool.ThreadedConnectionPool(
        minconn=1,
        maxconn=10,
        **DB_CONFIG
    )

def obtener_conexion():
    """Pide una conexión del pool."""
    if _pool is None:
        raise Exception("El pool no ha sido iniciado. Llama iniciar_pool() primero.")
    return _pool.getconn()

def liberar_conexion(conn):
    """Devuelve la conexión al pool cuando terminas de usarla."""
    if _pool is not None:
        _pool.putconn(conn)

def cerrar_pool():
    """Llama esto al apagar el sistema."""
    if _pool is not None:
        _pool.closeall()
