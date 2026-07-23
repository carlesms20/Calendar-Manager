from datetime import datetime
import agent

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
    
    if  len(historial) >= 15:
        msg_antiguos = historial[:8]
        resumen_previo = await agent.summarize(msg_antiguos, resumen_previo)
        del historial[:8] #historial se queda solamente con los ultimos 8
        
def get_history():
    return historial

def get_resumen():
    if resumen_previo != "":
        return resumen_previo
    else:
        return False
