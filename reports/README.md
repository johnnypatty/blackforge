# BlackArch package-availability snapshot

Generated on 2026-07-28 from:

- Website catalog: <https://www.blackarch.org/tools.html>
- x86_64 repository database:
  <https://www.blackarch.org/blackarch/blackarch/os/x86_64/blackarch.db>

## Result

| Check | Count |
| --- | ---: |
| Website-listed tools | 2,861 |
| Present in the live x86_64 repository database | 2,858 |
| Website-listed but absent from that database | 3 |
| Total repository packages, including dependencies, split/debug, desktop, and infrastructure packages | 5,050 |

The three website entries absent from the live repository database were:

- `rtl` (`blackarch-radio`)
- `sr` (`blackarch-recon`)
- `vega` (`blackarch-webapp`)

These are package-availability failures, not proof that the upstream projects
are dead. For example, `rtl` points to the active `rtl_433` upstream project;
the mismatch may be a rename, temporary repository publication gap, or stale
website entry.

Four entries had a different base version between the website and repository:

| Package | Website | Repository |
| --- | --- | --- |
| `apkstudio` | `100.9e114ca` | `2:6.3.0.r0.gc6e2724-1` |
| `garak` | `0.15.1.r58.g17de5f8` | `0.15.1.r346.g1c7c7e2-1` |
| `recaf` | `4.0.0.alpha.r22.g709b3f9` | `4.0.0.alpha.r406.gc10fb3e-2` |
| `user-scanner` | `1.4.1` | `1.4.2-1` |

The full 2,861-row evidence is in
[`blackarch-package-health-2026-07-28.json`](blackarch-package-health-2026-07-28.json).

Repository metadata:

- Last-Modified: `Mon, 27 Jul 2026 14:27:10 GMT`
- SHA-256:
  `6f59e5ed9c25483adba4b94ee16788432ef547b162084418c4c14ec7b674ddef`

## Important limit

No tools were launched. A repository entry proves that a package is currently
published, not that every code path works. Runtime validation must be scoped by
tool and performed in an authorized lab with its required hardware, services,
credentials, data, and graphical environment.

Repeat the check at any time:

```bash
blackforge --json status --all --remote --output blackforge-audit.json
```

