"""Test Fix C — cache anti-lag."""
import asyncio
import time
from bitrix_tasks import (
    _cache_guardar, _cache_actualizar, _cache_leer_frescas,
    _CACHE_TAREAS_RECIENTES,
)
from models import Tarea, EstadoEOS


# Limpiar cache antes de empezar
_CACHE_TAREAS_RECIENTES.clear()

# 1) Guardar una tarea recién "creada"
t = Tarea(id=99001, title="Test cache", status_eos=EstadoEOS.NEW)
_cache_guardar(99001, responsable_id=42, tarea=t)

# 2) Leer con filtro correcto: aparece
frescas = _cache_leer_frescas(filtro_responsable_id=42)
assert len(frescas) == 1
assert frescas[0].id == 99001

# 3) Leer con filtro distinto: no aparece
frescas = _cache_leer_frescas(filtro_responsable_id=43)
assert len(frescas) == 0

# 4) Leer sin filtro: aparece
frescas = _cache_leer_frescas(filtro_responsable_id=None)
assert len(frescas) == 1

# 5) Actualizar cache
_cache_actualizar(99001, {"status_eos": EstadoEOS.CANCELLED})
frescas = _cache_leer_frescas(filtro_responsable_id=42)
assert frescas[0].status_eos == EstadoEOS.CANCELLED

# 6) Actualizar tarea NO cacheada: no rompe
_cache_actualizar(99999, {"status_eos": EstadoEOS.COMPLETED})  # no-op

# 7) TTL expira: purga automática al leer
# Truquito: forzamos el timestamp a 60s en el pasado
ts_original, r_id, tarea_original = _CACHE_TAREAS_RECIENTES[99001]
_CACHE_TAREAS_RECIENTES[99001] = (ts_original - 60.0, r_id, tarea_original)

frescas = _cache_leer_frescas(filtro_responsable_id=42)
assert len(frescas) == 0, "Debería haber expirado"
assert 99001 not in _CACHE_TAREAS_RECIENTES, "Debería haberse purgado"

print("Fix C OK")