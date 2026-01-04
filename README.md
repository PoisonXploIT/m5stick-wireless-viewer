# Visual extendida para M5Stick-Plus-2
Este repositorio proporciona una solución sencilla pero potente para visualizar en una pantalla grande (PC o móvil) los datos mostrados por el dispositivo M5Stick Plus 2 usando cualquier firmware en este caso el potente Evil-M5Project.

<img width="618" height="518" alt="image" src="https://github.com/user-attachments/assets/1b4b76dc-c6fa-468a-b3b6-1a650f656cea" />

El M5Stick Plus 2 cuenta con una pantalla muy reducida, lo que dificulta la lectura de datos escaneados, menús o actividad del firmware en tiempo real.

Además, el firmware no incluye tarjeta SD ni exportación automática de datos desde su interfaz web.

# Solución Propuesta

##Visualización remota vía USB:
Captura de la salida serie del dispositivo y visualización en pantalla grande mediante Flask + PySerial.
#Exportación de datos:
Opción adicional para guardar automáticamente lo mostrado en HTML.
Futuro: conversión a CSV para análisis en Power BI o Excel.

## Requisitos
Python 3.10+
Flask
PySerial

## Instalación:

pip install flask pyserial
Conexión del M5Stick
Conecta tu M5Stick Plus 2 por USB al PC.
Abre el Administrador de dispositivos y localiza el puerto COM asignado (por ejemplo: COM5).

## Visualización en pantalla grande
## Guardado automático de HTML (opcional)
## Uso
Ejecuta los scripts
http://localhost:5000 o la IP de tu dispositivo 
(Opcional) Ejecuta auto_save_html.py en una terminal aparte si quieres guardar capturas periódicas.

## Creditos

Proyecto basado en una necesidad real para visualizar y analizar la información del Evil-M5Project en M5Stick Plus 2.

"Ver mejor es entender mejor. Exportar es analizar."

## LICENCIA

Este proyecto está licenciado bajo la Licencia MIT.
