# Plan de Ejecución: del MVP al Executive Operating System

**Versión:** 1.0  
**Fecha:** 11 de agosto de 2026  
**Autor:** Carles (Syncrosfera)  
**Cliente:** Alexander Kolobnev, CEO Syncrosfera  
**Cobertura:** PHASE 1 v1.1 (FOUNDATION), PHASE 2 (EXECUTIVE WORKFLOW), PHASE 3 (GOOGLE CALENDAR), PHASE 6 Document 3 (EXECUTIVE PLANNING ENGINE)  
**Estado:** DRAFT — pendiente de aprobación del CEO

---

## 1. Punto de partida

MVP desplegado en Railway el 7 de agosto de 2026, multiusuario (`carles` como dev, `alexander` como cliente), con:

- Bot Telegram + mini-app web servida bajo `/app` con Basic Auth
- Bitrix24 Calendar como backend (crear / leer / modificar / eliminar eventos)
- Voz vía Gemini free tier (STT + TTS)
- Anthropic Sonnet 5 como motor de razonamiento con prompt caching activo
- Buffer de operaciones pendientes con confirmación explícita
- Persistencia en Supabase: `conversation_history`, `conversation_summary`, `token_usage`

**Lo que hace el MVP hoy:** agendar, modificar y eliminar eventos de calendario; calcular huecos libres; mantener contexto conversacional por usuario.

**Lo que NO hace el MVP hoy:** ninguna otra cosa de las que piden PHASE 1, PHASE 2, PHASE 3 y PHASE 6 Doc 3.

---

## 2. Diagnóstico de gaps

Cinco brechas arquitectónicas entre el MVP actual y el Executive Operating System descrito en los PHASE docs.

### G1 — Conversación Ejecutiva y consolidación de reuniones

**Cubre:** PHASE 1 §1.4, §2.5-§2.7, PHASE 2 §1-3, PHASE 3 §1-4, PHASE 6 Doc 3 §11.

**Actual:** el MVP crea eventos aislados, uno por uno. Si Alexander agenda tres cosas con Carlos, se crean tres eventos independientes.

**Objetivo:** cuando hay N tareas con el mismo interlocutor principal, el sistema propone UNA reunión ejecutiva con agenda estructurada, duración estimada y hueco propuesto. Las tareas originales se conservan (PHASE 1 §2.8: "no se eliminan tareas por haber sido agrupadas").

### G2 — Modelo de Tarea distinto del modelo de Evento

**Cubre:** PHASE 1 §6 Status Model, §8 Work Item Model; PHASE 4 (LOCK en `ROADMAP.md`, NO implementada en MVP).

**Actual:** solo existe `Evento` (calendar item con hora concreta). Todo lo que Alexander pide se materializa como evento de calendario.

**Objetivo:** modelo `Tarea` con los 8 estados permitidos (`New`, `In Progress`, `Delegated`, `Waiting`, `Blocked`, `Scheduled`, `Completed`, `Cancelled`) y los campos del Work Item Model: `Owner`, `Alexander Role`, `Next Action`, `Expected Result`, `Deadline`, `Review Date`, `Escalation Condition`, `Requires Conversation`, `Primary Interlocutor`, `Meeting Candidate`.

### G3 — Executive Brief diario

**Cubre:** PHASE 1 §4, PHASE 6 Doc 3 §4.

**Actual:** el sistema es puramente reactivo; solo responde cuando se le habla.

**Objetivo:** Brief matutino automático con las 13 secciones de PHASE 1 §4.1 (Executive Summary, Calendar Overview, Three Key Outcomes, Quick Actions, People Blocked by Alexander, Executive Conversations and Proposed Meetings, Delegated Work, Waiting for Responses, Proposed Work Blocks, Not Today, Remaining Task Inventory, Missing Information, Integrity Check).

### G4 — Delegation Model

**Cubre:** PHASE 1 §7.

