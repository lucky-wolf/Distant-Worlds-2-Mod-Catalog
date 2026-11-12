# Distant Worlds 2 mod catalog

Federated mod catalog for Distant Worlds 2. `index.json` lists every Distant Worlds 2 mod release found on GitHub (and the GitLab projects in `pointers.json`), rebuilt daily. The DW2 Mod Launcher reads it.

## Get your mod listed

1. Release your mod as a GitHub release with a zip and a `<tag>.manifest.json` asset (see `scripts/release-mod.py` in DW2-XL for the format). Optionally add the mod's preview image as `<tag>.preview.png` (or `.jpg`, under 8 MiB); the launcher shows it as the mod's thumbnail without downloading the zip.
2. Give your repository the topic `dw2-mod`.

That is all. If you cannot add a topic, open a pull request adding `owner/repo` to `pointers.json`. Abuse is handled by `blocked` in the same file.

Download counts and release dates come from GitHub.
