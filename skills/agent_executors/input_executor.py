"""Input-analysis intent executor."""

from __future__ import annotations


def execute(dispatcher_module, run_dir, card_folder, root_dir, intent, run_claude=None):
    return dispatcher_module._execute_analyze_input_impl(
        run_dir,
        card_folder,
        root_dir,
        intent,
        run_claude=run_claude,
    )
