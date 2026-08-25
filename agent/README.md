# Self-correcting coding agent — plan / act / check / iterate

A small, real agent built with the raw Anthropic API (not Claude Code) that
implements `records_core.py` from `spec.md`, verifying its own work against a
real pytest suite instead of self-reporting success.

## Por qué existe esto

Mapea directo a tres líneas del puesto:

> A habit of designing for reliability, observability, and correctness at scale.
> You build with AI coding agents every day.
> You design agentic workflows and self-correcting loops — plan, act, check,
> iterate — instead of one-shot prompting, so the work actually reaches "done."

La diferencia entre esto y "le pido código a Claude" es el **check objetivo**:
`run_agent.py` nunca confía en que el modelo *diga* que terminó. El único
criterio de éxito es que `run_tests()` — que corre pytest de verdad, en un
subproceso — reporte `PASSED`. Si el modelo dice "listo" pero los tests
fallan, el harness sigue iterando. Si se agota el presupuesto de iteraciones
(`MAX_ITERATIONS = 6`), reporta la falla honestamente en vez de fingir éxito.

## Cómo mapea el loop

| Fase | Dónde está |
|---|---|
| **Plan** | El razonamiento de Claude (`thinking: adaptive`) entre llamadas a herramientas, guiado por `spec.md` — un contrato explícito y testeable, no un prompt vago ("hacelo bien"). Esto es lo que el puesto llama "context engineering". |
| **Act** | La tool `write_file`, la única forma que tiene el modelo de modificar `records_core.py`. |
| **Check** | La tool `run_tests` — corre pytest real vía `subprocess`, devuelve el output crudo. El modelo no puede alucinar un "pasó" — o el exit code es 0, o no. |
| **Iterate** | El `for` en `run_agent.py`: cada vuelta manda el resultado real de los tests de vuelta al modelo. Acotado por `MAX_ITERATIONS`, con reporte honesto si no converge. |

## Setup

Esta máquina no tiene Python instalado — hace falta antes de correr esto:

1. Instalar [Python 3.10+](https://www.python.org/downloads/).
2. Instalar dependencias:
   ```bash
   pip install -r requirements.txt
   ```
3. Conseguir una API key de Anthropic en [console.anthropic.com](https://console.anthropic.com)
   (distinta de tu login de Claude.ai/Claude Code — esta es de la API paga por uso)
   y setearla:
   ```bash
   export ANTHROPIC_API_KEY=sk-ant-...
   ```

## Correr

```bash
python run_agent.py
```

Vas a ver, iteración por iteración: qué herramienta llama Claude, qué le
devuelve el harness, y cuándo (si) `run_tests` reporta `PASSED`. Costo
esperado: unos centavos de USD — es una tarea chica con pocas iteraciones.

## Qué mirar en la salida (para la entrevista)

- La **primera** corrida de `run_tests` va a fallar (el archivo empieza vacío)
  — eso es intencional, para que el loop tenga trabajo real que hacer.
- Si Claude escribe una implementación con un bug (por ejemplo, no genera un
  id único, o no es idempotente), vas a ver el output real de pytest
  mostrando exactamente qué assertion falló — y la siguiente iteración de
  Claude reaccionando a ese output específico, no a una re-explicación tuya.
- El harness nunca dice "éxito" salvo que el string `PASSED` venga de pytest
  mismo. Ese es el punto central para explicar en la entrevista: el "check"
  tiene que ser una fuente de verdad independiente del modelo, no el modelo
  auto-evaluándose.

## Cómo esto se conecta con el resto del proyecto

`spec.md` documenta la misma regla de idempotencia que implementamos en
[`../processor-service/main.py`](../processor-service/main.py) contra
reentregas de Pub/Sub (`ON CONFLICT (id) DO NOTHING`). Acá está aislada en un
módulo puro, sin GCP, para que el loop del agente sea rápido y barato de
correr — la misma razón por la que en ingeniería real conviene separar lógica
de negocio de infraestructura: se vuelve testeable sin levantar Cloud SQL.
