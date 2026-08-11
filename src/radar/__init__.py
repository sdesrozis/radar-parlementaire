"""Radar parlementaire — votes, amendements et sujets de l'Assemblée nationale.

Pipeline en quatre temps :

    fetch   →  parse   →  analyze / topics  →  alerts / viz
    (zips)     (parquet)   (numpy, polars)      (bulletin, graphiques)

Point d'entrée usuel :

    from radar import analyze, viz

    cube = analyze.build_cube()
    analyze.plus_proches(cube, "Charles de Courson", hors_groupe=True)

Source : open data de l'Assemblée nationale, Licence Ouverte 2.0.
"""

__version__ = "0.1.0"

__all__ = ["alerts", "analyze", "config", "fetch", "parse", "topics", "viz"]
