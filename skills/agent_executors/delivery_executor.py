"""Delivery intent executor."""

from __future__ import annotations


def execute(dispatcher_module, run_dir, card_folder, root_dir, intent, run_command):
    return dispatcher_module._execute_deliver_round_impl(run_dir, card_folder, root_dir, intent, run_command)
