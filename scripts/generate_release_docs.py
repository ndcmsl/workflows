#!/usr/bin/env python3
"""
generate_release_docs.py
========================
Genera documentación de release contextualizada para IA a partir del
historial de commits y diffs de código.

Se ejecuta dentro del reusable workflow generate-docs.yml.
Vive en el repo ndcmsl/workflows — NUNCA en repos de producto.

Uso:
    python scripts/generate_release_docs.py \
        --commits /tmp/commits.txt \
        --diff-stat /tmp/diff_stat.txt \
        --diff /tmp/diff.txt \
        --docs-dir /path/to/repo/__documentacion \
        --out-dir /path/to/repo/__documentacion/releases \
        --model gpt-4o
"""

import argparse
import os
import sys
import re
from datetime import datetime
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    print("ERROR: paquete 'openai' no instalado. Ejecuta: pip install openai")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
MAX_DIFF_CHARS = 80_000       # ~20k tokens aprox — evita exceder contexto
MAX_DIFF_STAT_CHARS = 20_000
MAX_COMMITS_CHARS = 10_000
MAX_CONTEXT_CHARS = 6_000


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def read_file_safe(path: str | Path, max_chars: int | None = None) -> str:
    """Lee un fichero, opcionalmente truncando el contenido."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if max_chars and len(content) > max_chars:
            content = (
                content[:max_chars]
                + f"\n\n... [TRUNCADO — {len(content):,} chars total, "
                f"mostrando primeros {max_chars:,}]"
            )
        return content
    except FileNotFoundError:
        return ""


def sanitize_filename(name: str) -> str:
    """Elimina caracteres no válidos para nombres de fichero."""
    return re.sub(r"[^\w\-.]", "_", name)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
def build_prompt(
    commits: str,
    diff_stat: str,
    diff: str,
    existing_context: str,
    today: str,
) -> str:
    """Construye el prompt para el modelo de IA."""

    return f"""Eres un ingeniero de documentación técnica para una plataforma e-commerce
basada en PrestaShop 1.6 fuertemente customizado (3 verticales: SKLUM, Create-Store, Themasie).

Tu tarea: generar documentación de release **concisa y útil** para que asistentes de IA
(como tú) entiendan rápidamente qué ha cambiado en el proyecto.

─── CONTEXTO DEL PROYECTO ───
{existing_context}

─── COMMITS DESDE LA ÚLTIMA RELEASE ───
{commits}

─── RESUMEN DE FICHEROS CAMBIADOS (git diff --stat) ───
{diff_stat}

─── DIFF DE CÓDIGO (puede estar truncado) ───
{diff}

─── INSTRUCCIONES DE GENERACIÓN ───

Genera un documento Markdown con **esta estructura exacta**:

# Release Notes — {today}

## Resumen ejecutivo
[2-3 frases describiendo los cambios principales de esta release]

## Cambios por área

### Core Framework (`/core`)
[Lista de cambios en controllers, services, datalayers, managers del framework core.
Si no hay cambios, escribe "Sin cambios en esta release."]

### Clases y Overrides PrestaShop (`/classes`, `/override`)
[Cambios en /classes/, /override/classes/, /override/controllers/.
Si no hay cambios, escribe "Sin cambios en esta release."]

### Controllers (`/controllers`)
[Cambios en controllers front y admin nativos de PrestaShop.
Si no hay cambios, escribe "Sin cambios en esta release."]

### Templates y Themes (`/themes`)
[Cambios en templates .tpl, assets CSS/JS de themes.
Indica si afecta a una vertical específica (skl_v2, ikh_v3, smb) o a default-bootstrap.
Si no hay cambios, escribe "Sin cambios en esta release."]

### Base de datos
[Cambios en esquemas SQL, migraciones, nuevas tablas/columnas.
Si no hay cambios, escribe "Sin cambios en esta release."]

