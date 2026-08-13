"""bootstrap_bitrix_fields.py — Crea los 15 UF_* del modelo Tarea EOS
en cada tenant Bitrix configurado.

Ejecucion: python bootstrap_bitrix_fields.py [usuario]
- Sin argumento: procesa todos los usuarios de config_usuarios
- Con nombre:    procesa solo ese usuario (carles | alexander)

Idempotente: si el campo ya existe (Bitrix devuelve
'ERROR_CORE: The field UF_XXX for the object TASKS_TASK already exists.'),
se salta con log info y se continua con el siguiente. Solo falla ante
errores reales (autenticacion, red, permisos insuficientes).

Diseno:
- httpx directo, sin pasar por bitrix.solicitud(), porque necesitamos
  inspeccionar 'error_description' (no solo 'error') para detectar el
  caso 'already exists'. bitrix.solicitud() solo captura el codigo.
- No aborta al primer fallo: intenta los 15 campos por usuario y
  reporta un resumen al final. Asi si uno falla puntualmente por red,
  el resto se crea igual y se puede reejecutar sin miedo.
- Codigo de salida != 0 si hubo cualquier error real, para que se pueda
  encadenar en scripts o CI.
"""
import asyncio
import sys
import httpx

from config_usuarios import USUARIOS_POR_USERNAME
import logger


# --- Definicion de los 15 UF_* ---
# Fuente: PHASE 1 §6.1 (estados) + §8 (campos del work item) + §8.2
# (campos de conversacion). SORT espaciado en decenas para poder
# insertar campos intermedios sin renumerar todo mas adelante.
CAMPOS_TAREA_EOS: list[dict] = [
    # --- Estado y clasificacion (100-129) ---
    {"FIELD_NAME": "UF_STATUS_EOS",           "USER_TYPE_ID": "string",   "LABEL": "EOS Status",
     "EDIT_FORM_LABEL": {"en": "EOS Status",           "es": "Estado EOS"},                  "SORT": 100},
    {"FIELD_NAME": "UF_TASK_TYPE",            "USER_TYPE_ID": "string",   "LABEL": "Task Type",
     "EDIT_FORM_LABEL": {"en": "Task Type",            "es": "Tipo de tarea"},               "SORT": 110},
    {"FIELD_NAME": "UF_ALEXANDER_ROLE",       "USER_TYPE_ID": "string",   "LABEL": "Alexander Role",
     "EDIT_FORM_LABEL": {"en": "Alexander Role",       "es": "Rol de Alexander"},            "SORT": 120},

    # --- Ejecucion (130-169) ---
    {"FIELD_NAME": "UF_NEXT_ACTION",          "USER_TYPE_ID": "string",   "LABEL": "Next Action",
     "EDIT_FORM_LABEL": {"en": "Next Action",          "es": "Siguiente accion"},            "SORT": 130},
    {"FIELD_NAME": "UF_EXPECTED_RESULT",      "USER_TYPE_ID": "string",   "LABEL": "Expected Result",
     "EDIT_FORM_LABEL": {"en": "Expected Result",      "es": "Resultado esperado"},          "SORT": 140},
    {"FIELD_NAME": "UF_REVIEW_DATE",          "USER_TYPE_ID": "datetime", "LABEL": "Review Date",
     "EDIT_FORM_LABEL": {"en": "Review Date",          "es": "Fecha de revision"},           "SORT": 150},
    {"FIELD_NAME": "UF_SOURCE",               "USER_TYPE_ID": "string",   "LABEL": "Source",
     "EDIT_FORM_LABEL": {"en": "Source",               "es": "Fuente"},                      "SORT": 160},

    # --- Riesgo y control (170-189) ---
    {"FIELD_NAME": "UF_RISK",                 "USER_TYPE_ID": "string",   "LABEL": "Risk",
     "EDIT_FORM_LABEL": {"en": "Risk",                 "es": "Riesgo"},                      "SORT": 170},
    {"FIELD_NAME": "UF_ESCALATION_CONDITION", "USER_TYPE_ID": "string",   "LABEL": "Escalation Condition",
     "EDIT_FORM_LABEL": {"en": "Escalation Condition", "es": "Condicion de escalado"},       "SORT": 180},

    # --- Conversacion ejecutiva (190-249) ---
    {"FIELD_NAME": "UF_REQUIRES_CONVERSATION", "USER_TYPE_ID": "boolean", "LABEL": "Requires Conversation",
     "EDIT_FORM_LABEL": {"en": "Requires Conversation","es": "Requiere conversacion"},       "SORT": 190},
    {"FIELD_NAME": "UF_PRIMARY_INTERLOCUTOR",  "USER_TYPE_ID": "string",  "LABEL": "Primary Interlocutor",
     "EDIT_FORM_LABEL": {"en": "Primary Interlocutor", "es": "Interlocutor principal"},      "SORT": 200},
    {"FIELD_NAME": "UF_CONVERSATION_PURPOSE",  "USER_TYPE_ID": "string",  "LABEL": "Conversation Purpose",
     "EDIT_FORM_LABEL": {"en": "Conversation Purpose", "es": "Proposito de la conversacion"},"SORT": 210},
    {"FIELD_NAME": "UF_EXPECTED_DECISION",     "USER_TYPE_ID": "string",  "LABEL": "Expected Decision",
     "EDIT_FORM_LABEL": {"en": "Expected Decision",    "es": "Decision esperada"},           "SORT": 220},
    {"FIELD_NAME": "UF_MEETING_CANDIDATE",     "USER_TYPE_ID": "boolean", "LABEL": "Meeting Candidate",
     "EDIT_FORM_LABEL": {"en": "Meeting Candidate",    "es": "Candidato a reunion"},         "SORT": 230},
    {"FIELD_NAME": "UF_RELATED_MEETING_ID",    "USER_TYPE_ID": "string",  "LABEL": "Related Meeting ID",
     "EDIT_FORM_LABEL": {"en": "Related Meeting ID",   "es": "ID de reunion relacionada"},   "SORT": 240},
]


