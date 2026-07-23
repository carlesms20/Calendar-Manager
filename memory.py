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
        res = await agent.summarize(msg_antiguos, resumen_previo)
        del historial[:8]
        resumen_previo = res #donde guardar el resumen previo?
        prompt["role"] = "user"
        prompt["fecha"] = datetime.now()
        prompt["text"] = res
        historial.append(prompt)
        
def get_history():
    return historial

