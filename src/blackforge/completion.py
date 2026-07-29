from __future__ import annotations

COMMANDS = (
    "help version setup sync update-catalog list names search show info categories "
    "doctor status check "
    "install get add remove rm uninstall upgrade repo profile export "
    "completion interactive tui plan history resume mirror updates self-update "
    "env maintenance collection"
)


def script(shell: str) -> str:
    if shell == "bash":
        return f"""# bash completion for BlackForge
_blackforge_complete() {{
    local current command
    current="${{COMP_WORDS[COMP_CWORD]}}"
    command="${{COMP_WORDS[1]}}"

    if (( COMP_CWORD == 1 )); then
        mapfile -t COMPREPLY < <(compgen -W "{COMMANDS}" -- "$current")
        return
    fi

    case "$command" in
        install|get|add|remove|rm|uninstall|show|info|status|check|upgrade)
            mapfile -t COMPREPLY < <(
                compgen -W "$(blackforge names --prefix "$current")" -- "$current"
            )
            ;;
    esac
}}
complete -F _blackforge_complete blackforge
"""
    if shell == "zsh":
        return f"""#compdef blackforge
_blackforge() {{
    local -a commands packages
    commands=({COMMANDS})
    if (( CURRENT == 2 )); then
        _describe 'command' commands
        return
    fi
    case "$words[2]" in
        install|get|add|remove|rm|uninstall|show|info|status|check|upgrade)
            packages=("${{(@f)$(blackforge names --prefix "$PREFIX")}}")
            _describe 'BlackArch package' packages
            ;;
    esac
}}
compdef _blackforge blackforge
"""
    if shell == "fish":
        return f"""# fish completion for BlackForge
complete -c blackforge -f
complete -c blackforge -n '__fish_use_subcommand' -a '{COMMANDS}'
complete -c blackforge -n '__fish_seen_subcommand_from install get add remove rm uninstall show info status check upgrade' -a '(blackforge names --prefix (commandline -ct))'
"""
    raise ValueError(f"Unsupported shell: {shell}")
