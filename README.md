# Galileo THD 32000 Datalogger

Aplicación web para la descarga, visualización y exportación de datos del sensor datalogger **Galileo THD 32000** (Temperatura y Humedad).

Esta herramienta permite conectar el sensor vía puerto serie (USB), descargar el historial completo de grabaciones, visualizar gráficas interactivas y analizar estadísticas (máximos, mínimos, promedios).

## Características

- 📡 **Lectura directa** del sensor Galileo THD 32000.
- 📊 **Visualización gráfica** de temperatura y humedad.
- 📑 **Exportación automática** a CSV en la carpeta de documentos del usuario.
- ⚙️ **Configuración de puertos** y baudrate automática.
- 🏥 **Presets de límites** configurables para distintos tipos de equipos (Heladeras, Freezers, Estufas, etc.).
- 🐧 **Soporte Multiplataforma**: Compatible con Windows y Linux.

## Requisitos

- Python 3.8+
- Bibliotecas: Ver `requirements.txt`

### Instalación de dependencias

```bash
pip install -r requirements.txt
```

Las principales dependencias son:

- `flask`: Servidor web local para la interfaz.
- `pyserial`: Comunicación serial con el hardware.
- `pyinstaller`: Para generar binarios ejecutables.

## Ejecución en Desarrollo

Para ejecutar la aplicación desde el código fuente:

```bash
python app.py
```

Esto abrirá automáticamente una ventana del navegador en `http://127.0.0.1:5000`.

## Compilación (Build)

Para distribuir la aplicación sin necesidad de instalar Python en el equipo destino, se utiliza **PyInstaller**.

### Generar Ejecutable

El proyecto incluye un archivo de especificación `Galileo_Datalogger.spec` listo para usar.

**En Windows / Linux:**

```bash
pyinstaller Galileo_Datalogger.spec
```

El ejecutable resultante se encontrará en la carpeta `dist/Galileo_Datalogger`.

Aclaración sobre pyinstaller:

Pyinstaller es un herramienta que permite convertir un script de Python en un binario ejecutable, no hace compilación cruzada, para crea un .exe debe ejecutarlo en Windows.

## Licencia

Este proyecto se distribuye bajo la licencia **GNU General Public License v3.0 (GPLv3)**. Consulte el archivo `LICENSE` para más detalles.
