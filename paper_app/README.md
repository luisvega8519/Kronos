# Panel de simulación histórica MES

Desde la raíz de Kronos, con Python 3.10+:

```sh
pip install -r requirements.txt
python -m paper_app.server
```

Abre http://127.0.0.1:8080. Carga tu CSV y selecciona generar predicciones con
Kronos o importar `forecast_close` generado sin información futura. El panel
compara las dos estrategias, muestra saldo cerrado, operaciones y registro,
y permite descargar JSON o detener el proceso. Datos y ejecución se guardan
localmente en `paper-state`; conserva esa carpeta para mantener el historial.

No hay datos MES incluidos. Para generar predicciones requiere 400 velas
consecutivas del mismo contrato más las velas de evaluación. Un mínimo de filas
no garantiza continuidad ni representatividad. Revisa futures_lab/README.md
para formato, causalidad, calendarios y límites del simulador.

Una sola prueba activa por servidor. Las pruebas completadas con CSV y ajustes
idénticos se reutilizan; no se duplican. Al reiniciar, pruebas incompletas quedan
marcadas como interrumpidas y se pueden volver a iniciar explícitamente. No se
reanuda un proceso a mitad de una operación. Ejecutar una sola instancia del
servidor por carpeta de estado. Detener cancela la prueba, no cierra órdenes:
esta aplicación nunca envía órdenes.

Para VPS: configurar KRONOS_PANEL_TOKEN con una clave aleatoria de al menos
24 caracteres, persistir paper-state en un volumen y usar HTTPS en un proxy
inverso. El servidor rechaza acceso externo sin token. No poner claves en URLs,
repositorios o archivos públicos. El panel mantiene el token solo en memoria
de la página. Este cambio no instala ni publica servicios en tu VPS.

Esto es un panel para backtests, no paper trading en tiempo real ni un bot
continuo. Faltan proveedor de datos, integración en vivo y validación de
rentabilidad. Costes predeterminados ilustrativos. Caída calculada sobre saldo
cerrado, no pérdidas flotantes. Una prueba técnica no demuestra ventaja.
