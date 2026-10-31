"""Shell tab completion for the ``pymobile`` command.

The shell scripts are tiny and never change: they call ``pymobile _complete``
with the line typed so far, and the answer is computed from the live argument
parser. A new command, option or ``choices`` value is therefore completed as
soon as it exists, with no second list to update.
"""

from __future__ import annotations

import argparse

__all__ = ["complete_line", "completion_script"]


def _subparsers(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction[argparse.ArgumentParser] | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _takes_value(action: argparse.Action) -> bool:
    return action.nargs != 0  # store_true, help and version take no value


def _visible_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> list[str]:
    # Names that start with "_" are internal (``_complete`` itself).
    return [name for name in sub.choices if not name.startswith("_")]


def complete_line(parser: argparse.ArgumentParser, line: str) -> list[str]:
    """Candidates for the word at the end of ``line`` (without the program name)."""
    tokens = line.split()
    ends_with_space = line[-1:].isspace() or not line
    current = "" if ends_with_space else (tokens.pop() if tokens else "")
    if tokens and tokens[0] in {"pymobile", "python", "python3"}:
        tokens = tokens[1:]

    node = parser
    pending_option: argparse.Action | None = None
    for token in tokens:
        if pending_option is not None:
            pending_option = None  # this token is the option's value
            continue
        if token.startswith("-"):
            action = _find_option(node, token)
            if action is not None and "=" not in token and _takes_value(action):
                pending_option = action
            continue
        sub = _subparsers(node)
        if sub is not None and token in sub.choices:
            node = sub.choices[token]
    return _candidates(node, pending_option, current)


def _find_option(parser: argparse.ArgumentParser, flag: str) -> argparse.Action | None:
    for action in parser._actions:
        if flag in action.option_strings:
            return action
    return None


def _candidates(
    node: argparse.ArgumentParser,
    pending_option: argparse.Action | None,
    current: str,
) -> list[str]:
    if pending_option is not None:
        choices = pending_option.choices
        values = [str(choice) for choice in choices] if choices else []
        return sorted(value for value in values if value.startswith(current))

    if current.startswith("-"):
        flags = {
            flag
            for action in node._actions
            for flag in action.option_strings
            if flag.startswith(current)
        }
        return sorted(flags)

    sub = _subparsers(node)
    if sub is not None:
        return sorted(name for name in _visible_commands(sub) if name.startswith(current))

    positional = [
        str(choice)
        for action in node._actions
        if not action.option_strings and action.choices
        for choice in action.choices
    ]
    return sorted(value for value in positional if value.startswith(current))


def completion_script(shell: str) -> str:
    """The script to load in ``shell`` (``bash`` or ``powershell``)."""
    if shell == "bash":
        return _BASH
    if shell == "powershell":
        return _POWERSHELL
    raise ValueError(f"unsupported shell: {shell!r}")


_BASH = """\
# pymobile tab completion. Load it with:
#   eval "$(pymobile completion bash)"    (put it in ~/.bashrc to keep it)
_pymobile_complete() {
    local IFS=$'\\n'
    COMPREPLY=( $(pymobile _complete -- "${COMP_LINE:0:COMP_POINT}" 2>/dev/null) )
}
complete -F _pymobile_complete pymobile
"""

_POWERSHELL = """\
# pymobile tab completion. Load it with:
#   pymobile completion powershell | Out-String | Invoke-Expression
#   (add the same line to your $PROFILE to keep it)
Register-ArgumentCompleter -Native -CommandName pymobile -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)
    $line = $commandAst.Extent.Text.Substring(
        0, [Math]::Min($cursorPosition - $commandAst.Extent.StartOffset,
                       $commandAst.Extent.Text.Length))
    if ($wordToComplete -eq '' -and -not $line.EndsWith(' ')) { $line += ' ' }
    & pymobile _complete -- $line 2>$null | ForEach-Object {
        [System.Management.Automation.CompletionResult]::new(
            $_, $_, 'ParameterValue', $_)
    }
}
"""