**Actual:** inexistente. Todo se asume ejecutado por Alexander.

**Objetivo:** 5 niveles de delegación (`CEO Execution`, `CEO Decision`, `CEO Approval`, `CEO Supervision`, `No Involvement`) con campos obligatorios: `Owner`, `Expected Result`, `Deadline`, `Preparation Required`, `Review Point`, `Escalation Condition`, `Next Action If Missed`.

### G5 — Executive Planning Engine (capacity + forecast + reminders)

**Cubre:** PHASE 6 Doc 3 §9-§16.

**Actual:** solo el cálculo de huecos libres (`consultar_huecos_libres`).

**Objetivo:** capacity planning con buffer mínimo del 30%, time blocking en 5 categorías (`Ultra Short` 5-10 min a `Strategic Block` >120 min), reminder engine proactivo priorizado según PHASE 6 Doc 3 §13, forecast engine que anticipa sobrecarga antes de que ocurra (§14), Integrity Rules ejecutadas antes de cada Brief (§15).

---

## 3. Principios rectores

- **`Analyse → Propose → Confirm → Execute`**: ninguna operación externa se ejecuta sin confirmación explícita del CEO. Aplica a eventos, tareas, delegaciones y cambios de estado. Regla presente en PHASE 1 §5.6, PHASE 2 §5, PHASE 3 §1 y PHASE 6 Doc 3.
- **Orden de dependencias del roadmap**: Foundation → Workflow → Calendar → Bitrix → Dashboard → Intelligence. No implementar Intelligence antes de tener el Modelo de Tarea (RULE-005 del `ROADMAP.md`).
- **No romper el MVP**: cada sprint entrega valor sin degradar lo desplegado. Alexander debe poder seguir usando el bot con normalidad al terminar cada sprint.
- **`[NO DATA]` explícito**: cuando falte información obligatoria, nunca inventar; usar la marca `[NO DATA]` literal (PHASE 1 §1.2).
- **Inventario completo**: nunca ocultar trabajo pendiente. Items de baja prioridad van a `Not Today` o `Remaining Task Inventory`, nunca desaparecen (PHASE 1 §1.2, PHASE 6 Doc 3 §4).
- **Fuentes de verdad únicas**: Bitrix24 para tareas y calendario, Executive Operating System para razonamiento y propuestas, conversación confirmada para info directa del CEO (PHASE 1 §3.2).

---

## 4. Sprints

### Sprint 0 — Observabilidad y estabilidad

**Duración estimada:** ~1 sesión (~1 hora)  
**Estado:** EN CURSO  
**Objetivo:** cerrar la deuda del refactor multiusuario y establecer visibilidad operativa antes de meter features nuevas.  
**Aporta a los gaps:** ninguno directamente; habilita el debugging de todos los sprints siguientes.

**Deliverables:**

- Tabla `app_logs` en Supabase con `level`, `source`, `event`, `message`, `user_id`, `metadata jsonb`, `error_type`, `error_stack` e índices por `created_at`, `user_id` y `level`
- Módulo `logger.py` con dual-write (Supabase + stdout), interfaz `info/warn/error(source, event, message, ...)`, falla suave si Supabase peta
- Instrumentación quirúrgica de los `try/except` que hoy se tragan errores: `agent.procesar_input`, `server.mensaje_texto`, `server.mensaje_audio`, `server.sintetizar_voz`, `server.obtener_uso`, `bot.audio_handler`, `voice.transcribir`, `tts.sintetizar`, `usage.registrar`
- `server.py`: redirect `/` → `/app` con `RedirectResponse`
- `railway.json`: añadir `deploy.healthcheckPath: "/health"` y `healthcheckTimeout: 100`

**Acceptance:**

- Un error real en producción genera fila en `app_logs` con `error_type`, `error_stack` y `metadata` con el texto de entrada
- Query `SELECT level, event, count(*) FROM app_logs WHERE created_at > now() - interval '1 day' GROUP BY 1,2` devuelve resultado tabulado
- `curl <URL>/` responde 302 a `/app`
- Railway healthcheck en verde

