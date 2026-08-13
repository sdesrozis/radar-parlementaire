"""Le mode développement : servir `sortie/` comme Vercel le servira.

Ce fichier a remplacé une application dynamique de 900 lignes — un serveur
d'API JSON dans le paquet `radar`, et un client JavaScript qui la consommait.
Elle rendait le même service que le générateur statique, sur les mêmes données,
avec un second système visuel et une seconde chance de diverger.

Le principe qui l'a remplacée : **ce qu'on regarde en local doit être
exactement ce qui sera en ligne**. Donc pas d'API, pas de rendu côté client,
pas de branche « en local, on fait autrement ». On génère les fichiers, on les
sert tels quels, et le seul écart avec la production est le nom de l'hôte.

    uv run python site/generer.py --servir   # recalcule tout, puis sert
    python3 site/serveur.py                  # sert ce qui est déjà généré

La seconde forme n'a **aucune dépendance** : ni `uv`, ni `radar`, ni polars. Elle
n'existe pas par élégance mais parce que relire le site ne doit pas coûter les
vingt secondes de calcul que coûte le reconstruire — sans quoi on prend
l'habitude d'ouvrir `file://`, qui ment sur les chemins et sur le canvas.

Le serveur vient de la bibliothèque standard : le site est local, mono-
utilisateur, et n'a aucune raison d'ajouter une dépendance. `SimpleHTTPRequest
Handler` assainit lui-même les chemins remontants — c'est pourquoi on ne réécrit
pas la résolution de fichier, qui était l'endroit exact où l'ancien serveur
devait se garder d'un `../`.
"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SORTIE = Path(__file__).parent / "sortie"

# Le port par défaut, que `$PORT` peut imposer. La convention vaut pour tous les
# lanceurs qui attribuent eux-mêmes un port libre : sans elle, deux relectures
# simultanées du site se disputent le 8000 et la seconde ne démarre pas.
PORT = int(os.environ.get("PORT") or 8000)


class _Silencieux(SimpleHTTPRequestHandler):
    """Le journal par défaut écrit une ligne par fichier : illisible en local."""

    protocol_version = "HTTP/1.1"
    server_version = "radar"

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass


def servir(dossier: Path, *, host: str = "127.0.0.1", port: int = PORT) -> ThreadingHTTPServer:
    """Crée le serveur sur `dossier`. À l'appelant d'appeler `serve_forever()`.

    L'écoute est locale par défaut : le site n'est pas exposé au réseau sans un
    `host` explicite.
    """
    return ThreadingHTTPServer((host, port), partial(_Silencieux, directory=str(dossier)))


def ouvrir(dossier: Path, *, port: int = PORT, navigateur: bool = True) -> None:
    """Sert `dossier` jusqu'à Ctrl+C. C'est la boucle, pas seulement le serveur."""
    s = servir(dossier, port=port)
    url = f"http://127.0.0.1:{port}/"
    print(f"→ {url}   (Ctrl+C pour arrêter)")
    if navigateur:
        webbrowser.open(url)
    try:
        s.serve_forever()
    except KeyboardInterrupt:
        print("  arrêt.")
    finally:
        s.server_close()


def main() -> None:
    p = argparse.ArgumentParser(description="Sert le site déjà généré, sans le recalculer.")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--sans-navigateur", action="store_true",
                   help="ne pas ouvrir le navigateur au démarrage")
    args = p.parse_args()

    if not (SORTIE / "index.html").is_file():
        # Servir un dossier vide donnerait une liste de fichiers ou un 404 : ni
        # l'un ni l'autre ne dit ce qu'il faut faire.
        print(f"Rien à servir : {SORTIE}/index.html n'existe pas.\n"
              f"Générer d'abord :  uv run python site/generer.py", file=sys.stderr)
        raise SystemExit(1)

    ouvrir(SORTIE, port=args.port, navigateur=not args.sans_navigateur)


if __name__ == "__main__":
    main()
