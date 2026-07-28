#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
    printf 'BlackForge installation requires Linux.\n' >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    printf 'Python 3.10+ is required. On Arch: sudo pacman -S python\n' >&2
    exit 1
fi

python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("BlackForge requires Python 3.10 or newer")
PY

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
data_home="${XDG_DATA_HOME:-${HOME}/.local/share}"
config_home="${XDG_CONFIG_HOME:-${HOME}/.config}"
install_root="${data_home}/blackforge"
venv="${install_root}/venv"
bin_dir="${HOME}/.local/bin"
launcher="${bin_dir}/blackforge"

mkdir -p -- "$install_root" "$bin_dir"
python3 -m venv "$venv"
"${venv}/bin/python" -m pip install --disable-pip-version-check --upgrade --no-deps "$project_dir"

if [[ -L "$launcher" ]]; then
    existing_target="$(readlink -- "$launcher")"
    if [[ "$existing_target" != "${venv}/bin/blackforge" ]]; then
        printf 'Refusing to replace an unrelated symlink: %s\n' "$launcher" >&2
        exit 1
    fi
elif [[ -e "$launcher" ]]; then
    printf 'Refusing to replace an existing file: %s\n' "$launcher" >&2
    exit 1
fi
ln -sfn -- "${venv}/bin/blackforge" "$launcher"

bash_completion="${data_home}/bash-completion/completions"
zsh_completion="${data_home}/zsh/site-functions"
fish_completion="${config_home}/fish/completions"
mkdir -p -- "$bash_completion" "$zsh_completion" "$fish_completion"
"${venv}/bin/blackforge" completion bash > "${bash_completion}/blackforge"
"${venv}/bin/blackforge" completion zsh > "${zsh_completion}/_blackforge"
"${venv}/bin/blackforge" completion fish > "${fish_completion}/blackforge.fish"

printf '\nBlackForge installed: %s\n' "$launcher"
case ":${PATH}:" in
    *":${bin_dir}:"*) ;;
    *)
        printf 'Add this to your shell profile, then open a new terminal:\n'
        printf '  export PATH="$HOME/.local/bin:$PATH"\n'
        ;;
esac
printf '\nNext:\n'
printf '  blackforge setup\n'
printf '  blackforge search "subdomain enumeration"\n'
printf '  blackforge install amass\n'
