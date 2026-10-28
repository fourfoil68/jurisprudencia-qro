"""
Descarga las tesis nuevas del Semanario Judicial de la Federación (SJF 2.0)
consultando su API pública por número de registro digital, y las guarda en data/.

Lo ejecuta GitHub Actions cada semana. Solo usa la biblioteca estándar de Python.
Guarda la respuesta cruda de la API: el filtrado por materia y por Querétaro
se hace después, al generar el reporte.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date

API = "https://sjf2.scjn.gob.mx/services/sjftesismicroservice/api/public/tesis/{}"
CARPETA = "data"
ESTADO = os.path.join(CARPETA, "estado.json")
SEMILLA = 2030000          # registro de arranque si no hay estado previo
FALLOS_PARA_PARAR = 60     # registros vacíos seguidos que indican el final
MAX_PETICIONES = 7000
SONDEO_PASO = 20           # al toparse con un hueco, sondea cada 20 registros...
SONDEO_MAX = 10000         # ...hasta 10,000 registros adelante
RETROCESO_INICIAL = 500    # en la primera ejecución, cuántos registros recientes tomar
PAUSA = 0.25

estadisticas = {}
diagnostico = {}

# Cabeceras de un navegador real: algunos firewalls rechazan peticiones "de robot".
CABECERAS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-MX,es;q=0.9",
    "Referer": "https://sjf2.scjn.gob.mx/busqueda-principal-tesis",
    "Origin": "https://sjf2.scjn.gob.mx",
}


def diagnosticar():
    """Distingue un bloqueo por país/IP (todo da 403) de un bloqueo solo a la API."""
    for nombre, url in {
        "scjn_portada": "https://www.scjn.gob.mx/",
        "sjf2_portada": "https://sjf2.scjn.gob.mx/",
        "sjf2_api": API.format(2030000),
        "api_reciente_normal": API.format(2032808),
        "api_reciente_semanal": API.format(2032808) + "?isSemanal=true",
    }.items():
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=CABECERAS), timeout=30) as r:
                diagnostico[nombre] = r.status
        except urllib.error.HTTPError as e:
            diagnostico[nombre] = e.code
        except Exception as e:
            diagnostico[nombre] = f"error:{type(e).__name__}"
    print("Diagnóstico:", diagnostico)


VARIANTES = ["", "?isSemanal=true"]   # las tesis recientes solo salen en la vista "semanal"


def _pedir_url(url):
    req = urllib.request.Request(url, headers=CABECERAS)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            codigo = r.status
            cuerpo = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        codigo, cuerpo = e.code, ""
    except Exception as e:  # red caída, DNS, timeout
        codigo, cuerpo = f"error:{type(e).__name__}", ""
    estadisticas[str(codigo)] = estadisticas.get(str(codigo), 0) + 1
    time.sleep(PAUSA)
    if codigo != 200 or not cuerpo.strip():
        return None
    try:
        datos = json.loads(cuerpo)
    except json.JSONDecodeError:
        return None
    return datos or None


def pedir(registro):
    """Devuelve el JSON de la tesis (probando ambas vistas de la API), o None si no existe."""
    for v in VARIANTES:
        datos = _pedir_url(API.format(registro) + v)
        if datos:
            return datos
    return None


def existe_cerca(registro, ventana=5):
    return any(pedir(registro + i) for i in range(ventana))


def buscar_frontera():
    """Sin estado previo: avanza en saltos hasta dejar de encontrar registros."""
    actual = SEMILLA
    if not existe_cerca(actual):
        return None
    salto = 2000
    while salto >= 10:
        if existe_cerca(actual + salto):
            actual += salto
        else:
            salto //= 2
    return actual


def main():
    os.makedirs(CARPETA, exist_ok=True)
    diagnosticar()
    estado = {}
    if os.path.exists(ESTADO):
        with open(ESTADO, encoding="utf-8") as f:
            estado = json.load(f)

    if "ultimo_registro" in estado:
        inicio = estado["ultimo_registro"] + 1
    else:
        frontera = buscar_frontera()
        if frontera is None:
            escribir_log(ok=False, nota="No se pudo leer ningún registro: posible bloqueo del Semanario.")
            sys.exit(1)
        inicio = frontera - RETROCESO_INICIAL

    encontradas, fallos, registro, ultimo = [], 0, inicio, estado.get("ultimo_registro", inicio - 1)
    while sum(estadisticas.values()) < MAX_PETICIONES:
        if fallos >= FALLOS_PARA_PARAR:
            # La numeración puede tener saltos grandes: antes de rendirse,
            # sondea más adelante y salta directo al siguiente bloque con tesis.
            salto = next((registro + d for d in range(SONDEO_PASO, SONDEO_MAX + 1, SONDEO_PASO)
                          if pedir(registro + d)), None)
            if salto is None:
                break
            registro, fallos = salto - SONDEO_PASO + 1, 0
            continue
        tesis = pedir(registro)
        if tesis:
            encontradas.append({"registro": registro, "datos": tesis})
            ultimo, fallos = registro, 0
        else:
            fallos += 1
        registro += 1

    hoy = date.today().isoformat()
    if not encontradas:
        escribir_log(ok=True, nota=f"0 tesis nuevas; último registro {ultimo}. Se conservan los archivos anteriores.")
        return
    archivo = os.path.join(CARPETA, f"tesis-{hoy}.json")
    if os.path.exists(archivo):  # segunda ejecución el mismo día: acumula sin duplicar
        with open(archivo, encoding="utf-8") as f:
            previas = json.load(f)
        vistos = {t["registro"] for t in encontradas}
        encontradas = [t for t in previas if t["registro"] not in vistos] + encontradas
    with open(archivo, "w", encoding="utf-8") as f:
        json.dump(encontradas, f, ensure_ascii=False, indent=1)
    with open(ESTADO, "w", encoding="utf-8") as f:
        json.dump({"ultimo_registro": ultimo, "actualizado": hoy}, f, indent=1)
    with open(os.path.join(CARPETA, "ultimo.json"), "w", encoding="utf-8") as f:
        json.dump({"archivo": archivo, "fecha": hoy, "total": len(encontradas)}, f, indent=1)
    escribir_log(ok=bool(encontradas) or "ultimo_registro" in estado,
                 nota=f"{len(encontradas)} tesis nuevas; último registro {ultimo}")


def escribir_log(ok, nota):
    with open(os.path.join(CARPETA, "log.json"), "w", encoding="utf-8") as f:
        json.dump({"fecha": date.today().isoformat(), "ok": ok, "nota": nota,
                   "codigos_http": estadisticas, "diagnostico": diagnostico}, f, indent=1)
    print(nota, estadisticas)


if __name__ == "__main__":
    main()
