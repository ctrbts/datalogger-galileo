# --- 0. IMPORTACIONES ---
import serial
import serial.tools.list_ports
import time
import random
import csv
import os
import re
import sys
import json
import webbrowser
from threading import Timer
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request


# --- 1. CONFIGURACIÓN E INICIALIZACIÓN ---


def resource_path(relative_path):
    """Obtiene la ruta absoluta al recurso, funciona en desarrollo y para PyInstaller"""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


app = Flask(__name__, template_folder=resource_path("templates"))


# --- LOGICA DE RUTAS PORTABLES ---

# Detectar dónde está corriendo la aplicación para guardar los datos
if getattr(sys, "frozen", False):
    # si es .exe (Windows) usar Documents/Galileo_Logs
    ruta_ejecutable = os.path.join(os.path.expanduser("~"), "Documents", "Galileo_Logs")
else:
    # si es .py (Linux) usar la carpeta del script
    ruta_ejecutable = os.path.dirname(os.path.abspath(__file__))

# Definimos las rutas relativas al ejecutable/script
CARPETA_HISTORIAL = os.path.join(ruta_ejecutable, "historial_lecturas")
ARCHIVO_CONFIG = os.path.join(ruta_ejecutable, "config.json")

# Crear carpeta si no existe
if not os.path.exists(CARPETA_HISTORIAL):
    os.makedirs(CARPETA_HISTORIAL)

# Configuración por defecto
DEFAULT_CONFIG = {"puerto": "COM3", "velocidad": 9600, "simulacion": False}


# --- GESTIÓN DE CONFIGURACIÓN ---


def cargar_config():
    if not os.path.exists(ARCHIVO_CONFIG):
        return DEFAULT_CONFIG
    try:
        with open(ARCHIVO_CONFIG, "r") as f:
            return json.load(f)
    except:
        return DEFAULT_CONFIG


def guardar_config_json(nueva_config):
    try:
        with open(ARCHIVO_CONFIG, "w") as f:
            json.dump(nueva_config, f)
        return True
    except:
        return False


# Base de datos de límites de temperatura y humedad
EQUIPOS = {
    "HELADERA": {
        "temp": {
            "alerta": [3, 7],
            "accion": [2, 8],
        },
        "hum": None,
    },
    "FREEZER": {
        "temp": {
            "alerta": [None, -17],
            "accion": [None, -15],
        },
        "hum": None,
    },
    "ESTUFA 30-35": {
        "temp": {
            "alerta": [31.5, 33.5],
            "accion": [30, 35],
        },
        "hum": None,
    },
    "ESTUFA 20-25": {
        "temp": {
            "alerta": [21.5, 23.5],
            "accion": [20, 25],
        },
        "hum": None,
    },
    "AREAS CALIFICADAS": {
        "temp": {
            "alerta": [17, 23],
            "accion": [15, 25],
        },
        "hum": {
            "alerta": [None, 62],
            "accion": [None, 65],
        },
    },
    "AREAS NO CALIFICADAS": {
        "temp": {
            "alerta": [17, 23],
            "accion": [15, 25],
        },
        "hum": {
            "alerta": [None, 67],
            "accion": [None, 70],
        },
    },
}


# --- 2. DRIVER DEL SENSOR ---


def bcd_to_int(b):
    return ((b >> 4) * 10) + (b & 0x0F)


