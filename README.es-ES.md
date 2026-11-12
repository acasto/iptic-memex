

# Iptic Memex

Iptic Memex es un entorno de trabajo para LLM basado en línea de comandos. Soporta modos de chat por CLI, completado, agente, TUI y Web, además de un sistema de herramientas flexible, RAG y hooks opcionales para análisis complementario.

El nombre es una referencia al Memex, un dispositivo descrito por Vannevar Bush en su ensayo de 1945 "As We May Think". Ver: https://en.wikipedia.org/wiki/Memex

![Iptic Memex demo](https://i.imgur.com/XLJ4AuY.gif)

---

## Destacados

- Múltiples modos de interacción: chat, completado, agente, TUI y Web.
- Cargar y resumir archivos locales (pdf/docx/xlsx/pptx/msg/audio/imágenes) y contenido web.
- Herramientas integradas (file, cmd, websearch, ragsearch, memory, persona_review).
- Generación Aumentada por Recuperación (RAG) con índices locales.
- Amplio soporte de proveedores (OpenAI, Anthropic, Gemini, OpenRouter y más).
- Hooks opcionales para análisis y memoria pre/post turno.
- Persistencia de sesiones con guardado automático, reanudación y puntos de control.

---

## Inicio rápido

Requisitos: Python 3.11+.

```bash
git clone https://github.com/acasto/iptic-memex.git
cd iptic-memex
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Establezca las claves de API en `config.ini` (o `~/.config/iptic-memex/config.ini`) o mediante variables de entorno como `OPENAI_API_KEY`.

Ejecute el modo de chat:

```bash
python main.py chat
```

Ejemplo de ejecución única (completado):

```bash
echo "What is PI?" | python main.py -f -
```

Consejo: Consulte `docs/getting-started.md` para dependencias específicas de la plataforma.

---

## Documentación

Comience aquí para obtener el resto de los detalles de la plataforma:

- [Índice de documentación](docs/README.md)
- [Inicio rápido](docs/getting-started.md)
- [Modos](docs/modes.md)
- [Referencia de CLI](docs/cli.md)
- [Herramientas](docs/tools.md)
- [Prompts](docs/prompts.md)
- [Plantillas](docs/templates.md)
- [Hooks](docs/hooks.md)
- [Ejecutores](docs/runners.md)
- [Sesiones](docs/sessions.md)
- [RAG](docs/rag.md)
- [MCP](docs/mcp.md)
- [Habilidades](docs/skills.md)
- [Sandbox y directorio base](docs/sandbox.md)
- [Agentes](docs/agents.md)
- [Proveedores](docs/providers.md)
- [Registros](docs/logging.md)
- [Notas sobre Web y TUI](docs/web.md)
