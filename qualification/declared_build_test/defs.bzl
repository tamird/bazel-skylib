"""Select the project's build_test tools once."""
load("@bazel_skylib//rules:build_test.bzl", _build_test = "build_test")

def build_test(name, targets, **kwargs):
    _build_test(
        name = name,
        targets = targets,
        marker_tool = "//tools:touch",
        test_runner = "//tools:coreutils",
        args = ["true"] + kwargs.pop("args", []),
        **kwargs
    )
