"""Proxy de alertas por coordenadas. Fuente: SMN, redistribuida por Mi Clima.

Mi Clima documenta el uso libre de su API citando ambas fuentes.
Una falla o una respuesta ambigua NUNCA se interpreta como estado verde.
"""
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json
import threading
import time

BASE = "https://miclima.com.ar"
CACHE_TTL = 130
HEALTH_TTL = 120
MAX_STALE_SECONDS = 3600
_LOCK = threading.RLock()
_HEALTH = {"at": 0.0, "value": None}
_POINTS = {}
_LEVELS = {"amarillo": "yellow", "naranja": "orange", "rojo": "red"}
_RANK = {"red": 3, "orange": 2, "yellow": 1, "unknown": 0}


def _get_json(url, limit=500000):
    request = Request(url, headers={
        "User-Agent": "AlertaEnTuZona/2.0 (datos de Mi Clima, origen SMN)",
        "Accept": "application/json"
    })
    with urlopen(request, timeout=12) as response:
        if response.status != 200:
            raise ValueError("El proveedor no respondió correctamente")
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("La respuesta del proveedor es demasiado grande")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("El proveedor no devolvió un objeto")
    return data


def _parsed(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OverflowError, TypeError):
        return None


def _time_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _source_health():
    now = time.monotonic()
    with _LOCK:
        if now - _HEALTH["at"] < HEALTH_TTL and _HEALTH["value"] is not None:
            return _HEALTH["value"]
    try:
        status = _get_json(BASE + "/api/estado", 1000000)
        smn = status.get("fuentes", {}).get("smn")
        if not isinstance(smn, dict):
            raise ValueError("Falta información de estado de SMN")
        feed = smn.get("feed")
        if not isinstance(feed, dict):
            raise ValueError("Falta estado del índice de alertas")
        updated = _parsed(smn.get("ultimo_refresco"))
        observed = _parsed(status.get("revisado"))
        now_dt = datetime.now(timezone.utc)
        if not (status.get("ok") is True
                and smn.get("ok") is True
                and feed.get("ok") is True
                and smn.get("sincronizacion_inicial_completa") is True
                and updated and observed
                and -120 <= (now_dt - updated).total_seconds() <= MAX_STALE_SECONDS
                and -120 <= (now_dt - observed).total_seconds() <= 900):
            raise ValueError("El SMN no está sincronizado o el registro está desactualizado")
        result = {"healthy": True, "source_updated": smn.get("ultimo_refresco")}
    except Exception as exc:
        print("Mi Clima/SMN: estado no verificable:", type(exc).__name__, str(exc)[:120], flush=True)
        result = {"healthy": False, "source_updated": None}
    with _LOCK:
        _HEALTH.update({"at": time.monotonic(), "value": result})
    return result


def _plain(value, max_chars=2000):
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:max_chars]


def _normalise(item, place):
    if not isinstance(item, dict):
        raise ValueError("Aviso inválido")
    if not item.get("evento") or not item.get("hasta"):
        raise ValueError("Aviso sin evento o vigencia")
    if _parsed(item.get("hasta")) is None:
        raise ValueError("Fecha de fin del aviso ilegible")
    color = _plain(item.get("color"), 30).lower()
    level = _LEVELS.get(color, "unknown")
    verified = item.get("color_verificado") is True
    return {
        "id": _plain(item.get("id"), 120),
        "event": _plain(item.get("evento"), 150),
        "headline": _plain(item.get("evento"), 150),
        "description": _plain(item.get("descripcion"), 1800),
        "instructions": _plain(item.get("instrucciones"), 1500),
        "severity": _plain(item.get("severidad"), 40),
        "level": level,
        "level_source": "Color confirmado" if verified else "Color orientativo según severidad CAP",
        "color_verified": verified,
        "issued": item.get("emitida"),
        "starts": item.get("desde"),
        "ends": item.get("hasta"),
        "area": _plain(item.get("zonas") or place, 250),
        "url": "https://www.smn.gob.ar/alertas",
        "horario_dudoso": item.get("horario_dudoso") is True,
    }


def _unavailable(message="El estado de las alertas no pudo verificarse."):
    return {
        "ok": False, "status": "unknown", "alerts": [],
        "checked_at": _time_now(),
        "source": "SMN vía Mi Clima",
        "source_url": "https://www.smn.gob.ar/alertas",
        "data_provider": "https://miclima.com.ar/api",
        "error": message
    }


def _load(lat, lon):
    health = _source_health()
    if not health["healthy"]:
        return _unavailable("La fuente de alertas del SMN no está actualizada o disponible.")
    query = urlencode({"lat": round(float(lat), 4), "lon": round(float(lon), 4)})
    try:
        data = _get_json(BASE + "/api/riesgo?" + query)
        alerts_raw = data.get("alertas")
        flag = data.get("hay_alertas")
        if type(flag) is not bool or not isinstance(alerts_raw, list):
            raise ValueError("Respuesta sin campos de alerta verificables")
        if flag != bool(alerts_raw):
            raise ValueError("Respuesta contradictoria de alertas")
        if _parsed(data.get("actualizado")) is None:
            raise ValueError("La consulta no tiene hora de actualización")
        if abs((datetime.now(timezone.utc) - _parsed(data["actualizado"])).total_seconds()) > 900:
            raise ValueError("La respuesta de riesgo está desactualizada")
        alerts = [_normalise(a, data.get("lugar") or "") for a in alerts_raw]
        alerts.sort(key=lambda a: _RANK.get(a["level"], 0), reverse=True)
        if alerts and alerts[0]["level"] == "unknown":
            raise ValueError("Hay una alerta, pero no se pudo determinar su nivel")
        return {
            "ok": True,
            "status": alerts[0]["level"] if alerts else "green",
            "alerts": alerts[:35],
            "checked_at": data["actualizado"],
            "source_updated_at": health["source_updated"],
            "source": "Servicio Meteorológico Nacional · vía Mi Clima",
            "source_url": "https://www.smn.gob.ar/alertas",
            "data_provider": "https://miclima.com.ar/api",
            "error": None,
            "scope": "Alertas meteorológicas para el punto seleccionado, con avisos previstos a futuro."
        }
    except Exception as exc:
        print("Mi Clima: fallo al consultar el riesgo:", type(exc).__name__, str(exc)[:140], flush=True)
        return _unavailable()


def alerts_for(lat, lon):
    """Devuelve estado por coordenadas sin exponer lat/lon en logs."""
    key = (round(float(lat), 3), round(float(lon), 3))
    now = time.monotonic()
    with _LOCK:
        cached = _POINTS.get(key)
        if cached and now - cached[0] <= CACHE_TTL:
            return cached[1]
    result = _load(lat, lon)
    with _LOCK:
        if len(_POINTS) > 500:
            _POINTS.clear()
        _POINTS[key] = (time.monotonic(), result)
    return result
