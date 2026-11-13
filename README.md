# Distant Worlds 2 mod catalog

Federated mod catalog for Distant Worlds 2. `index.json` lists every Distant Worlds 2 mod release found on GitHub (and the GitLab projects in `pointers.json`), rebuilt daily. The DW2 Mod Launcher reads it.

## Get your mod listed

1. Release your mod as a GitHub release with a zip and a `<tag>.manifest.json` asset (the DW2 Mod Launcher's Publish dialog makes both; its `ReleasePackager` defines the format). Optionally add the mod's preview image as `<tag>.preview.png` (or `.jpg`, under 8 MiB); the launcher shows it as the mod's thumbnail without downloading the zip.
2. Give your repository the topic `dw2-mod`.

That is all. If you cannot add a topic, open a pull request adding `owner/repo` to `pointers.json`. Abuse is handled by `blocked` in the same file.

## Identity

A mod is listed only with an **author** and a **uid**: a random UUID the DW2 Mod Launcher writes into the mod's `dw2modlauncher.json` when the mod is made or first published, and the launcher's Publish copies into the manifest. A release without either is rejected (the workflow log says which and why), so publish a mod once from the launcher before releasing it. A GitLab pointer needs `uid` and `author` too.

The first repository to release a uid owns it. Another repository's release of the same uid is refused, so a copy cannot take over a listing. To move a mod to another repository on purpose, add `"approved": { "<uid>": "owner/repo" }` to `pointers.json` (a GitLab project is `gitlab.com/group/project`). This stops casual takeover; it does not prove who wrote a mod.

Download counts and release dates come from GitHub.
