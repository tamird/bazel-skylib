# Copyright 2019 The Bazel Authors. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A test verifying other targets build as part of a `bazel test`"""

load("//lib:new_sets.bzl", "sets")

def _empty_test_impl(ctx):
    is_windows = ctx.target_platform_has_constraint(ctx.attr._windows_constraint[platform_common.ConstraintValueInfo])
    extension = ".bat" if is_windows else ".sh"
    content = "exit 0" if is_windows else "#!/usr/bin/env bash\nexit 0"
    executable = ctx.actions.declare_file(ctx.label.name + extension)
    ctx.actions.write(
        output = executable,
        is_executable = True,
        content = content,
    )

    # Build and test requests include the default output group. Keep the
    # checked files separate from the executable and its runfiles.
    return [
        DefaultInfo(
            files = depset([executable]),
            executable = executable,
            runfiles = ctx.runfiles(),
        ),
        OutputGroupInfo(default = depset(transitive = [
            target[DefaultInfo].files
            for target in ctx.attr.targets
        ])),
    ]

_empty_test = rule(
    implementation = _empty_test_impl,
    attrs = {
        "targets": attr.label_list(allow_files = True),
        "_windows_constraint": attr.label(default = "@platforms//os:windows"),
    },
    test = True,
)

def build_test(name, targets, **kwargs):
    """Test rule checking that other targets build.

    This works not by an instance of this test failing, but instead by
    the targets it depends on failing to build, and hence failing
    the attempt to run this test.

    Typical usage:

    ```
      load("@bazel_skylib//rules:build_test.bzl", "build_test")
      build_test(
          name = "my_build_test",
          targets = [
              "//some/package:rule",
          ],
      )
    ```

    Args:
      name: The name of the test rule.
      targets: A list of targets to ensure build.
      **kwargs: The [common attributes for tests](https://bazel.build/reference/be/common-definitions#common-attributes-tests).
    """
    if len(targets) == 0:
        fail("targets must be non-empty", "targets")
    if kwargs.get("data", None):
        fail("data is not supported on a build_test()", "data")

    # Remove any duplicate test targets.
    targets = sets.to_list(sets.make(targets))

    _empty_test(
        name = name,
        targets = targets,
        size = kwargs.pop("size", "small"),  # Default to small for test size
        **kwargs
    )
