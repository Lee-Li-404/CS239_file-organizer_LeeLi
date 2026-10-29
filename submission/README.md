# Submission — Lee Li · 006997204

App name: `file-organizer`. Archive prefix: `Lee-Li-file-organizer-006997204`.

| Location | Purpose |
| --- | --- |
| archives/ | The two files to submit, ending in -requirements.tar.gz and -whole.tar.gz. |
| requirements/file-organizer/ | Application, policy, tool, actual agent prompt, and episode descriptions. |
| whole/src/ | Implementation, setup README, and curated historical demo evidence. |
| whole/tests/ | Test descriptions, offline Python suites, and viewer JavaScript tests. |
| STATUS.md | Requirement coverage, known limitations, and remaining evaluation work. |
| guidelines.md | The submission instructions supplied by the user. |
| content/; build.py | Maintained document inputs and reproducible archive builder. |
| manifest.json | Name/UID, archive sizes and SHA-256 hashes, configured-key exclusion check. |

Regenerate from the project root after changing implementation or descriptions:

```sh
.venv/bin/python submission/build.py
```

The builder reads canonical file_organizer code and policy, exports actual system
prompts, and copies only selected source/tests and the identified synthetic demo.
It excludes .env, unrelated course materials, private downloads, and other traces.
Edit source/content inputs rather than generated requirements/ or whole/.
Historical evidence retains its own executed source snapshot independently of
the current implementation.

Root requirements.md and policies.md now describe the current five-agent Phase 1
application. The public Phase 1 repository also includes three real-model traces
and an MIT license. The older project_plan.md and docs/episodes/ remain local
history and are excluded from publication. Main source folders remain in place
to preserve existing commands and links.
