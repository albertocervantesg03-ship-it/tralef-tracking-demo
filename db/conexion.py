import os
import psycopg2
from psycopg2 import pool
from dotenv import load_dotenv

load_dotenv()  # lee el archivo .env en desarrollo local

DB_CONFIG = {
    "host":     os.getenv("DB_HOST"),
    "database": os.getenv("DB_NAME"),
    "user":     os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "port":     os.getenv("DB_PORT", "5432")
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