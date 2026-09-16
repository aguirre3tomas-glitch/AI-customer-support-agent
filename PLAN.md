# Plan — Project 2: Customer Support AI Agent (cd14763)

Fuentes cruzadas: página de Prerequisites del classroom + README del starter repo + **Instructions.md** (nueva, define deploy/tests/cleanup/entrega).
Última revisión: 2026-09-16.

## Estado actual

- [x] Repo propio: `github.com/aguirre3tomas-glitch/AI-customer-support-agent` (espejo del starter en `starter/`)
- [x] Entorno local: uv 0.12.5, venv Python 3.14.7, dependencias resueltas (`uv.lock` commiteado)
- [x] Node.js 24 (para MCP Inspector)
- [x] AWS CLI **v2.36.46** instalada (reemplaza la v1 que quedó en el venv del P1)
- [x] **Credenciales del lab** — funcionan desde la máquina local (el token viejo era el problema; se transfirió el correcto desde la VM con `fold -w 76`). ⚠️ Al cerrarse la sesión, Vocareum revoca todo (`voc-cancel-cred`) → recopiar el trío al reiniciar el lab

## Progreso Fase 1

- [x] **Lambdas (2)** — `order-tracker` + `refund-processor` desplegadas (Python 3.12) y probadas: ORD-001 → `TRK987654321`/UPS; refund → `REF-…`/`APPROVED`. Rol de ejecución: `csai-lambda-basic-role` · Script: `scripts/deploy_lambdas.py`
- [x] **REST API** — `customer-support-api` (id `wah32tfea3`), stage `prod`, 3 GET con operation names (`get_order`, `get_customer_orders`, `get_customer`) + integración proxy → `order-tracker` · Script: `scripts/setup_gateway_infra.py`
- [x] **AgentCore Gateway** — `CustomerSupportGateway` (id `customersupportgateway-4grevewyco`) estado READY · authorizer NONE · rol `csai-gateway-role`
- [x] **Targets del Gateway** — `order-tracker` (API GW) + `refund-processor` (Lambda) — ambos **READY**; 6 tools verificadas con MCP Inspector
  - ⚠️ Bug encontrado y arreglado: el script tomaba la raíz del REST API por posición (`items[0]`) en vez de por path → creó un subárbol duplicado (`/orders/orders/...`) que rompía el parser OAS del Gateway (`operationId repeated`). Además los métodos por CLI no llevaban method response → el OAS exportado no tenía `responses` (obligatorio). Reparado con `scripts/repair_rest_api.py` (poda + method response 200 + redeploy).
- [x] **Verificación end-to-end** — `tools/call` vía MCP al Gateway: `get_order` devuelve tracking real; `initiate_refund` crea refund APPROVED
- [x] **Knowledge Base** `CustomerSupportKB` (id `PGV0IQVSWO`) — tipo **MANAGED** (Bedrock gestiona el vector store)
  - ⚠️ El lab **deniega `aoss:*` y `s3vectors:*`** → imposible la colección OpenSearch auto-creada que describe el curso. Alternativa: KB *managed* (Bedrock provisiona el vector store internamente, sin OpenSearch). Mismo `retrieve()` desde el código.
  - Bucket `csai-kb-032189013563-us-east-1` con `product_catalog.txt` · data source S3 (`JCVYZS5U8G`) · ingestión COMPLETE (1 doc, 1 indexado) · rol `csai-kb-role`
  - Nota: para KBs managed, `retrieve()` **no acepta** `vectorSearchConfiguration` (usar `managedSearchConfiguration`). El default del starter funciona tal cual.
