"""
Script utilitário para criar e registar uma nova release do Accdc.
Garante que a versão do projeto e da interface segue o formato YYYYMMDD com a data atual.

Uso:
    python create_release.py
    ou
    python create_release.py 20261105
"""
import sys
import os
import re
from datetime import datetime

VERSION_FILE = os.path.join(os.path.dirname(__file__), "core", "version.py")

def create_release(version_str=None):
    if not version_str:
        version_str = datetime.now().strftime("%Y%m%d")

    # Validar formato YYYYMMDD
    if not re.match(r"^\d{8}$", version_str):
        print(f"Error: Version format must be YYYYMMDD (e.g., 20261105). Received: {version_str}")
        sys.exit(1)

    conteudo = f'''"""
Módulo de Versionamento do Projeto Accdc.
Define a versão da aplicação segundo o formato de release por data: YYYYMMDD.
"""

VERSION = "{version_str}"
__version__ = VERSION
'''

    with open(VERSION_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(conteudo)

    print(f"[RELEASE CREATED SUCCESSFULLY] Version updated to: {version_str}")
    print(f"-> File updated: {VERSION_FILE}")
    print("-> About tab and main window now automatically reflect this version.")

if __name__ == "__main__":
    v = sys.argv[1] if len(sys.argv) > 1 else None
    create_release(v)