def parse_header_fecha_intervalo(buf):
    """
    Decodifica la cabecera del sensor (64 bytes) para obtener la fecha base y el intervalo de registro.

    Formato esperado:
    - Bytes 0-1: 0xD1 0x1C (Magic Bytes)
    - Byte 14: Año (BCD) -> 2000 + valor
    - Byte 15: Mes (BCD)
    - Byte 16: Día (BCD)
    - Byte 17: Hora (BCD)
    - Byte 18: Minuto (BCD)
    - Byte 19: Segundo (BCD)
    - Byte 20: Intervalo de grabación en minutos

    Retorna:
        datetime: Fecha de inicio de la grabación.
        int: Intervalo en minutos entre muestras.
    """
    data = bytearray(buf)
    header = None
    if len(data) >= 28 and data[0] == 0xD1 and data[1] == 0x1C:
        header = data[:28]
    else:
        for i in range(len(data) - 27):
            if data[i] == 0xD1 and data[i + 1] == 0x1C:
                header = data[i : i + 28]
                break

    if header is None:
        raise ValueError("Cabecera corrupta o no encontrada (Falta D1 1C)")

    year = 2000 + bcd_to_int(header[14])
    month = bcd_to_int(header[15])
    day = bcd_to_int(header[16])
    hour = bcd_to_int(header[17])
    minute = bcd_to_int(header[18])
    second = bcd_to_int(header[19])
    intervalo_min = header[20]
    return datetime(year, month, day, hour, minute, second), intervalo_min


def parse_samples(payload, offset=0):
    """
    Parsea un bloque de muestras de temperatura y humedad.
    Cada muestra ocupa 4 bytes:
    - Byte 0-1: Temperatura Raw (Big Endian). Temp (°C) = Raw / 10.0
    - Byte 2-3: Humedad Raw (Big Endian). Hum (%rH) = Raw / 10.0

    0x0000 o 0xFFFF indican fin de datos o espacio vacío.
    """
    muestras = []
    i = offset
    while i + 4 <= len(payload):
        t_raw = (payload[i] << 8) | payload[i + 1]
        h_raw = (payload[i + 2] << 8) | payload[i + 3]
        if t_raw in (0x0000, 0xFFFF) and h_raw in (0x0000, 0xFFFF):
            break
        temp_c = t_raw / 10.0
        hum_rh = h_raw / 10.0
        muestras.append((temp_c, hum_rh))
        i += 4
    return muestras


def leer_bloque(ser, idx):
    cmd = bytes([0xD3, 0xDA, idx, 0x00, 0x00])
    ser.write(cmd)
    ser.flush()
    time.sleep(0.3)
    return ser.read(128)


def leer_sensor_real(puerto, velocidad):
    """
    Establece comunicación con el datalogger Galileo THD 32000 y descarga la memoria completa.

    Flujo de comunicación:
    1. Handshake: Enviar 0x5C, recibir ACK (16 bytes).
    2. Header Request: Enviar 0xAD 0xDA, recibir Header (64 bytes).
    3. Lectura de Bloques:
       - Iterar desde bloque 0 hasta 255.
       - Enviar comando 0xD3 0xDA <index_bloque> 0x00 0x00.
       - Recibir bloque de datos (128 bytes).
       - Parsear muestras del bloque.

    Retorna:
        List[Dict]: Lista de muestras con fecha, temperatura y humedad.
    """
    datos_procesados = []
    try:
        ser = serial.Serial(puerto, velocidad, timeout=1.0)
        ser.setDTR(True)
        ser.setRTS(True)
        time.sleep(0.2)

        ser.write(b"\x5c")
        ser.flush()
        time.sleep(0.1)
        ser.read(16)  # Handshake

        ser.write(b"\xad\xda")
        ser.flush()
        time.sleep(0.3)  # Header
        cabecera = ser.read(64)

        try:
            dt_base, intervalo = parse_header_fecha_intervalo(cabecera)
        except Exception as e:
            ser.close()
            print(f"Error parseando fecha: {e}")
            return []

        idx_bloque = 0
        sample_index = 0
        while idx_bloque < 255:
            bloque = leer_bloque(ser, idx_bloque)
            if not bloque:
                break
            muestras_raw = parse_samples(bloque)
            if not muestras_raw:
                break
            for t, h in muestras_raw:
                fecha_muestra = dt_base + timedelta(minutes=intervalo * sample_index)
                datos_procesados.append(
                    {
                        "fecha": fecha_muestra.strftime("%Y-%m-%d %H:%M:%S"),
                        "temp": round(t, 2),
                        "hum": round(h, 2),
                    }
                )
                sample_index += 1
            idx_bloque += 1

        ser.close()
        return datos_procesados
    except Exception as e:
        print(f"Error conexión ({puerto}): {e}")
        return []