- [x] **Memory** `CustomerSupportMemory` (`CustomerSupportMemory-HQ2KtBHkEs`) — ACTIVE, estrategias `customer_facts` (SEMANTIC) y `customer_preferences` (USER_PREFERENCE) con sus namespaces
- [x] **`main.py`** — los 8 TODOs implementados y probados por componentes: namespaces OK, KB retrieve OK (devuelve política 15 días), code interpreter OK ($99 / 4000 pts / 400 restantes), memoria OK (evento guardado + extracción + retrieval)
- [x] **Agente local end-to-end** — Test 1 responde con tracking real vía Gateway (`order-tracker___get_order`)
- [ ] **Deploy a AgentCore** (`agentcore configure` + `agentcore deploy`) — en curso
  - El toolkit requiere un binario `zip` (chequeo de capacidad) → shim en `~/bin/zip.cmd` + PATH
  - Runtime: `PYTHON_3_13` (el CLI no acepta 3_14; el servicio sí) · `direct_code_deploy` · linux/arm64 · memory del toolkit desactivado (usamos la nuestra)
- [ ] **6 tests funcionales** con `agentcore invoke` (evidencia de entrega)

> Los IDs reales se guardan en `infra_config.json` (gitignored; se regenera al correr los scripts).

## Fase 1 — Infraestructura AWS (todo en us-east-1)

1. **Lambdas (2)** — consola Lambda, runtime Python 3.12, código tal cual de `starter/lambda/` (no se modifica):
   - `order-tracker` · `refund-processor`
   - Rol de ejecución con permisos básicos (CloudWatch Logs)
   - ➡️ Anotar ARN de cada una
2. **API Gateway REST API** (para order-tracker):
   - Resources + métodos GET: `/orders/{order_id}`, `/customers/{customer_id}/orders`, `/customers/{customer_id}`
   - Lambda proxy integration → `order-tracker`
   - Operation names EXACTOS: `get_order`, `get_customer_orders`, `get_customer`
   - Deploy a stage `prod`
   - ➡️ Anotar REST API ID + stage
3. **AgentCore Gateway `CustomerSupportGateway`** (authorizer NONE):
   - Target `order-tracker` (tipo *API Gateway REST API*): las 3 operaciones del paso 2
   - Target `refund-processor` (tipo *Lambda*): tool schema desde `starter/lambda/lambda_schema`
   - ➡️ Anotar Gateway URL (termina en `/mcp`)
4. **Knowledge Base `CustomerSupportKB`**:
   - Subir `starter/product_catalog.txt` a un bucket S3
   - Titan Embeddings v2 + OpenSearch Serverless (auto-creado)
   - Sync del data source
   - ➡️ Anotar KB_ID · Test: *"What is the return policy for electronics?"* → ventana de 15 días
5. **Memory `CustomerSupportMemory`** — 2 estrategias:
   - `customer_facts` (SEMANTIC) → namespace `cs_agent/{actorId}/facts`
   - `customer_preferences` (USER_PREFERENCE) → namespace `cs_agent/{actorId}/preferences`
   - ➡️ Anotar MEMORY_ID

## Fase 2 — Código: 8 TODOs en `starter/main.py`

| TODO | Qué implementar |
|---|---|
| 1 | `app = BedrockAgentCoreApp()` |
| 2 | Completar `GATEWAY_URL`, `KB_ID`, `REGION`, `MEMORY_ID` (de Fase 1) |
| 3 | `BedrockModel` (modelo `global.amazon.nova-2-lite-v1:0`) + `MemoryClient` + cliente boto3 `bedrock-agent-runtime` |
| 4 | `get_namespaces()` → dict `{tipo_strategy: namespace}` vía `get_memory_strategies` |
| 5 | `MemoryHook`: retrieve (prepend "Customer Context:") / save (`create_event`) / register Hooks |
| 6 | `search_knowledge_base()` → `retrieve()` de la KB, chunks unidos con `"\n---\n"` |
| 7 | `calculate_loyalty_discount()` → código Python en Code Interpreter (`code_session`), fallback solo tier |
| 8 | `invoke()` entrypoint: payload → MemoryHook + Browser + tools del Gateway (MCP) → Agent → respuesta |

Tareas del curso: Task 1 (TODOs 1-3) · Task 2 (TODO 4) · Task 3 (TODO 5) · Task 4 (TODO 6) · Task 5 (TODO 7) · Task 6 (TODO 8) · Task 7 (deploy + tests) · Task 8 (reflexión).

