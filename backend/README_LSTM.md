# Integración LSTM many-to-one

Se agregó una LSTM many-to-one dentro de `backend/model.py`.

## Qué hace

- Usa los últimos 5 partidos de cada equipo.
- Construye una secuencia temporal: local + visitante + diferencia.
- Entrena una LSTM con TensorFlow al iniciar el backend.
- Combina la predicción final así:
  - 40% clasificadores clásicos: HistGradientBoosting + RandomForest
  - 35% Poisson
  - 25% LSTM many-to-one

## Cómo verificar que está integrada

Al correr el backend debe aparecer en consola algo parecido a:

```txt
LSTM many-to-one entrenada | Accuracy: ... | F1: ...
```

En la respuesta de `/prediccion_debate`, dentro de `datos_matematicos`, deben aparecer estos campos:

```json
"lstm_integrado": true,
"lstm_accuracy": 0.xxx,
"lstm_f1": 0.xxx
```

Si TensorFlow no está instalado o falla, el backend no se cae. Verás:

```txt
LSTM desactivada: ...
```

En ese caso instala dependencias nuevamente:

```powershell
cd backend
.\venv\Scripts\activate
python -m pip install -r requirements.txt
```

## Ejecutar backend

```powershell
cd backend
.\venv\Scripts\activate
python -m uvicorn app:app --reload --port 8001
```
