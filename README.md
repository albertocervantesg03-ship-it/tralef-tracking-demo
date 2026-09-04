# Sistema de Seguimiento de Flota en Tiempo Real

Sistema de tracking logístico para una empresa de transporte refrigerado: los operadores reportan el estatus de sus viajes por Telegram, y los clientes dan seguimiento en tiempo real desde una página web con ubicación GPS y fotos de la refrigeración del embarque.

> Repositorio de portafolio derivado de un sistema en producción. Se removieron credenciales, datos reales y el nombre de la empresa.

## Stack
Python · FastAPI · PostgreSQL · Telegram Bot API · Railway

## Cómo funciona
- **Bot de Telegram** — los operadores reportan estatus desde su celular
- **API (FastAPI)** — conecta el bot, la base de datos y la web de clientes
- **PostgreSQL** — base de datos normalizada (operadores, unidades, clientes, viajes)
- **Pipelines** — sincronizan datos capturados en Google Forms hacia la base de datos
- **Página web** — vista pública para que el cliente siga su envío en tiempo real

## Decisiones técnicas
- Reemplacé polling por un webhook (Google Apps Script → FastAPI) para eliminar latencia
- Normalicé catálogos (operadores, unidades, remolques, clientes) con auto-inserción por ID
- Diseñé un sistema de alertas de mantenimiento preventivo basado en kilometraje acumulado

## Correrlo localmente
```bash
git clone https://github.com/albertocervantesg03-ship-it/tralef-tracking-demo.git
cd tralef-tracking-demo
pip install -r requirements.txt
cp .env.example .env  # agrega tus propias credenciales
```

---
Alberto Cervantes — [GitHub](https://github.com/albertocervantesg03-ship-it)