---

### Sprint 1 — Conversación Ejecutiva Light + Bloques No Negociables

**Duración estimada:** ~1 semana  
**Estado:** PLANIFICADO  
**Objetivo:** primera capa del Executive OS visible para Alexander, sin refactorizar el modelo de datos.  
**Aporta a los gaps:** G1 (consolidación de reuniones sobre eventos existentes), G5 parcial (protección de bloques).

**Bloque A — Detección de agrupación por interlocutor**

- Nueva tool `proponer_consolidacion(user_id, ventana_dias)`: consulta eventos futuros con `involucrado` no vacío, agrupa por persona, aplica Meeting Compatibility Test (PHASE 1 §2.7), devuelve propuestas de consolidación
- Actualización de `SYSTEM_PROMPT_ESTABLE` en `agent.py`: cuando el usuario prepara N eventos con el mismo `involucrado` en el buffer o el detector encuentra N eventos futuros con la misma persona, el agente pregunta si consolidar en una reunión ejecutiva
- Nueva tool `crear_reunion_ejecutiva(user_id, interlocutor, temas: list[str], duracion_estimada, hueco_propuesto)`: crea UN evento en Bitrix con la agenda estructurada en la descripción y (opcionalmente) marca los eventos individuales como consolidados en meeting X sin borrarlos
- Frontend: cuando la respuesta del agente contenga una propuesta consolidada, botón visible para confirmar

**Bloque B — Bloques no negociables (Caso 13)**

- Tabla `bloques_no_negociables` en Supabase: `user_id`, `nombre`, `dia_semana` (0-6 o -1 para diario), `hora_inicio` (time), `hora_fin` (time), `activo` (bool), `descripcion`
- Tool `gestionar_bloques(user_id, accion: "listar"|"añadir"|"eliminar", ...)`
- Inyección en `_calcular_huecos`: los bloques activos del `user_id` se restan como intervalos ocupados

**Acceptance:**

- **Test C-01** (PHASE 1 §12.2): Alexander prepara 2 eventos con Carlos → agente detecta y propone consolidar. PASS si no se muestran como dos conversaciones separadas sin justificación
- **Test C-02** (§12.2): dos temas con Carlos incompatibles (uno confidencial) → agente detecta y mantiene separados con `Compatibility Reason`
- **Test CAL-03** (§12.3): con bloque no negociable "gimnasio 07:00-08:00" activo, `consultar_huecos_libres` NO devuelve intervalos que solapen ese bloque
- Cada propuesta consolidada sigue el patrón `Analyse → Propose → Confirm → Execute` explícito

---

### Sprint 2 — Modelo de Tarea (Bitrix Tasks)

**Duración estimada:** ~2 semanas  
**Estado:** PLANIFICADO  
**Objetivo:** el refactor arquitectónico que desbloquea todo lo demás.  
**Aporta a los gaps:** G2 completo, sienta base para G3, G4 y G5.

**Modelo**

- Nuevo `models.Tarea`: `id`, `titulo`, `tipo` (`Task` | `Waiting` | `Decision` | `Risk` | `Information`), `status` (los 8 de PHASE 1 §6.1), `owner`, `alexander_role` (`Execution` | `Decision` | `Approval` | `Supervision` | `No Involvement`), `next_action`, `expected_result`, `deadline`, `review_date`, `source`, `risk`, `escalation_condition`, `requires_conversation`, `primary_interlocutor`, `conversation_purpose`, `expected_decision`, `meeting_candidate`, `related_executive_meeting_id`

**Integración Bitrix Tasks**

