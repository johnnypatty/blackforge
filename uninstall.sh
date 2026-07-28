#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
    printf 'BlackForge uninstallation requires Linux.\n' >&2
    exit 1
fi

data_home="${XDG_DATA_HOME:-${HOME}/.local/share}"
config_home="${XDG_CONFIG_HOME:-${HOME}/.config}"
install_root="${data_home}/blackforge"
launcher="${HOME}/.local/bin/blackforge"

if [[ "$install_root" != */blackforge || "$install_root" == "/blackforge" ]]; then
    printf 'Refusing unsafe install path: %s\n' "$install_root" >&2
    exit 1
fi

if [[ -L "$launcher" ]]; then
    target="$(readlink -- "$launcher")"
    if [[ "$target" == "${install_root}/venv/bin/blackforge" ]]; then
        rm -- "$launcher"
    fi
fi

rm -f -- \
    "${data_home}/bash-completion/completions/blackforge" \
    "${data_home}/zsh/site-functions/_blackforge" \
    "${config_home}/fish/completions/blackforge.fish"
rm -rf -- "$install_root"

printf 'BlackForge application files were removed.\n'
printf 'The BlackArch tools you installed were left untouched.\n'