def _normalizar_webhook(webhook: str) -> str:
    """Asegura que el webhook acabe en '/' para concatenar el metodo
    limpio (f'{webhook}task.item.userfield.add'). bitrix.py asume lo
    mismo implicitamente."""
    webhook = webhook.strip()
    if not webhook.endswith("/"):
        webhook += "/"
    return webhook


async def _crear_campo(
    client: httpx.AsyncClient, webhook: str, user_id: str, definicion: dict
) -> str:
    """Crea un UF_* en el tenant Bitrix del usuario. Devuelve el estado:
    'created' | 'exists' | 'error'.

    Bitrix devuelve HTTP 400 (no 200) tanto para 'already exists' como
    para rechazos por permisos o parametros invalidos. Ambos casos
    llevan un body JSON con 'error' + 'error_description'. Por eso
    parseamos SIEMPRE el body antes de decidir, sin cortocircuitar por
    el status code, y solo damos por perdida la respuesta si no es
    JSON interpretable.

    Idempotencia: si error_description contiene 'already exists',
    exito silencioso (info + return 'exists'). Cualquier otra respuesta
    con 'error' se loguea con el error_description en el propio mensaje
    para poder diagnosticar sin abrir Supabase.
    """
    field_name = definicion["FIELD_NAME"]
    params = {
        "PARAMS": {
            **definicion,
            "XML_ID": field_name,
            "MULTIPLE": "N",  # para boolean siempre N (regla de la API)
            "MANDATORY": "N", # opcional para no romper tareas preexistentes
        }
    }

    try:
        resp = await client.post(f"{webhook}task.item.userfield.add", json=params)
    except httpx.RequestError as e:
        logger.error("bootstrap", "field_network_error",
                     f"Fallo de red creando {field_name}",
                     user_id=user_id, error=e,
                     metadata={"field": field_name})
        return "error"

    try:
        data = resp.json()
    except ValueError as e:
        logger.error("bootstrap", "field_bad_json",
                     f"Respuesta no-JSON creando {field_name} (HTTP {resp.status_code})",
                     user_id=user_id, error=e,
                     metadata={"field": field_name,
                               "status": resp.status_code,
                               "body": resp.text[:500]})
        return "error"

    if "error" in data:
        descripcion = str(data.get("error_description", ""))
        if "already exists" in descripcion.lower():
            logger.info("bootstrap", "field_exists",
                        f"{field_name} ya existe, saltando",
                        user_id=user_id, metadata={"field": field_name})
            return "exists"
        # Error real: permisos, tipo invalido, webhook malo, etc. Metemos
        # el error_description en el mensaje para verlo en stdout directo.
        logger.error("bootstrap", "field_bitrix_error",
                     f"Bitrix rechazo {field_name} (HTTP {resp.status_code}): "
                     f"[{data['error']}] {descripcion}",
                     user_id=user_id,
                     metadata={"field": field_name,
                               "status": resp.status_code,
                               "error": data["error"],
                               "error_description": descripcion})
        return "error"

    # Body sin 'error' pero status raro: seguimos, pero avisamos.
    # No deberia pasar segun la doc, pero mejor no comernos un created
    # silencioso si algo huele mal.
    if not resp.is_success:
        logger.warn("bootstrap", "field_unexpected_status",
                    f"Status {resp.status_code} pero sin 'error' en body: {field_name}",
                    user_id=user_id,
                    metadata={"field": field_name,
                              "status": resp.status_code,
                              "body": resp.text[:500]})

    field_id = data.get("result")
    logger.info("bootstrap", "field_created",
                f"{field_name} creado (id={field_id})",
                user_id=user_id,
                metadata={"field": field_name, "field_id": field_id})
    return "created"


