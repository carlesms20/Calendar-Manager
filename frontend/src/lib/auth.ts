// Gestion del JWT que llegara desde el dashboard de David via postMessage.
// Por ahora es placeholder: el backend aun no valida JWT, pero dejamos
// la infraestructura montada para que este todo listo cuando toque.

let jwtToken: string | null = null;

/**
 * Devuelve el token JWT actual, o null si no lo tenemos aun.
 */
export function getToken(): string | null {
  return jwtToken;
}

/**
 * Detecta si la app corre dentro de un iframe.
 */
export function estaDentroDeIframe(): boolean {
  try {
    return window.self !== window.top;
  } catch {
    return true;
  }
}

/**
 * Arranca el listener de postMessage y avisa al parent que estamos listos
 * para recibir el token. Llamar una sola vez al montar la app.
 */
export function inicializarAuth(): void {
  // Escuchar mensajes del parent
  window.addEventListener("message", (event) => {
    // TODO cuando toque produccion: validar event.origin contra el dominio
    // del dashboard. Por ahora aceptamos cualquiera para desarrollo local.
    if (event.data?.type === "auth:token" && typeof event.data.token === "string") {
      jwtToken = event.data.token;
      console.log("[auth] Token recibido del parent.");
    }
  });

  // Avisar al parent que estamos listos (solo si estamos en iframe)
  if (estaDentroDeIframe() && window.parent !== window) {
    window.parent.postMessage({ type: "agente:ready" }, "*");
    console.log("[auth] Avisado al parent que estamos listos.");
  }
}