- Nuevo módulo `bitrix_tasks.py` con `tasks.task.add`, `tasks.task.get`, `tasks.task.list`, `tasks.task.update`, `tasks.task.delete`
- Mapping bidireccional entre `models.Tarea` y campos Bitrix Task
- Campos personalizados `UF_*` para los atributos del Work Item Model sin equivalente nativo (`next_action`, `expected_result`, `escalation_condition`, etc.)

**Tools nuevas**

- `crear_tarea(user_id, titulo, owner, deadline?, next_action?, ...)` con buffer y confirmación
- `consultar_tareas(user_id, status?, owner?, texto_libre?)` con filtros
- `actualizar_estado_tarea(user_id, id, nuevo_status)` con validación de transición legal (PHASE 1 §6.4)
- `delegar_tarea(user_id, id, owner, review_date, escalation_condition, expected_result)` que encapsula la transición → `Delegated`
- `marcar_waiting(user_id, id, waiting_for, expected_response, next_follow_up)` que encapsula → `Waiting`

**System prompt**

- Distinción explícita entre "crear evento" (calendar item) y "crear tarea" (work item). Regla base: si el usuario no menciona hora concreta, es Task; si menciona hora, es Event.

**Acceptance:**

- Alexander: "recuérdame llamar a proveedores mañana" → se crea Task con `owner=Alexander`, `status=New`, `next_action="llamar proveedores"`, `deadline=mañana`. NO se crea evento en calendario
- Alexander: "reunión con Carlos a las 10 el jueves" → se crea Event como hasta ahora
- Transiciones ilegales rechazadas (por ejemplo `Completed → New`)
- **Test PM-01** (§12.4): una reunión produce decisión + tarea delegada + waiting → cada objeto se crea/actualiza correctamente y ninguna tarea se cierra automáticamente por celebrarse la reunión

---

### Sprint 3 — Executive Brief Diario

**Duración estimada:** ~1 semana  
**Estado:** PLANIFICADO  
**Objetivo:** el sistema pasa de reactivo a proactivo.  
**Aporta a los gaps:** G3 completo.

**Deliverables:**

- Nuevo módulo `brief.py` que orquesta el Executive Reasoning Pipeline (PHASE 1 §2.2) para producir el Brief diario:
  1. Recopilación (`consultar_tareas` + `consultar_ocupacion_bitrix` + `bloques_no_negociables`)
  2. Estructuración según PHASE 1 §4.1
  3. Priorización según PHASE 1 §2.4
  4. Detección de conversación reutilizando las herramientas del Sprint 1
  5. Generación de la sección `Executive Conversations and Proposed Meetings`
  6. Integrity Check contra el checklist del Apéndice C
- Cron matutino: 07:00 hora Madrid, días laborables, para cada usuario configurado
- Delivery dual: mensaje Telegram estructurado + endpoint `/api/brief` para el frontend
- Adaptar el skill `morning` existente en `/mnt/skills/examples/morning/SKILL.md` como referencia de layout

**Acceptance:**

- **Test EB-01** (§12.5): Calendar Overview distingue claramente reuniones confirmadas, propuestas, bloques protegidos y capacidad libre
- **Test EB-02** (§12.5): un resultado con José se formula como aprobación/decisión, NO como "reunirse con José"
- Máximo 3 Key Outcomes (PHASE 1 §4.4)
- Al menos 30% de capacidad libre en el plan (PHASE 1 §5.8)
- `[NO DATA]` explícito allí donde falte información obligatoria

---

### Sprint 4 — Delegation Model + Waiting Management

**Duración estimada:** ~1 semana  
**Estado:** PLANIFICADO  
**Objetivo:** minimizar la participación operativa de Alexander.  
**Aporta a los gaps:** G4 completo.

**Deliverables:**

