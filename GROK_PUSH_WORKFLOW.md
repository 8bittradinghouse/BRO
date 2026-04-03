# GROK Push Workflow

Use this after each git push so Grok always gets a consistent packet.

## 1) Push your code branch

```bash
git push origin <branch>
```

## 2) Generate Grok sync packet + pointer update

```bash
./scripts/grok_sync_packet.sh \
  --branch <branch> \
  --commit HEAD \
  --run-id <run_id_if_any> \
  --proof-file <proof_note_path_if_any>
```

What this does:
- writes a paste-ready packet to `exports/GROK_SYNC_PACKET_<UTC>.md`
- appends a new pointer section to `GROK_BLOB_INDEX.md`
- updates the `Branch:` line in `GROK_BLOB_INDEX.md`

## 3) Publish pointer update

```bash
git add GROK_BLOB_INDEX.md scripts/grok_sync_packet.sh GROK_PUSH_WORKFLOW.md
git commit -m "add/update grok sync pointers"
git push origin <branch>
```

## Notes

- `exports/` is intentionally ignored; packet files are local convenience artifacts.
- `GROK_BLOB_INDEX.md` is the tracked index Grok should open first.
- For run artifacts, include local absolute paths in the packet (validation summaries are not git-tracked).