# --- 3. LÓGICA AUXILIAR Y ESTADÍSTICAS ---


def limpiar_nombre(nombre):
    """Elimina caracteres prohibidos en nombres de archivo de Windows/Linux"""
    return re.sub(r'[\\/*?:"<>|]', "", nombre).strip()


def guardar_csv_historico(datos, nombre_equipo, tag_usuario=""):
    if not datos:
        return None

    timestamp = datetime.now().strftime("%Y-%m-%d__%H-%M-%S")

    # Construcción del nombre: FECHA__HORA__TIPO[__TAG].csv
    nombre_base = f"{timestamp}__{nombre_equipo}"
    if tag_usuario:
        tag_limpio = limpiar_nombre(tag_usuario)
        if tag_limpio:
            nombre_base += f"__{tag_limpio}"

    nombre_archivo = f"{nombre_base}.csv"
    ruta_completa = os.path.join(CARPETA_HISTORIAL, nombre_archivo)

    try:
        with open(ruta_completa, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=";")
            # Agregamos metadatos en la cabecera del CSV para referencia futura
            writer.writerow(["#", "Equipo:", nombre_equipo, "Tag:", tag_usuario])
            writer.writerow(["Fecha", "Temperatura", "Humedad"])
            for d in datos:
                writer.writerow([d["fecha"], d["temp"], d["hum"]])
        return nombre_archivo
    except Exception as e:
        print(f"Error archivo: {e}")
        return None


"""Generamos datos simulados para un entorno de prueba, para simular el comportamiento del sensor"""


def generar_datos_simulados(equipo):
    datos = []
    base_temp = 20
    if "HELADERA" in equipo:
        base_temp = 5
    elif "FREEZER" in equipo:
        base_temp = -18
    elif "30-35" in equipo:
        base_temp = 32.5
    fecha = datetime.now() - timedelta(hours=4)  # Simular 4 horas atrás

    # Generar 100 muestras (aprox una cada 15 min son 24hs, aqui hacemos menos para demo)
    for i in range(100):
        ruido = random.uniform(-1.0, 1.0)
        temp = base_temp + ruido
        hum = 60 + random.uniform(-5, 10)
        datos.append(
            {
                "fecha": fecha.strftime("%Y-%m-%d %H:%M:%S"),
                "temp": round(temp, 2),
                "hum": round(hum, 2),
            }
        )
        fecha += timedelta(minutes=15)
    time.sleep(1.0)
    return datos


def calcular_resumen(datos):
    """Calcula estadísticas y metadatos de la misión"""
    if not datos:
        return {}

    temps = [d["temp"] for d in datos]
    hums = [d["hum"] for d in datos]

    return {
        "inicio": datos[0]["fecha"],
        "fin": datos[-1]["fecha"],
        "muestras": len(datos),
        "temp_max": max(temps),
        "temp_min": min(temps),
        "temp_prom": round(sum(temps) / len(temps), 2),
        "hum_max": max(hums),
        "hum_min": min(hums),
        "hum_prom": round(sum(hums) / len(hums), 2),
        "unidad_temp": "°C",
        "unidad_hum": "%rH",
    }


# --- 4. RUTAS WEB (API para el frontend) ---


# Ruta principal
@app.route("/")
def index():
    return render_template("index.html", equipos=EQUIPOS.keys())


# Rutas de Configuración
@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify(cargar_config())


# Ruta de guardado de configuración
@app.route("/api/config", methods=["POST"])
def save_config():
    data = request.json
    if guardar_config_json(data):
        return jsonify({"status": "ok", "msg": "Configuración guardada"})
    return jsonify({"status": "error", "msg": "No se pudo escribir el archivo"}), 500


# Ruta de listado de puertos
@app.route("/api/ports", methods=["GET"])
def list_ports():
    ports = serial.tools.list_ports.comports()
    result = []
    for p in ports:
        result.append({"device": p.device, "description": p.description})
    return jsonify(result)


