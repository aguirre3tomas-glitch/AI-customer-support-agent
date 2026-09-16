# Plan — Project 2: Customer Support AI Agent (cd14763)

Fuentes cruzadas: página de Prerequisites del classroom + README del starter repo.
Última revisión: 2026-09-15.

## Estado actual

- [x] Repo propio: `github.com/aguirre3tomas-glitch/AI-customer-support-agent` (espejo del starter en `starter/`)
- [x] Entorno local: uv 0.12.5, venv Python 3.14.7, dependencias resueltas (`uv.lock` commiteado)
- [x] Node.js 24 (para MCP Inspector)
- [ ] **Credenciales del nuevo lab** (Udacity → Cloud Resources) ← bloqueante para arrancar

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

## Fase 3 — Deploy a AgentCore

```bash
agentcore configure     # primera vez
agentcore deploy
agentcore invoke '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "test-1"}'
```

## Fase 4 — Testing funcional (6 escenarios)

| # | Prompt | Esperado |
|---|---|---|
| 1 | track order ORD-001 | tracking TRK987654321, carrier UPS, entrega estimada |
| 2 | refund ORD-002 (Kindle) | refund ID, APPROVED, 3-5 días |
| 3 | beneficios Platinum | same-day shipping, 15% discount, priority support |
| 4 | memoria: sesión A "I am Jane, prefer concise" → sesión B "remember my name?" | recuerda Jane + preferencia |
| 5 | Gold 4250 pts, $150 | points redeemed, 10% tier, total final, points restantes |
| 6 | browser: amazon.com title | título real de la página |

## Fase 5 — CloudWatch

Log group del Runtime → metric filter `ERROR` → alarma si errores > 5 en 5 min → **screenshot de la alarma**.

## Fase 6 — Entrega

- [ ] `main.py` con los 8 TODOs completos
- [ ] Screenshots/outputs de los 6 tests
- [ ] Screenshot de la alarma CloudWatch
- [ ] Reflexión escrita 200–400 palabras: 1 decisión de diseño / 1 desafío / extensión a producción

## Discrepancias detectadas entre las fuentes

- **Modelo**: la página dice `amazon.nova-2-lite-v1:0`, el README dice `amazon.nova-lite-v1:0`, y el código usa `global.amazon.nova-2-lite-v1:0` → manda el código; verificar Model access en Bedrock igual.
- **Python**: README pide 3.14+ (tenemos 3.14.7 ✓); la página decía 3.13+.
- **AWS CLI**: piden v2; local tenemos v1.46 (suficiente por ahora; el trabajo lo hacen boto3 y el toolkit de AgentCore).
- **Rutas**: el README referencia `solution/...` para `lambda_schema` y `product_catalog.txt`; en el starter están en `starter/` (los tenemos).
- **RUBRIC.md**: mencionado en el README pero no vino en el clone público (estará en el classroom, como en el P1).

## Costos / cleanup (al terminar)

Estimado <$15. Borrar: Gateway, Knowledge Base, Memory, Lambdas, API Gateway, bucket S3 y el Runtime del agente.