- Extensión del modelo `Tarea` con los campos del Delegation Model completo (PHASE 1 §7.2)
- Tool `evaluar_delegacion(user_id, tarea_id)` que aplica el Delegation Decision Test (PHASE 1 §7.3) y sugiere nivel mínimo suficiente
- Tool `follow_up_waiting(user_id)` que revisa Waiting Items con `next_follow_up` vencido y propone acciones
- Ampliación del Brief con secciones "Delegated Work Requiring Supervision" (Review Dates próximas) y "Waiting for Responses" (follow-ups vencidos)
- Meeting Delegation Rule (PHASE 1 §7.4): antes de incluir a Alexander en una reunión propuesta, el sistema evalúa si otro Owner puede resolverla, si basta con recibir un resumen, o si puede prepararse una decisión asíncrona

**Acceptance:**

- **Test C-04** (§12.2): tarea marcada como dependiente de conversación sin persona identificada → `Requires Conversation = Sí`, `Primary Interlocutor = [NO DATA]`, aparece en Missing Information
- Waiting con `next_follow_up` vencido → aparece en Brief con Next Action definido
- **Test PM-02** (§12.4): reunión finalizada sin cerrar todos los asuntos → tareas mantienen su estado activo, Next Action actualizada, no se marcan Completed automáticamente

---

### Sprint 5 — Executive Planning Engine

**Duración estimada:** ~2 semanas  
**Estado:** PLANIFICADO  
**Objetivo:** capacity planning + forecast + reminders proactivos.  
**Aporta a los gaps:** G5 completo.

**Deliverables:**

- Capacity planning: función que calcula capacidad disponible por día y semana teniendo en cuenta horario laboral, eventos, bloques, buffers, entrenamiento y tiempo reservado para imprevistos (PHASE 6 Doc 3 §9)
- Time blocking en 5 categorías (PHASE 6 Doc 3 §10): `Ultra Short` (5-10 min), `Short` (10-30 min), `Medium` (30-60 min), `Deep Work` (60-120 min), `Strategic Block` (>120 min)
- Reminder engine proactivo según prioridad de PHASE 6 Doc 3 §13: personas bloqueadas por CEO, decisiones pendientes, dependencias externas, revisiones comprometidas, riesgos de incumplimiento
- Forecast engine: detección temprana de sobrecarga, saturación del calendario, exceso de reuniones y acumulación de Waiting (§14)
- Integrity Rules ejecutadas antes de cada Brief (§15)
- Weekly Planning (PHASE 6 Doc 3 §5): revisión semanal con resultados, KPI, riesgos, Waiting y trabajo delegado

**Acceptance:**

- El plan diario respeta ≥30% de buffer (regla de la casa)
- Si la carga supera la capacidad, el sistema mueve items menos críticos a `Not Today` y avisa
- El forecast detecta la próxima semana con >100% de carga antes de que llegue el lunes
- **Test CAL-04** (§12.3): dos asuntos con Carlos y varios huecos separados → una única reunión consolidada, sin distribuir conversaciones en dos momentos del día

---

## 5. Fuera del alcance de este roadmap

Explícitamente fuera para evitar deriva:

- **PHASE 5 — Dashboard**: la mini-app actual (`/app`) sirve como interfaz operativa. Un dashboard ejecutivo completo (My Day, Decision Center, Waiting Center, Delegation Center, KPI Dashboard) es una fase entera y depende de que Sprints 2-4 estén cerrados. Se aborda como PHASE 5 cuando toque.
- **PHASE 6 Documents 1, 2, 4**: Executive Core, Decision & Supervision Engine, Executive Intelligence. Se abordan después del Sprint 5.
- **PHASE 7 — Communication Intelligence**: Gmail, WhatsApp, drafting automático de mensajes, seguimiento de respuestas.
- **PHASE 8 — Knowledge & Memory**: knowledge base, SOP library, Company Knowledge Graph, búsqueda semántica.
- **PHASE 9 — KPI Intelligence**: detección automática de desviaciones de KPI, alertas, forecast operativo.
- **PHASE 10 — Strategic Intelligence**: simulación de decisiones, planificación estratégica, gestión de escenarios.
- **Google Calendar directo**: PHASE 3 dice que Google Calendar es la fuente oficial de ocupación. Actualmente Bitrix sincroniza con Google Calendar, así que Bitrix hace de proxy. Migración directa a Google Calendar API queda para futuro si se decide y no bloquea los sprints.