# Rutas de Escaneo
@app.route("/api/scan", methods=["POST"])
def scan_sensor():
    data = request.json
    equipo = data.get("equipo", "GENERICO")
    tag = data.get("tag", "")  # <--- RECIBIMOS EL TAG

    config = cargar_config()

    # ... (Lógica de lectura igual que antes) ...
    if config["simulacion"]:
        muestras = generar_datos_simulados(equipo)
        origen_txt = "MODO SIMULACIÓN"
    else:
        muestras = leer_sensor_real(config["puerto"], config["velocidad"])
        origen_txt = f"Sensor ({config['puerto']})"
        if not muestras:
            return jsonify(
                {
                    "mensaje": f"Error: No se detectó sensor en {config['puerto']}.",
                    "datos": [],
                    "archivo": None,
                }
            )

    # Pasamos el tag a la función de guardado
    archivo_guardado = guardar_csv_historico(muestras, equipo, tag)
    resumen = calcular_resumen(muestras)

    msg = f"Lectura exitosa: {origen_txt}. {len(muestras)} registros."
    if archivo_guardado:
        msg += " Guardado."

    return jsonify(
        {
            "mensaje": msg,
            "datos": muestras,
            "archivo": archivo_guardado,
            "resumen": resumen,
        }
    )


# Rutas de Historial
@app.route("/api/history/list")
def list_history():
    archivos = []
    if os.path.exists(CARPETA_HISTORIAL):
        lista = sorted(
            os.listdir(CARPETA_HISTORIAL),
            key=lambda x: os.path.getmtime(os.path.join(CARPETA_HISTORIAL, x)),
            reverse=True,
        )
        for f in lista:
            if f.endswith(".csv"):
                archivos.append(f)
    return jsonify(archivos)


# Ruta de carga de historial
@app.route("/api/history/load/<filename>")
def load_history(filename):
    ruta = os.path.join(CARPETA_HISTORIAL, filename)
    datos = []
    try:
        # Lógica para parsear el nombre nuevo (con 4 partes) o viejo (con 3)
        # Formato: FECHA__HORA__EQUIPO__TAG.csv
        partes = filename.replace(".csv", "").split("__")

        equipo_detectado = "HELADERA"  # Default
        tag_detectado = ""

        if len(partes) >= 3:
            equipo_detectado = partes[2]
        if len(partes) >= 4:
            tag_detectado = partes[3]

        with open(ruta, mode="r", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter=";")
            filas = list(reader)

            # Detectar si tiene cabecera de metadatos (inicia con #)
            inicio_datos = 0
            if filas and filas[0][0].startswith("#"):
                inicio_datos = 2  # Saltamos metadatos y headers
            else:
                inicio_datos = 1  # Solo saltamos headers (formato viejo)

            # Leer datos reales
            for i in range(inicio_datos, len(filas)):
                row = filas[i]
                if len(row) >= 3:  # Asegurar que la fila es válida
                    datos.append(
                        {"fecha": row[0], "temp": float(row[1]), "hum": float(row[2])}
                    )

        resumen = calcular_resumen(datos)

        return jsonify(
            {
                "datos": datos,
                "equipo": equipo_detectado,
                "tag": tag_detectado,  # <--- DEVOLVEMOS EL TAG
                "resumen": resumen,
                "mensaje": f"Cargado: {filename}",
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Ruta de limites
@app.route("/api/limits/<equipo>")
def get_limits(equipo):
    return jsonify(EQUIPOS.get(equipo, {}))


# Función para abrir el navegador
def open_browser():
    webbrowser.open_new("http://127.0.0.1:5000")


if __name__ == "__main__":
    # Solo abrimos navegador si NO estamos en modo debug
    # (Para evitar que se abra 2 veces si el reloader está activo)
    if not os.environ.get("WERKZEUG_RUN_MAIN"):
        Timer(1, open_browser).start()

    app.run(port=5000, debug=True)
