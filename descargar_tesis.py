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
MAX_PETICIONES = 4000
RETROCESO_INICIAL = 500    # en la primera ejecución, cuántos registros recientes tomar
PAUSA = 0.25

estadisticas = {}


def pedir(registro):
    """Devuelve el JSON de la tesis, o None si no existe. Registra el código HTTP."""
    req = urllib.request.Request(
        API.format(registro),
        headers={"User-Agent": "Mozilla/5.0 (monitor jurisprudencia)", "Accept": "application/json"},
    )
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
    while fallos < FALLOS_PARA_PARAR and sum(estadisticas.values()) < MAX_PETICIONES:
        tesis = pedir(registro)
        if tesis:
            encontradas.append({"registro": registro, "datos": tesis})
            ultimo, fallos = registro, 0
        else:
            fallos += 1
        registro += 1

    hoy = date.today().isoformat()
    archivo = os.path.join(CARPETA, f"tesis-{hoy}.json")
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
                   "codigos_http": estadisticas}, f, indent=1)
    print(nota, estadisticas)


if __name__ == "__main__":
    main()
