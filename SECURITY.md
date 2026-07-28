# Security policy

BlackForge never launches catalog tools and never passes package names through
a shell. Package changes are delegated to `pacman` with an argument list.

Repository setup downloads BlackArch's official `strap.sh` over HTTPS, displays
its SHA-256 and first lines, verifies the SHA-1 currently published on
BlackArch's installation page, asks for confirmation, then invokes it directly
with `bash`. Use `--dry-run` if you only want to inspect it. If upstream changes
the script, BlackForge fails closed until the pinned checksum is reviewed.

Report vulnerabilities privately through GitHub's security-advisory feature.
Do not include live credentials, target data, or exploit output.
