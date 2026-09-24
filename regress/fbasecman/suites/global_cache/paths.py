"""Stable paths for global-cache suite assets."""


def asset_path(root, *parts):
    return root.joinpath("suites", "global_cache", "assets", *parts)
