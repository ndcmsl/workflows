#!/usr/bin/env python3
"""
generate_release_docs.py
========================
Genera documentación de release contextualizada para IA a partir del
historial de commits y diffs de código.

Se ejecuta dentro del reusable workflow generate-docs.yml.
Vive en el repo ndcmsl/workflows — NUNCA en repos de producto.

REGLA CRÍTICA: Solo documenta ficheros que aparecen LITERALMENTE en el
diff-stat. Cualquier mención a un fichero no modificado es un error.

Uso:
    python scripts/generate_release_docs.py \
        --commits /tmp/commits.txt \
        --diff-stat /tmp/diff_stat.txt \
        --diff /tmp/diff.txt \
        --file-list /tmp/file_list.txt \
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


def extract_files_from_diff_stat(diff_stat: str) -> list[str]:
    """Extrae la lista de ficheros del output de git diff --stat."""
    files = []
    for line in diff_stat.strip().split("\n"):
        line = line.strip()
        if not line or ("changed" in line and ("insertion" in line or "deletion" in line)):
            continue
        # git diff --stat format: " path/to/file | 5 +++--"
        match = re.match(r"^\s*(.+?)\s*\|", line)
        if match:
            files.append(match.group(1).strip())
    return files


def validate_output(doc_content: str, allowed_files: list[str]) -> tuple[bool, list[str]]:
    """Valida que la documentación generada no mencione ficheros no modificados.

    Retorna (is_valid, list_of_violations).
    Solo verifica paths con extensión (no carpetas genéricas).
    """
    violations = []
    # Buscar paths que parecen ficheros (contienen extensión)
    mentioned_paths = re.findall(r'`([^`]*\.\w{1,5})`', doc_content)

    for mentioned in mentioned_paths:
        mentioned_clean = mentioned.strip().lstrip("./")
        # Solo validar si parece un path relativo del proyecto
        if "/" not in mentioned_clean:
            continue
        # No validar paths genéricos / de ejemplo
        if mentioned_clean.startswith("themes/{") or "{" in mentioned_clean:
            continue
        # Comprobar si está en los ficheros permitidos
        found = any(
            mentioned_clean in f or f.endswith(mentioned_clean)
            for f in allowed_files
        )
        if not found:
            violations.append(mentioned_clean)

    return len(violations) == 0, violations


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
def build_prompt(
    commits: str,
    diff_stat: str,
    diff: str,
    file_list: str,
    existing_context: str,
    today: str,
) -> str:
    """Construye el prompt para el modelo de IA."""

    return f"""Eres un ingeniero de documentación técnica para una plataforma e-commerce
basada en PrestaShop 1.6 fuertemente customizado (3 verticales: SKLUM, Create-Store, Themasie).

Tu tarea: generar documentación de release **concisa y útil** para que asistentes de IA
(como tú) entiendan rápidamente qué ha cambiado en el proyecto.

════════════════════════════════════════════════════════════════════════════════
 ██  REGLA ABSOLUTA — LEE ESTO ANTES QUE NADA  ██
════════════════════════════════════════════════════════════════════════════════

Los ÚNICOS ficheros que han cambiado en esta release son los que aparecen en
la sección "LISTA EXACTA DE FICHEROS MODIFICADOS" de abajo.

- PROHIBIDO mencionar, describir o insinuar cambios en ficheros que NO estén
  en esa lista.
- PROHIBIDO inventar, deducir o suponer cambios que no aparezcan EXPLÍCITAMENTE
  en el diff proporcionado.
- Si una sección no tiene ficheros modificados, escribe EXACTAMENTE:
  "Sin cambios en esta release."
- En "Ficheros modificados" lista SOLO ficheros de la lista proporcionada.
- En "Contexto para IA" describe SOLO lo que se ve en el diff real.
- Si el diff está truncado, indica que la información es parcial y NO inventes
  el contenido que falta.

Si violas alguna de estas reglas, la documentación será rechazada.

════════════════════════════════════════════════════════════════════════════════

─── CONTEXTO DEL PROYECTO (solo para referencia, NO para inventar cambios) ───
{existing_context}

─── LISTA EXACTA DE FICHEROS MODIFICADOS (FUENTE DE VERDAD) ───
{file_list}

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
[2-3 frases describiendo los cambios principales. Basarte SOLO en los commits
y ficheros listados arriba.]

## Cambios por área

### Core Framework (`/core`)
[Lista de cambios SOLO en ficheros de la lista que estén bajo core/.
Si ningún fichero de la lista está bajo core/, escribe "Sin cambios en esta release."]

### Clases y Overrides PrestaShop (`/classes`, `/override`)
[Cambios SOLO en ficheros de la lista bajo /classes/ u /override/.
Si no hay ninguno, escribe "Sin cambios en esta release."]

### Controllers (`/controllers`)
[Cambios SOLO en ficheros de la lista bajo /controllers/.
Si no hay ninguno, escribe "Sin cambios en esta release."]

### Templates y Themes (`/themes`)
[Cambios SOLO en ficheros de la lista bajo /themes/.
Indica si afecta a una vertical específica (skl_v2, ikh_v3, smb) o a default-bootstrap.
Si no hay ninguno, escribe "Sin cambios en esta release."]

### Base de datos
[SOLO si hay ficheros .sql o migraciones en la lista.
Si no hay ninguno, escribe "Sin cambios en esta release."]

### Configuración (`/config`, `/core/config`)
[SOLO si hay ficheros de la lista bajo /config/ o /core/config/.
Si no hay ninguno, escribe "Sin cambios en esta release."]

