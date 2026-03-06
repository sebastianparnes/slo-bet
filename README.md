# 🇸🇮 Slovenian Football Betting Analyzer

API de análisis predictivo para apuestas de fútbol esloveno — PrvaLiga y 2.SNL.

## 🔍 Motor de Análisis

El sistema usa un modelo de scoring en 6 dimensiones (0–100 pts de confianza):

| Componente | Peso | Descripción |
|---|---|---|
| **Forma reciente** | 25 pts | Últimos 5 partidos con pesos temporales (más reciente = más peso) |
| **Head-to-Head** | 20 pts | Historial directo últimos 10 encuentros |
| **Posición en tabla** | 20 pts | Diferencia de puntos y ranking relativo |
| **Estadísticas de goles** | 20 pts | Modelo Poisson con xG (Expected Goals) |
| **Factor local** | 10 pts | Tasa de victoria en casa de la liga (PrvaLiga: 44%) |
| **Consistencia** | 5 pts | Penaliza equipos con resultados muy erráticos |

### Tipos de apuesta analizados
- **1X2** — Resultado final
- **Doble oportunidad** — 1X, X2, 12
- **Over/Under 2.5** — basado en modelo Poisson
- **BTTS** — Ambos equipos marcan

## 🚀 Setup Local

```bash
git clone https://github.com/TU_USER/slovenian-football-analyzer
cd slovenian-football-analyzer
pip install -r requirements.txt

# Opcional: configurar API key para datos reales
export API_FOOTBALL_KEY=tu_clave_aqui  # api-football.com (plan gratis: 100 req/día)

uvicorn app.main:app --reload
```

Abrí `http://localhost:8000` en el navegador.

**Sin API key:** el sistema corre en modo demo con datos simulados de equipos reales eslovenos.

## 🌐 Deploy en Render

1. Forkeá/subí este repo a GitHub
2. En [render.com](https://render.com), creá un nuevo **Web Service**
3. Conectá tu repo
4. Render detecta el `render.yaml` automáticamente
5. En **Environment Variables**, agregá:
   - `API_FOOTBALL_KEY` = tu clave de [api-football.com](https://www.api-football.com/)
6. Deploy 🚀

El disk persistente en Render guarda el historial de apuestas entre deploys.

## 📡 Endpoints API

```
GET /api/matches/upcoming?days=5          # Próximos partidos
GET /api/matches/analyzed-all?days=5      # Todos con análisis completo
GET /api/matches/{id}/analysis            # Análisis individual
GET /api/analysis/value-bets?days=5       # Solo las apuestas de mayor valor
GET /api/analysis/standings/{league}      # Tabla (PrvaLiga / 2SNL)

GET /api/history/                         # Historial de apuestas
GET /api/history/stats                    # Estadísticas y P&L
POST /api/history/                        # Registrar apuesta
PATCH /api/history/{id}/result            # Actualizar resultado
DELETE /api/history/{id}                  # Eliminar apuesta
```

Documentación interactiva: `http://localhost:8000/docs`

## 🏆 Ligas

- **PrvaLiga** — 1ª División Eslovena (`league_id: 218`)
- **2.SNL** — 2ª División Eslovena (`league_id: 219`)

## ⚙️ Variables de Entorno

| Variable | Descripción | Default |
|---|---|---|
| `API_FOOTBALL_KEY` | Clave de api-football.com | — (modo demo) |
| `DB_PATH` | Ruta de la base de datos SQLite | `data/betting_history.db` |
| `PORT` | Puerto (Render lo inyecta solo) | `8000` |

## ⚠️ Disclaimer

Este sistema es solo para análisis estadístico. Las apuestas deportivas implican riesgo. Jugá con responsabilidad.
