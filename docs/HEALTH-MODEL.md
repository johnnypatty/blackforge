# What “working” means

Running every security tool automatically is neither safe nor technically
meaningful. Some tools need special hardware, a graphical desktop, licensed
data, root access, credentials, a lab target, or a long-running service.

BlackForge therefore reports observable layers:

| Status | Proven fact |
| --- | --- |
| `available` | The package appears in the locally synced or remotely fetched BlackArch repository database. |
| `installed` | Pacman reports the package as installed. No program was launched. |
| `installed-files-ok` | Installed `/usr/bin` entries declared by the package exist and are executable. No program was launched. |
| `installed-no-cli` | The installed package declares no `/usr/bin` entry. It may be a GUI, library, data, or meta package. |
| `installed-files-missing` | At least one declared `/usr/bin` entry is absent or not executable. |
| `missing-from-repo` | The website lists the package but the live repository database does not. |
| `repo-not-enabled` | Pacman is present, but `[blackarch]` is not configured. |
| `unverified` | The check is running without Arch Linux/pacman. |

For a full current package-availability audit on Arch or BlackArch:

```bash
blackforge --json status --all --output blackforge-audit.json
```

From any operating system, compare all entries with the live official x86_64
repository database:

```bash
blackforge --json status --all --remote --output blackforge-audit.json
```

For installed-file checks:

```bash
blackforge status --all --executables
```

This model deliberately does not call `tool --help`: even “help” flags can
initialize services, touch configuration, require a display, or behave
inconsistently across legacy tools.