async def bootstrap_usuario(user_id: str, usuario: dict) -> dict:
    """Ejecuta el bootstrap para un usuario. Devuelve resumen con
    contadores {created, exists, error, skipped}.

    No aborta al primer fallo: intenta crear los 15 campos y reporta al
    final. Asi, si uno falla puntualmente por red, los otros se crean
    y una segunda ejecucion cierra el gap.
    """
    webhook = usuario.get("webhook_bitrix", "").strip()
    if not webhook:
        logger.warn("bootstrap", "usuario_sin_webhook",
                    f"Usuario {user_id} sin WEBHOOK_BITRIX configurado, saltando",
                    user_id=user_id)
        return {"created": 0, "exists": 0, "error": 0, "skipped": True}

    webhook = _normalizar_webhook(webhook)
    logger.info("bootstrap", "usuario_inicio",
                f"Empezando bootstrap para {user_id} ({len(CAMPOS_TAREA_EOS)} campos)",
                user_id=user_id)

    resumen = {"created": 0, "exists": 0, "error": 0, "skipped": False}
    async with httpx.AsyncClient(timeout=60) as client:
        for definicion in CAMPOS_TAREA_EOS:
            estado = await _crear_campo(client, webhook, user_id, definicion)
            resumen[estado] += 1

    logger.info("bootstrap", "usuario_fin",
                f"Bootstrap {user_id} terminado: "
                f"{resumen['created']} creados, "
                f"{resumen['exists']} existentes, "
                f"{resumen['error']} errores",
                user_id=user_id, metadata=resumen)
    return resumen


async def main() -> int:
    """Entry point. Codigo de salida:
        0 = todo OK (0 errores en todos los usuarios procesados)
        1 = al menos un error de creacion en algun usuario
        2 = argumento invalido (usuario desconocido)
    """
    if len(sys.argv) > 1:
        target = sys.argv[1].lower()
        if target not in USUARIOS_POR_USERNAME:
            print(f"ERROR: usuario '{target}' no existe. "
                  f"Opciones: {list(USUARIOS_POR_USERNAME.keys())}")
            return 2
        objetivos = {target: USUARIOS_POR_USERNAME[target]}
    else:
        objetivos = USUARIOS_POR_USERNAME

    errores_totales = 0
    for user_id, usuario in objetivos.items():
        resumen = await bootstrap_usuario(user_id, usuario)
        errores_totales += resumen["error"]

    if errores_totales:
        print(f"\nBOOTSTRAP FINALIZADO CON {errores_totales} ERRORES. "
              f"Revisa los logs (source='bootstrap') en Supabase o stdout.")
        return 1

    print("\nBOOTSTRAP COMPLETADO SIN ERRORES.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))