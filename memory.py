from datetime import datetime

historial = []
resumen_previo = ""

async def save_message(role: str, text):
    global historial
    global resumen_previo
    prompt = {}
    fecha = datetime.now()
    
    prompt["role"] = role
    prompt["fecha"] = fecha
    prompt["text"] = text

    historial.append(prompt)
        
def get_history():
    return historial

def get_resumen():
    if resumen_previo:
        return resumen_previo
    else:
        return None

def set_resumen(new_resumen):
    global resumen_previo
    resumen_previo = new_resumen

def check_history():
    return len(historial) >= 15

def del_history():
    del historial[:8]