---

## 6. Infraestructura: Railway vs IONOS

Decisión pendiente. El jefe ha planteado migrar a IONOS. Análisis:

### IONOS Deploy Now (el PaaS estilo Railway de IONOS)

**Descartado.** La documentación oficial actualizada de IONOS (agosto 2026) confirma que Deploy Now solo soporta sitios estáticos generados con SSG, single page applications y aplicaciones PHP. Los frameworks detectados automáticamente son de la familia frontend (React, Vue, Next, Astro, Angular, Svelte) más Laravel/Symfony. NO soporta Python. NO soporta procesos long-running. Nuestro `main.py` con `asyncio.gather(bot Telegram + FastAPI)` no tiene sitio ahí.

### IONOS VPS

**Viable con trabajo de operaciones.** Los VPS de IONOS dan root completo sobre Linux (Ubuntu, Debian, AlmaLinux, Rocky) y permiten instalar Docker y desplegar cualquier stack. Nuestro `Dockerfile` funciona sin cambios.

**Comparativa Railway vs IONOS VPS para nuestro workload:**

| Aspecto | Railway (actual) | IONOS VPS |
|---|---|---|
| Modelo | PaaS (git push → deploy) | IaaS (root Linux, tú administras) |
| Deploy | Automático desde GitHub | GitHub Actions + SSH deploy o pull manual |
| HTTPS | Automático | Configurar Let's Encrypt + nginx |
| Env vars | UI del panel | `.env` en servidor o systemd `EnvironmentFile` |
| Escalado | Vertical, click en UI | Cambio de plan requiere reinstall (según docs IONOS) |
| Precio para nuestro perfil | ~€5-20/mes según uso | VPS S+ ~€4-5/mes fijos |
| Ops requerido | Casi cero | Instalar/mantener Docker, nginx, certbot, actualizaciones OS, monitorización |
| Backup | Snapshots automáticos | Servicio opcional adicional |
| Logs | Panel Railway + `logger.py` (Sprint 0) | Docker logs + `logger.py` → Supabase |
| Región de servidores | US/EU | EU (Frankfurt, Logroño) — proveedor europeo |

### Trabajo adicional para migrar a IONOS VPS

