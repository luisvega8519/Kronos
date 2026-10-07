# Laboratorio MES / Kronos (solo investigación)

Implementa la hipótesis inicial: velas de 5 minutos, SMA50, ATR14 (promedio
simple de true range), stop de 1.5 ATR redondeado al tick, solo compras si
la predicción de cierre a 15 minutos supera el stop más los costes. Riesgo
presupuestado 0.5% del saldo; límite de pérdida realizada diaria 1.5%; máximo
10 contratos. No hay conexión al bróker, órdenes reales ni rentabilidad validada.

## Datos y ejecución

Desde la raíz del repositorio:

```sh
pip install -r requirements.txt
python -m futures_lab.forecast mes.csv --output mes-forecasts.csv --device cpu
python -m futures_lab.backtest mes-forecasts.csv --commission 1.25 --slippage-ticks 1
python -m unittest discover -s tests -p 'test_futures_lab.py' -v
```

CSV requerido:

```csv
timestamp,contract,expiry_date,open,high,low,close,volume
2026-01-05T14:35:00+00:00,MESH26,2026-03-20,6000,6002,5999,6001,1500
```

El ejemplo es ilustrativo y una fila no basta para generar señales. `timestamp`
es el CIERRE de la vela y debe incluir zona horaria. Precios sin ajustes de
rollover, contrato individual explícito, vencimiento real `YYYY-MM-DD`, volumen
real. Se requiere historial consecutivo de 400 velas por contrato para la
predicción predeterminada; 50..512 configurable. No se cruzan huecos o contratos.
No usar el CSV de acciones incluido en Kronos para simular MES. Confirmar el
vencimiento con el proveedor; no inferirlo solo del símbolo. `amount` es opcional:
si falta, Kronos usa su proxy volumen por promedio OHLC, no turnover real.

`forecast_close` se crea cronológicamente usando solo el historial hasta cada
cierre. El modelo y tokenizer están fijados a las revisiones utilizadas por las
pruebas upstream. Cinco muestras por defecto; semilla por fila. Las horas del
modelo están en UTC. Inferencia CPU puede ser lenta y los pesos deben descargarse.
Predicciones externas deben tener la misma causalidad; el simulador no puede
comprobar cómo se produjeron. Nunca introducir cierres reales futuros como forecast.

## Salidas comparadas

- `fixed`: objetivo 2R y stop inicial.
- `trailing`: activa tras avance 1R y sigue al máximo a distancia 1R.
- Ambas cierran después de tres velas y esperan el mismo horizonte antes de
  considerar otra entrada. Comparten señales y oportunidades; el tamaño puede
  divergir según el saldo y el límite diario de cada variante.

R se fija en la entrada. El trailing actualizado entra en vigor en la siguiente
vela: OHLC no revela el orden de los movimientos dentro de una vela. Si una vela
toca stop y objetivo, se asume stop primero. Gaps se ejecutan al open adverso;
comisiones por lado y deslizamiento en ambas ejecuciones. No se garantiza el
riesgo presupuestado ante gaps. Stops no se alejan y no se promedian pérdidas.

Entradas limitadas a señales entre 09:30 y 15:45 America/New_York. Sin entradas
en o después del vencimiento; salida antes de 16:00. Calendarios festivos y
sesiones abreviadas requieren datos filtrados del proveedor. No hay chequeo de
margen del bróker: necesario antes de cualquier despliegue posterior.

## Interpretación y limitaciones

Genera `fixed.json` y `trailing.json`: operaciones, señales, PnL neto,
porcentaje de aciertos, profit factor, promedio por operación y caída máxima
sobre SALDO CERRADO. Esta última no incluye pérdidas flotantes intratrade.
Los costes predeterminados son supuestos ilustrativos, no una tarifa verificada.
La entrada al siguiente open supone latencia cero; probar retrasos y costes
mayores antes de considerar resultados operables. No reproduce el motor de
órdenes de un bróker ni es paper trading en tiempo real.

Separar cronológicamente un periodo de ajuste y otro posterior intacto de
validación. Para cada corte, generar señales sobre la serie completa con solo
historial anterior y evaluar por separado las filas del periodo; reservar al
menos 50 velas de calentamiento sin forecast al inicio. No retocar parámetros
tras ver el periodo reservado. Repetir en distintos periodos de volatilidad y
con costes más altos. Sin datos MES ni inferencia ejecutada no hay evidencia
de ventaja; pruebas sintéticas validan lógica, no rentabilidad.