### Módulos (`/modules`)
[SOLO si hay ficheros de la lista bajo /modules/.
Si no hay ninguno, escribe "Sin cambios en esta release."]

### Otros
[Cualquier fichero de la lista que no encaje en las categorías anteriores]

## Impacto en verticales
[Basándote SOLO en los ficheros modificados, indica si afectan a las 3 verticales
o solo a alguna. Si un fichero está en themes/skl_v2/ solo afecta a SKLUM, etc.]

## Contexto para IA
[SOLO notas derivadas del diff real:
- Si AutoLoad.json aparece en la lista Y el diff muestra nuevas clases → listarlas
- Si hay cambios en flujo de ejecución visibles en el diff → describirlos
- Si hay feature flags nuevos visibles en el diff → mencionarlos
- Si NADA de esto aplica, escribe "Sin cambios relevantes de contexto."]

## Ficheros modificados
[Lista EXACTA de los ficheros de la sección "LISTA EXACTA DE FICHEROS MODIFICADOS".
No añadas ninguno que no esté ahí. Agrupa por directorio.]

─── RECORDATORIO FINAL ───
- Escribe en español
- Sé conciso pero preciso
- SOLO documenta lo que VES en el diff y la lista de ficheros
- NUNCA inventes, deduzcas o supongas cambios
- Cada afirmación debe ser verificable contra el diff proporcionado
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
    file_list = read_file_safe(args.file_list)

    if not commits.strip():
        print("⚠ No se encontraron commits. Saltando generación de documentación.")
        return

    if not file_list.strip():
        print("⚠ No se encontró lista de ficheros. Saltando generación.")
        return

    # Extraer lista de ficheros permitidos (de file_list + diff_stat)
    allowed_files = [f.strip() for f in file_list.strip().split("\n") if f.strip()]
    allowed_files += extract_files_from_diff_stat(diff_stat)
    allowed_files = list(set(allowed_files))  # dedup

    print(f"📄 Ficheros modificados detectados: {len(allowed_files)}")
    for f in sorted(allowed_files)[:20]:
        print(f"   - {f}")
    if len(allowed_files) > 20:
        print(f"   ... y {len(allowed_files) - 20} más")

    # 2. Leer contexto existente del proyecto --------------------------------
    docs_dir = Path(args.docs_dir)
    existing_context = read_file_safe(
        docs_dir / "00_CONTEXTO_RAPIDO_IA.md", MAX_CONTEXT_CHARS
    )

    # 3. Construir prompt ----------------------------------------------------
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = build_prompt(commits, diff_stat, diff, file_list, existing_context, today)

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
                    "PHP/PrestaShop. Tu regla número 1 es NUNCA mencionar "
                    "ficheros que no hayan sido modificados. Solo documentas "
                    "lo que aparece explícitamente en el diff y la lista de "
                    "ficheros proporcionada. Si inventas algo, el sistema "
                    "rechazará tu respuesta."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,  # Baja temperatura = menos creatividad = menos alucinaciones
        max_tokens=4096,
    )

    doc_content = response.choices[0].message.content
    if not doc_content:
        print("❌ El modelo no devolvió contenido. Abortando.")
        sys.exit(1)

    # 5. Validar output contra lista de ficheros -----------------------------
    is_valid, violations = validate_output(doc_content, allowed_files)
    if not is_valid:
        print(f"⚠ ADVERTENCIA: La IA mencionó {len(violations)} fichero(s) no modificado(s):")
        for v in violations:
            print(f"   ❌ {v}")
        print("   Se procede igualmente pero la documentación puede contener imprecisiones.")

    # 6. Escribir release doc ------------------------------------------------
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

    # 7. Actualizar CHANGELOG_AI.md ------------------------------------------
    update_changelog(docs_dir, doc_content)

    # 8. Actualizar índice ---------------------------------------------------
    update_index(docs_dir, release_file.name)


def update_changelog(docs_dir: Path, doc_content: str) -> None:
    """Actualiza o crea el CHANGELOG_AI.md acumulativo."""

    changelog_path = docs_dir / "CHANGELOG_AI.md"
    separator = "\n\n---\n\n"
    new_entry = doc_content.strip()

    if changelog_path.exists():
        existing = changelog_path.read_text(encoding="utf-8")
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

    if release_filename in content:
        print(f"ℹ {release_filename} ya está en el índice. Saltando.")
        return

    new_row = (
        f"| [{release_filename}](releases/{release_filename}) "
        f"| Release notes generadas automáticamente |"
    )

    if "### Releases" not in content and "### releases" not in content:
        releases_section = (
            "\n### Releases auto-generadas (`releases/`)\n\n"
            "| Documento | Descripción |\n"
            "|-----------|-------------|\n"
            f"{new_row}\n"
            "| [CHANGELOG_AI.md](CHANGELOG_AI.md) "
            "| Histórico acumulativo de todas las releases |\n\n"
        )
        if "## Convenciones" in content:
            content = content.replace(
                "## Convenciones", releases_section + "## Convenciones"
            )
        else:
            content += "\n" + releases_section
    else:
        lines = content.split("\n")
        insert_idx = None
        in_releases = False

        for i, line in enumerate(lines):
            if "###" in line and "releases" in line.lower():
                in_releases = True
            elif in_releases and line.startswith("|") and "---" not in line:
                insert_idx = i
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
        "--file-list",
        required=True,
        help="Ruta al fichero con lista exacta de ficheros modificados (uno por línea)",
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
