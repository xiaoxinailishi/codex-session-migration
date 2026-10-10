# Contributing

Keep changes focused, readable and reversible. Python runtime code uses only the standard library and supports 3.11+. Native publication is Windows-specific. Add a synthetic regression test for changes affecting migration, image/offset handling, rollback or deletion. Never use a real user database as a fixture.

```text
python -m unittest discover -v
python scripts/check_publication.py --tracked-only
```

Windows PowerShell helpers must remain compatible with Windows PowerShell 5.1 and use UTF-8 with BOM. Do not declare variables that shadow automatic variables such as `$HOME`, `$PID` or `$Host`. Open the offline runner from Start or Win+R; creating a new console from Codex does not by itself prove process independence.

When adding support for a database migration, document its columns and meaning, add old/new synthetic cases, reject unknown alternatives, and prove unselected records are preserved. Do not update the recipient migration registry to fake compatibility. Do not silently reinterpret an image as an equivalent original file.

Reports distinguish static checks, synthetic tests, offline application and UI acceptance. Passing CI is not native Codex acceptance. Keep cleanup user-controlled and protect live image roots. Review [SECURITY.md](SECURITY.md) before posting a repro or publishing files.