1. Provisionar VPS S+ o M con Ubuntu 24.04 LTS
2. Instalar Docker + Docker Compose
3. Configurar nginx como reverse proxy con TLS (Let's Encrypt vía certbot con renovación automática)
4. Systemd service o Docker Compose con `restart: unless-stopped` para auto-restart
5. GitHub Actions workflow: build image → push a GHCR → SSH deploy con `docker compose pull && docker compose up -d`
6. Firewall (ufw) + fail2ban
7. Log shipping: ya lo hacemos con `logger.py` (Sprint 0); Supabase actúa como agregador central
8. Estrategia de backups (servicio snapshot de IONOS opcional, o volcado periódico de Supabase que ya es la fuente de estado)
9. Migrar dominio: `calendar-manager-production-1b2c.up.railway.app` → dominio propio + DNS apuntando a la IP del VPS

### Recomendación

En el estado actual (MVP con un usuario en piloto), Railway sigue siendo la opción coste-efectiva por el tiempo de administración que ahorra. La migración a IONOS aporta valor cuando se cumpla al menos una de estas condiciones:

- Alexander confirma que el sistema pasa de piloto a producción estable
- Existe una razón de compliance/GDPR/soberanía de datos para exigir proveedor y data center europeos (IONOS es alemán, Railway es US)
- El coste mensual de Railway supera claramente el TCO estimado de IONOS incluyendo horas de operaciones

Si el jefe insiste, la migración se puede planificar como **Sprint 0.5** entre Sprint 0 y Sprint 1, con ~2-3 días de trabajo dedicado. Prerrequisitos antes de comprometer la migración:

- Sprint 0 completo (logger.py en Supabase para observabilidad post-migración)
- Snapshot funcional en Railway como fallback si la migración falla
- Dominio propio ya adquirido
- Runbook escrito con los pasos exactos y el plan de rollback

---

## 7. Matriz de trazabilidad PHASE → Sprint

| PHASE / Sección | Cubierto en |
|---|---|
| PHASE 1 §1.4 Conversación Ejecutiva | Sprint 1 (light), Sprint 2-3 (completo) |
| PHASE 1 §2.2 Executive Reasoning Pipeline | Sprint 2-3 (parcial), Sprint 5 (completo) |
| PHASE 1 §2.4 Priority Order | Sprint 3 |
| PHASE 1 §2.5 Conversation Detection Logic | Sprint 1 |
| PHASE 1 §2.6 Meeting Candidate Logic | Sprint 1 |
| PHASE 1 §2.7 Meeting Compatibility Test | Sprint 1 |
| PHASE 1 §3.6 Consolidación por interlocutor | Sprint 1 |
| PHASE 1 §3.7 Asynchronous-First Test | Sprint 1 (light), Sprint 3 (completo) |
| PHASE 1 §4 Executive Brief | Sprint 3 |
| PHASE 1 §5 Daily Workflow | Sprint 3, Sprint 5 |
| PHASE 1 §5.6 CEO Confirmation | Sprint 1 (aplicación en propuestas) |
| PHASE 1 §5.7 Agenda Preparation | Sprint 1 (light), Sprint 3 (completo) |
| PHASE 1 §5.11 Post-Meeting Processing | Sprint 4 |
| PHASE 1 §6 Status Model (8 estados) | Sprint 2 |
| PHASE 1 §7 Delegation Model | Sprint 4 |
| PHASE 1 §8 Work Item Model | Sprint 2 |
| PHASE 1 §10 Integrity Check | Sprint 3, Sprint 5 |
| PHASE 2 §1-3 Conversation Grouping | Sprint 1 |
| PHASE 2 §4 Calendar Proposal | Sprint 1 |
| PHASE 2 §5 CEO Confirmation | Sprint 0 (principio ya presente), Sprint 1 (aplicación) |
| PHASE 2 §6 Agenda Preparation | Sprint 1 (light), Sprint 3 (completo) |
| PHASE 2 §9 Checkpoint Workflow | Sprint 4 |
| PHASE 2 §10 Evening Review | Sprint 4-5 |
| PHASE 2 §11 Waiting Workflow | Sprint 4 |
| PHASE 2 §12 Follow-up Workflow | Sprint 4 |
| PHASE 3 §1 Executive Meetings | Sprint 1 |
| PHASE 3 §2 Automatic Agenda | Sprint 3 |
| PHASE 3 §3 Calendar Slot Selection | Sprint 1 (base existente), Sprint 5 (avanzado) |
| PHASE 3 §4 Context Switching | Sprint 1 |
| PHASE 3 §6 Protected Deep Work | Sprint 1 (bloques no negociables) |
| PHASE 3 §7 Pending Meetings | Sprint 1 |
| PHASE 3 §8 Post Meeting | Sprint 4 |
| PHASE 3 §9 Meeting Intelligence | Sprint 5 |
| PHASE 6 Doc 3 §4 Daily Planning | Sprint 3 |
| PHASE 6 Doc 3 §5 Weekly Planning | Sprint 5 |
| PHASE 6 Doc 3 §9 Capacity Planning | Sprint 5 |
| PHASE 6 Doc 3 §10 Time Blocking | Sprint 5 |
| PHASE 6 Doc 3 §11 Conversation Batching | Sprint 1 |
| PHASE 6 Doc 3 §13 Reminder Engine | Sprint 5 |
| PHASE 6 Doc 3 §14 Forecast Engine | Sprint 5 |
| PHASE 6 Doc 3 §15 Integrity Rules | Sprint 3, Sprint 5 |

---

## 8. Riesgos

**R1 — Distancia arquitectónica mayor de la aparente.** Los PHASE docs describen un sistema conceptualmente muy distinto al MVP actual. Riesgo de infra-estimar sprints, especialmente Sprint 2. Mitigación: acceptance tests concretos por sprint, cortar scope antes que fecha si se sale.

**R2 — Bitrix Tasks API con campos personalizados.** Los campos del Work Item Model (`next_action`, `expected_result`, `escalation_condition`) no tienen equivalente nativo en Bitrix Tasks. Requerirá campos personalizados (`UF_*`). Mitigación: prototipar Sprint 2 con 1-2 días de spike antes de comprometer 2 semanas completas.

**R3 — Consumo de tokens Anthropic.** Cada Executive Brief matutino puede costar 1000-3000 tokens de entrada + salida. Con prompt caching activo y ~22 días laborables/mes, el coste marginal es aceptable, pero conviene medir. Mitigación: registrar cada llamada en `token_usage` como ya se hace, monitorizar el dashboard `/api/usage`.

**R4 — Migración de plataforma (Railway → IONOS).** Si se decide, riesgo de downtime durante la ventana de cambio. Mitigación: entorno IONOS montado y validado ANTES de cambiar DNS. Snapshot final del Railway como fallback disponible al menos 48 horas después de la migración.

**R5 — Deriva del alcance del cliente.** Alexander puede pedir features de PHASE 7-10 (Gmail, KPI intelligence) antes de tener PHASE 1-3 sólidos. Mitigación: este roadmap es explícito sobre qué queda fuera. Cada nueva petición se ubica en un Sprint concreto de este plan o en "fuera de alcance".

**R6 — Regla de secuencia LOCK.** El `ROADMAP.md` establece que las fases se implementan en orden y que un artefacto no puede empezar hasta que el anterior recibe LOCK. PHASE 1 está en `DRAFT FOR LOCK` (no LOCK aún). Si Alexander no aprueba explícitamente la v1.1 de PHASE 1, tenemos deuda formal aunque el trabajo esté hecho. Mitigación: solicitar aprobación expresa del CEO sobre PHASE 1 v1.1 antes de cerrar Sprint 3.

---

## 9. Estado actual del roadmap

| Sprint | Estado | Duración estimada | Dependencia |
|---|---|---|---|
| Sprint 0 | 🟡 EN CURSO | ~1 hora | Ninguna |
| Sprint 1 | ⬜ PLANIFICADO | ~1 semana | Sprint 0 completo |
| Sprint 2 | ⬜ PLANIFICADO | ~2 semanas | Sprint 1 completo |
| Sprint 3 | ⬜ PLANIFICADO | ~1 semana | Sprint 2 completo |
| Sprint 4 | ⬜ PLANIFICADO | ~1 semana | Sprint 3 completo |
| Sprint 5 | ⬜ PLANIFICADO | ~2 semanas | Sprint 4 completo |

**Cierre estimado del roadmap:** ~7-8 semanas desde el arranque de Sprint 1, sin contar ciclos de feedback y ajustes intermedios con Alexander.

---

## 10. Referencias

- `PHASE1_ES_ACTUALIZADA.md` (v1.1, DRAFT FOR LOCK)
- `PHASE2_ES.md`
- `PHASE3_GOOGLE_CALENDAR_ES.md`
- `PHASE_6_DOCUMENT_3_EXECUTIVE_PLANNING_ENGINE.md` (v1.2, LOCK)
- `ROADMAP.md`
- Documentación técnica del MVP actual: `README.md`, `main.py`, `agent.py`, `tools.py`, `models.py`

---

**Aprobación pendiente de Alexander Kolobnev, CEO Syncrosfera.**
