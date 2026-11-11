"""Regenerate docs/content/administration/parametres.md from backend/app/soc/runtime.py.

Run: make docs-params (in the backend container), or python3 docs/tools/gen_parametres.py
with the backend dependencies installed. A backend test fails when a
runtime setting is missing from the page, so the documentation cannot drift.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1]
# Backend sources: ../backend in the repository, or BACKEND_DIR (e.g. /app in the container).
sys.path.insert(0, os.environ.get("BACKEND_DIR") or str(DOCS.parent / "backend"))

from app.soc.runtime import EDITABLE  # noqa: E402

TYPES = {int: "entier", float: "décimal", bool: "oui / non"}

HEADER = """# Paramètres opérationnels

<p class="where">Administration → <strong>Paramètres</strong> — administrateurs. Page générée depuis le code (<code>docs/tools/gen_parametres.py</code>).</p>

Ces réglages modifient le comportement du moteur **sans redémarrage**. Chaque modification est enregistrée, appliquée immédiatement et tracée dans l'audit (`system.settings`). La barre du haut (**Appliquer (n)**, **Annuler les modifications**) reste visible pendant le défilement. Dans l'interface, chaque paramètre affiche la même explication que ci-dessous ; **▲ Augmenter** et **▼ Diminuer** décrivent l'effet d'un changement dans chaque sens.

!!! tip "Méthode"
    Changez **un seul paramètre à la fois**, observez la file d'incidents pendant quelques jours (part de fausses alarmes dans les clôtures, incidents manqués), puis ajustez.

## Vue d'ensemble

| Paramètre | Valeur par défaut | Plage | Unité |
| --- | --- | --- | --- |
"""


def main() -> None:
    lines = [HEADER]
    for key, (typ, low, high, label, meta) in EDITABLE.items():
        lines.append(f"| [{label}](#{key.replace('_', '-')}) | {meta['default']} | {low} – {high} | {meta['unit']} |\n")
    for key, (typ, low, high, label, meta) in EDITABLE.items():
        lines.append(f"\n## {label} {{#{key.replace('_', '-')}}}\n\n")
        lines.append(f"Nom technique : `{key}` · type {TYPES.get(typ, typ.__name__)} · plage {low} – {high} {meta['unit']} · "
                     f"défaut **{meta['default']}**\n\n")
        lines.append(f"{meta['what']}\n\n")
        lines.append(f"- **▲ Augmenter** : {meta['higher']}\n")
        lines.append(f"- **▼ Diminuer** : {meta['lower']}\n\n")
        lines.append(f"!!! success \"Recommandation\"\n    {meta['advice']}\n")
    out = DOCS / "content" / "administration" / "parametres.md"
    out.write_text("".join(lines), encoding="utf-8")
    print(f"{out} ({len(EDITABLE)} paramètres)")


if __name__ == "__main__":
    main()
