"""Configuracion de usuarios. Fuente unica de verdad sobre quien puede
usar el sistema, con que contexto Bitrix opera cada uno, y como se
autentican en la mini-app web.

Diseno: dos dicts indexados por la clave que llega en cada canal.
- USUARIOS_POR_TELEGRAM_ID: para el bot Telegram, lookup por from_user.id
- USUARIOS_POR_USERNAME: para la web (Basic Auth), lookup por username

Ambos apuntan al mismo user_id logico ("carles" | "alexander") que se
usa como clave en Supabase (conversation_history, conversation_summary,
token_usage) y como identificador en logs.

Escala: hardcoded para dos usuarios porque es lo que hay. Si algun dia
entra un tercero, se anade una entrada mas y ya. Cuando pase de 5,
migrar a tabla en Supabase.
"""
from os import getenv
from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str) -> int:
    """Lee un env var como int. Si no existe o esta vacio, devuelve 0.
    0 se usa como sentinela de 'no configurado' — nunca es un telegram_id
    ni un bitrix_user_id valido."""
    val = getenv(name, "").strip()
    if not val:
        return 0
    try:
        return int(val)
    except ValueError:
        print(f"CONFIG WARN: {name} no es un entero valido ('{val}'), usando 0")
        return 0


# --- Contextos por usuario ---
# Cada entrada agrupa TODO lo que el sistema necesita saber sobre un
# usuario: su user_id logico, su webhook Bitrix (calendario propio),
# su bitrix_user_id (para el parametro ownerId en las llamadas), y
# su password para la mini-app.
#
# NOTA: los env vars pueden llegar vacios si no estan en .env. En ese
# caso el usuario existe en el dict pero no puede operar realmente
# (Bitrix rechazaria las llamadas). Se filtran en runtime al hacer
# lookup — ver usuario_por_telegram_id() y autenticar_web().

_CARLES = {
    "user_id": "carles",
    "webhook_bitrix": getenv("WEBHOOK_BITRIX_CARLES", ""),
    "bitrix_user_id": _int_env("BITRIX_USER_ID_CARLES"),
    "app_password": getenv("APP_PASSWORD_CARLES", ""),
}

_ALEXANDER = {
    "user_id": "alexander",
    "webhook_bitrix": getenv("WEBHOOK_BITRIX_ALEXANDER", ""),
    "bitrix_user_id": _int_env("BITRIX_USER_ID_ALEXANDER"),
    "app_password": getenv("APP_PASSWORD_ALEXANDER", ""),
}

_CARLOS = {
    "user_id": "carlos",
    "webhook_bitrix": getenv("WEBHOOK_BITRIX_CARLOS", ""),
    "bitrix_user_id": _int_env("BITRIX_USER_ID_CARLOS"),
    "app_password": getenv("APP_PASSWORD_CARLOS", ""),
}


# --- Indices ---
# Los construimos filtrando los usuarios sin telegram_id o sin
# credenciales, para que el filtro del bot y el middleware Basic Auth
# rechacen automaticamente a quien no este bien configurado.

_CARLES_TG = _int_env("CARLES_TELEGRAM_ID")
_ALEXANDER_TG = _int_env("ALEXANDER_TELEGRAM_ID")
_CARLOS_TG = _int_env("CARLOS_TELEGRAM_ID")

USUARIOS_POR_TELEGRAM_ID: dict[int, dict] = {}
if _CARLES_TG:
    USUARIOS_POR_TELEGRAM_ID[_CARLES_TG] = _CARLES
if _ALEXANDER_TG:
    USUARIOS_POR_TELEGRAM_ID[_ALEXANDER_TG] = _ALEXANDER
if _CARLOS_TG:
    USUARIOS_POR_TELEGRAM_ID[_CARLOS_TG] = _CARLOS

USUARIOS_POR_USERNAME: dict[str, dict] = {
    "carles": _CARLES,
    "alexander": _ALEXANDER,
    "carlos": _CARLOS,
}


# --- Helpers publicos ---

def usuario_por_telegram_id(telegram_id: int) -> dict | None:
    """Devuelve el contexto de usuario para un telegram_id, o None si
    no esta autorizado. Los handlers del bot llaman a esto antes de
    procesar cualquier mensaje.
    """
    return USUARIOS_POR_TELEGRAM_ID.get(telegram_id)


def autenticar_web(username: str, password: str) -> dict | None:
    """Devuelve el contexto de usuario si las credenciales validan, o
    None. El middleware Basic Auth de server.py llama a esto.

    Comparacion con secrets.compare_digest para no filtrar tiempo de
    ejecucion (defensa contra timing attacks). Si el usuario no existe,
    igualmente comparamos contra una cadena dummy del mismo tamano
    para que el tiempo sea constante.
    """
    import secrets as _secrets

    user = USUARIOS_POR_USERNAME.get(username)
    if user is None:
        # Comparacion dummy para que el tiempo de rechazo por usuario
        # inexistente sea similar al de password incorrecta
        _secrets.compare_digest("dummy_password_xxxxxx", password)
        return None

    esperado = user.get("app_password", "")
    if not esperado:
        return None

    if _secrets.compare_digest(esperado, password):
        return user
    return None


def contexto_bitrix(usuario: dict) -> tuple[str, int]:
    """Extrae (webhook, bitrix_user_id) del contexto de usuario. Helper
    para las llamadas a bitrix.py, que reciben ambos por argumento."""
    return usuario["webhook_bitrix"], usuario["bitrix_user_id"]


# --- Aviso de arranque ---
# En dev mode (ninguno configurado) es util saberlo. Si al menos uno
# esta configurado, asumimos que es intencional y no logueamos nada.

if not USUARIOS_POR_TELEGRAM_ID:
    print("CONFIG WARN: no hay usuarios configurados en USUARIOS_POR_TELEGRAM_ID. "
          "Bot Telegram rechazara a todos. Configura CARLES_TELEGRAM_ID o "
          "ALEXANDER_TELEGRAM_ID en el .env.")