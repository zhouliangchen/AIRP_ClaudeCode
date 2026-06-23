"""Projection and actor intent executors."""

from __future__ import annotations


def execute_request_projection(dispatcher_module, run_dir, root_dir, intent, run_claude):
    return dispatcher_module._execute_request_projection_impl(run_dir, root_dir, intent, run_claude)


def execute_run_actor(dispatcher_module, run_dir, root_dir, intent, run_claude):
    return dispatcher_module._execute_run_actor_impl(run_dir, root_dir, intent, run_claude)