### Configuración (`/config`, `/core/config`)
[Cambios en ficheros de configuración, AutoLoad.json, etc.
Si no hay cambios, escribe "Sin cambios en esta release."]

### Módulos (`/modules`)
[Cambios en módulos de PrestaShop.
Si no hay cambios, escribe "Sin cambios en esta release."]

### Otros
[Cualquier otro cambio relevante que no encaje en las categorías anteriores]

## Impacto en verticales
[Indica si los cambios afectan a las 3 verticales (SKLUM / Create-Store / Themasie)
o solo a alguna específica. Justifica brevemente.]

## Contexto para IA
[Notas importantes que un asistente de IA debería saber tras estos cambios:
- Nuevas clases registradas en AutoLoad.json
- Cambios en el flujo de ejecución
- Nuevos patrones o convenciones introducidos
- APIs modificadas o deprecadas
- Feature flags nuevos
- Cambios en la estructura de directorios]

## Ficheros clave modificados
[Lista de los ficheros más relevantes, agrupados por directorio.
No incluyas más de 30 ficheros — prioriza los más importantes.]

─── REGLAS ───
- Escribe en español
- Sé conciso pero preciso
- NO inventes cambios que no aparezcan en el diff/commits
- Si el diff está truncado, indícalo y trabaja solo con la información disponible
- Presta especial atención a cambios en AutoLoad.json (nuevas clases registradas)
- Destaca cambios que afecten al flujo de ejecución o a la API
- Si detectas feature flags nuevos, menciónalos explícitamente
- Usa listas con viñetas, no párrafos largos
"""


# ---------------------------------------------------------------------------
# Generación
# ---------------------------------------------------------------------------
def generate_docs(args: argparse.Namespace) -> None:
    """Función principal de generación."""

    # 1. Leer inputs ---------------------------------------------------------
    commits = read_file_safe(args.commits, MAX_COMMITS_CHARS)
    diff_stat = read_file_safe(args.diff_stat, MAX_DIFF_STAT_CHARS)
    diff = read_file_safe(args.diff, MAX_DIFF_CHARS)

    if not commits.strip():
        print("⚠ No se encontraron commits. Saltando generación de documentación.")
        return

    # 2. Leer contexto existente del proyecto --------------------------------
    docs_dir = Path(args.docs_dir)
    existing_context = read_file_safe(
        docs_dir / "00_CONTEXTO_RAPIDO_IA.md", MAX_CONTEXT_CHARS
    )

    # 3. Construir prompt ----------------------------------------------------
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = build_prompt(commits, diff_stat, diff, existing_context, today)

    # 4. Llamar a OpenAI -----------------------------------------------------
    client = OpenAI()  # usa OPENAI_API_KEY del entorno

    print(f"🤖 Generando documentación con modelo '{args.model}'...")
    response = client.chat.completions.create(
        model=args.model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Eres un experto en documentación técnica de proyectos "
                    "PHP/PrestaShop. Generas documentación clara, concisa y "
                    "útil para contextualizar asistentes de IA."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=4096,
    )

    doc_content = response.choices[0].message.content
    if not doc_content:
        print("❌ El modelo no devolvió contenido. Abortando.")
        sys.exit(1)

    # 5. Escribir release doc ------------------------------------------------
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    release_file = out_dir / f"{today}_release.md"

    # Si ya existe (varios pushes el mismo día), añadir contador
    counter = 1
    while release_file.exists():
        counter += 1
        release_file = out_dir / f"{today}_release_{counter}.md"

    release_file.write_text(doc_content, encoding="utf-8")
    print(f"✅ Release doc escrito en: {release_file}")

    # 6. Actualizar CHANGELOG_AI.md ------------------------------------------
    update_changelog(docs_dir, doc_content)

    # 7. Actualizar índice ---------------------------------------------------
    update_index(docs_dir, release_file.name)


def update_changelog(docs_dir: Path, doc_content: str) -> None:
    """Actualiza o crea el CHANGELOG_AI.md acumulativo."""

    changelog_path = docs_dir / "CHANGELOG_AI.md"
    separator = "\n\n---\n\n"
    new_entry = doc_content.strip()

    if changelog_path.exists():
        existing = changelog_path.read_text(encoding="utf-8")
        # Insertar la nueva entrada justo después del header (antes del primer ---)
        header_marker = "\n---\n"
        first_sep = existing.find(header_marker)
        if first_sep != -1:
            header = existing[: first_sep + len(header_marker)]
            body = existing[first_sep + len(header_marker) :]
            updated = header + "\n" + new_entry + separator + body
        else:
            updated = existing + separator + new_entry
    else:
        updated = (
            "# Changelog para IA — Plataforma SKLUM\n\n"
            "> Histórico de cambios generado automáticamente para contextualizar "
            "asistentes de IA.\n"
            "> Cada entrada corresponde a un push en master.\n"
            "> Las entradas más recientes aparecen primero.\n\n"
            "---\n\n"
            + new_entry
            + "\n"
        )

    changelog_path.write_text(updated, encoding="utf-8")
    print(f"✅ Changelog actualizado: {changelog_path}")


def update_index(docs_dir: Path, release_filename: str) -> None:
    """Añade la entrada de release al índice de documentación."""

    index_path = docs_dir / "00_INDICE_DOCUMENTACION.md"

    if not index_path.exists():
        print("⚠ Fichero de índice no encontrado, saltando actualización del índice.")
        return

    content = index_path.read_text(encoding="utf-8")

    # Evitar duplicados
    if release_filename in content:
        print(f"ℹ {release_filename} ya está en el índice. Saltando.")
        return

    new_row = (
        f"| [{release_filename}](releases/{release_filename}) "
        f"| Release notes generadas automáticamente |"
    )

    if "### Releases" not in content and "### releases" not in content:
        # Crear sección de releases
        releases_section = (
            "\n### Releases auto-generadas (`releases/`)\n\n"
            "| Documento | Descripción |\n"
            "|-----------|-------------|\n"
            f"{new_row}\n"
            "| [CHANGELOG_AI.md](CHANGELOG_AI.md) "
            "| Histórico acumulativo de todas las releases |\n\n"
        )
        # Insertar antes de "## Convenciones" o al final
        if "## Convenciones" in content:
            content = content.replace(
                "## Convenciones", releases_section + "## Convenciones"
            )
        else:
            content += "\n" + releases_section
    else:
        # Añadir fila a la tabla existente de releases
        lines = content.split("\n")
        insert_idx = None
        in_releases = False

        for i, line in enumerate(lines):
            if "###" in line and "releases" in line.lower():
                in_releases = True
            elif in_releases and line.startswith("|") and "---" not in line:
                insert_idx = i  # última fila de la tabla
            elif in_releases and not line.startswith("|") and line.strip() == "":
                break

        if insert_idx is not None:
            lines.insert(insert_idx + 1, new_row)
            content = "\n".join(lines)

    index_path.write_text(content, encoding="utf-8")
    print(f"✅ Índice actualizado: {index_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Genera documentación de release contextualizada para IA"
    )
    parser.add_argument(
        "--commits",
        required=True,
        help="Ruta al fichero con lista de commits",
    )
    parser.add_argument(
        "--diff-stat",
        required=True,
        help="Ruta al fichero con salida de git diff --stat",
    )
    parser.add_argument(
        "--diff",
        required=True,
        help="Ruta al fichero con salida de git diff",
    )
    parser.add_argument(
        "--docs-dir",
        required=True,
        help="Ruta al directorio de documentación existente",
    )
    parser.add_argument(
        "--out-dir",
        required=True,
        help="Directorio de salida para release docs",
    )
    parser.add_argument(
        "--model",
        default="gpt-4o",
        help="Modelo de OpenAI a usar (default: gpt-4o)",
    )

    args = parser.parse_args()
    generate_docs(args)


if __name__ == "__main__":
    main()
