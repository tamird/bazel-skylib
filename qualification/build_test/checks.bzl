"""Qualification-only checked outputs and consumers."""

def _outputs(ctx):
    files = [ctx.actions.declare_file(ctx.label.name + "_{}.dat".format(i)) for i in range(ctx.attr.count)]
    for i, output in enumerate(files):
        ctx.actions.write(output, "physical checked file {} {}\n".format(ctx.var["SKYLIB_CHECK_MODE"], i))
    return [DefaultInfo(files = depset(files))]

outputs = rule(implementation = _outputs, attrs = {"count": attr.int(default = 1)})

def _broken(ctx):
    out = ctx.actions.declare_file("broken.dat")
    ctx.actions.run_shell(outputs = [out], command = "echo SKYLIB_INTENTIONAL_BROKEN_INPUT >&2; exit 41", mnemonic = "CheckedInputFailure")
    return [DefaultInfo(files = depset([out]))]

broken = rule(implementation = _broken)

def _single(ctx):
    out = ctx.actions.declare_file(ctx.label.name + ".copy")
    ctx.actions.symlink(output = out, target_file = ctx.file.src)
    return [DefaultInfo(files = depset([out]))]

single = rule(implementation = _single, attrs = {"src": attr.label(allow_single_file = True)})

def _receiver(ctx):
    exe = ctx.actions.declare_file(ctx.label.name + ".sh")
    ctx.actions.write(exe, "#!/bin/sh\nexit 0\n", is_executable = True)
    return [DefaultInfo(executable = exe, runfiles = ctx.attr.source[DefaultInfo].default_runfiles)]

consumer_test = rule(implementation = _receiver, attrs = {"source": attr.label()}, test = True)

def _inspect(ctx):
    target = ctx.attr.source
    info = target[DefaultInfo]
    group = getattr(target[OutputGroupInfo], "default", depset()) if OutputGroupInfo in target else depset()
    out = ctx.actions.declare_file(ctx.label.name + ".json")
    ctx.actions.write(out, json.encode({
        "executable": info.files_to_run.executable.short_path,
        "files": [f.short_path for f in info.files.to_list()],
        "group": [f.short_path for f in group.to_list()],
        "runfiles": [f.short_path for f in info.default_runfiles.files.to_list()],
    }))
    return [DefaultInfo(files = depset([out]))]

inspect = rule(implementation = _inspect, attrs = {"source": attr.label()})