## Fase 3 — Deploy a AgentCore

```bash
# crea .bedrock_agentcore.yaml (dice qué archivo tiene el entrypoint y cómo nombrar los recursos)
agentcore configure --entrypoint main.py --name customer-support-agent
agentcore deploy
agentcore invoke '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "test-1"}'
```

## Fase 4 — Testing funcional (6 escenarios)

| # | Prompt | Esperado |
|---|---|---|
| 1 | track order ORD-001 | tracking TRK987654321, carrier UPS, entrega estimada |
| 2 | refund ORD-002 (Kindle) | refund ID, APPROVED, "3-5 business days" |
| 3 | beneficios Platinum | same-day shipping, 15% discount, priority support |
| 4 | memoria: sesión A `"Hi, I am Jane. I prefer concise responses."` → **esperar ≥30s** → sesión B `"Do you remember my name and communication preference?"` | recuerda Jane + preferencia |
| 5 | Gold 4250 pts, $150 standard | points redeemed, 10% tier, total final, points restantes |
| 6 | browser: **udacity.com** title | título real de la página |

## Fase 5 — CloudWatch (solo en README viejo)

Log group del Runtime → metric filter `ERROR` → alarma si errores > 5 en 5 min → screenshot.
> ⚠️ Instructions.md NO lo pide en la entrega: hacerlo como extra barato que cubre la versión vieja también.

## Fase 6 — Entrega (checklist de Instructions.md)

- [x] `main.py` completo — **sin `pass` ni `None` colgados** (los 8 TODOs implementados; `SUBMISSION.md` mapea cada uno)
- [x] Outputs de los 6 tests (Test 4 = ambas sesiones) en `evidence/test*.txt`
- [x] Reflexión escrita 200–400 palabras: (1) decisión de diseño, (2) desafío y resolución, (3) extensión a producción — en `SUBMISSION.md`
- [x] Extra: alarma CloudWatch — `evidence/cloudwatch-alarm.txt`

## Stand-outs sugeridos (opcionales)

- Validación de output estructurado con **Pydantic** (order status, discount breakdown)
- **Summarización** de historial al exceder presupuesto de tokens
- Personalizar el dominio manteniendo la arquitectura

## Discrepancias detectadas entre las fuentes

- **Modelo**: página `amazon.nova-2-lite-v1:0` · README `amazon.nova-lite-v1:0` · código `global.amazon.nova-2-lite-v1:0` → **manda el código**; verificar Model access igual.
- **Python**: README pide 3.14+ (tenemos 3.14.7 ✓); la página decía 3.13+.
- **AWS CLI**: piden v2 → **instalada v2.36.46**.
- **Test 6**: Instructions pide **udacity.com**; el expected quedó desactualizado ("live Amazon.com page").
- **CloudWatch**: estaba en el checklist del README viejo, no en el nuevo de Instructions.
- **Deploy**: el README decía `agentcore configure` a secas; Instructions especifica `--entrypoint main.py --name <nombre>` (genera `.bedrock_agentcore.yaml`).
- **Rutas**: el README referencia `solution/...` para `lambda_schema` y `product_catalog.txt`; en el starter están en `starter/`.
- **RUBRIC.md**: mencionado en el README pero no vino en el clone público (estará en el classroom, como en el P1).

## Costos / cleanup (al terminar)

Estimado <$15. Pasos (según Instructions):
1. `agentcore destroy` — borra el deployment del Runtime
2. Consola Bedrock → AgentCore: borrar **Gateway** y **Memory**; Knowledge Bases: borrar la **KB**
3. OpenSearch Serverless → colecciones: borrar la auto-creada · S3: vaciar y borrar el bucket
4. API Gateway: borrar la REST API · Lambda: borrar ambas funciones
5. (Opcional) IAM: borrar los roles autogenerados
6. ⚠️ **Borrar las credenciales de `~/.aws` al final del proyecto — preguntar al usuario antes** (pedido explícito)
