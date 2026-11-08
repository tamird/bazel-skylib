"""Check declared marker arguments, tool runfiles and downstream failure."""
import hashlib
from pathlib import Path
import subprocess

import proof

ROOT, WORK, OUT = proof.ROOT, proof.WORK, proof.OUT
R = proof.R
PKG = "//tests/native_binary:"
OWNER = PKG + "declared_build_test!"
RESOURCE = PKG + "check!"
DOWNSTREAM = PKG + "marker_downstream"
RESOURCE_TOKEN = "SKYLIB_MARKER_DECLARED_RUNTIME_FILE"
DOC_HASH = "975e903a2c87cfaf1ce60d99ad893364af25703f1c185bc85a066956db963bab"


def actions(phase, label, mnemonic):
    rows = []
    for event in proof.old.events(WORK / (phase + ".bep")):
        if "action" not in event:
            continue
        action = event["action"]
        action_label = event["id"]["actionCompleted"].get("label", "").removeprefix("@@")
        if action_label != label or action.get("type") != mnemonic:
            continue
        row = {"owner": label, "mnemonic": mnemonic, "ok": action.get("success", False),
               "argv": action.get("commandLine", []), "exit": action.get("exitCode")}
        for stream in ("stdout", "stderr"):
            file = action.get(stream, {})
            if not file.get("uri", "").startswith("file:"):
                continue
            try:
                raw = proof.old.read_item(file)
            except OSError:
                continue
            row[stream] = [line[:400] for line in raw.decode(errors="replace").splitlines()
                           if "SKYLIB_" in line][:5]
        rows.append(row)
    return rows


def checked_marker(phase, label):
    row = proof.phase(phase, "test", label, 12)
    markers = actions(phase, label + "_0__deps", "BuildTestMarker")
    row["build_test_markers"] = markers
    proof.old.save()
    assert proof.passed(row, label) and len(markers) == 1 and markers[0]["ok"], label
    executable, output = markers[0]["argv"]
    owner_name = label.split(":")[1]
    marker = ROOT / "bazel-bin/tests/native_binary" / (owner_name + "_0__deps.out")
    assert output.endswith("/tests/native_binary/" + marker.name), output
    assert (ROOT / output).samefile(marker) and marker.stat().st_size == 0, output
    row["materialized_marker_bytes"] = marker.stat().st_size
    row["tool_sha256"] = hashlib.sha256((ROOT / executable).read_bytes()).hexdigest()
    proof.old.save()


def main():
    assert R["checked_out"] == proof.PRODUCT and R["checked_out_tree"] == R["tree"]
    R["previous"] = {"semantic_matrix": 13621086928, "quoted_path_and_docs": 13620994476}
    proof.old.assets.DIST.mkdir(parents=True, exist_ok=True)
    proof.old.DIRS.add(proof.old.assets.DIST)
    pkg = ROOT / "tests/native_binary"
    build = pkg / "BUILD"
    rc = ROOT / ".bazelrc"
    original = build.read_bytes()
    assert not rc.exists()
    checks = pkg / "marker_probe_checks.bzl"
    script = pkg / "marker-probe.sh"
    token = pkg / "marker-probe-resource.txt"
    assert not any(p.exists() for p in (checks, script, token))
    fmt = subprocess.run([WORK / "buildifier", "-mode=diff", "rules/build_test.bzl",
                          "rules/BUILD", "tests/native_binary/BUILD"], capture_output=True)
    R["format"] = {"exit": fmt.returncode, "diff": (fmt.stdout + fmt.stderr).decode()[:2000]}
    doc = (ROOT / "docs/build_test_doc.md").read_bytes()
    assert hashlib.sha256(doc).hexdigest() == DOC_HASH
    R["checked_in_stardoc"] = {"sha256": DOC_HASH, "bytes": len(doc)}
    # Require the tool to see its runfiles in a sandbox, rather than a populated execroot.
    rc.write_text("build --strategy=BuildTestMarker=processwrapper-sandbox\n")
    try:
        checks.write_bytes((WORK / "checks.bzl").read_bytes())
        token.write_text(RESOURCE_TOKEN + "\n")
        script.write_text("""#!/bin/sh
set -eu
[ "$#" = 1 ]
for workspace in _main bazel_skylib; do
  file="$0.runfiles/$workspace/tests/native_binary/marker-probe-resource.txt"
  if [ -f "$file" ]; then
    IFS= read -r value < "$file"
    [ "$value" = SKYLIB_MARKER_DECLARED_RUNTIME_FILE ]
    printf '%s\\n' "$value"
    : > "$1"
    exit 0
  fi
done
echo SKYLIB_MARKER_RUNTIME_FILE_MISSING >&2
exit 46
""")
        script.chmod(0o755)
        build.write_bytes(b'load(":marker_probe_checks.bzl", "broken", "consumer_test")\n' + original + b"""
native_binary(name="resource_marker", src="marker-probe.sh", data=["marker-probe-resource.txt"])
build_test(name="check!", targets=[":assertdata"], marker_tool=":resource_marker", test_runner=":assertarg", args=["'a b'"])
broken(name="faulty_marker_input", tags=["manual"])
build_test(name="failed_fixture", targets=[":faulty_marker_input"], marker_tool=":write_marker", test_runner=":assertarg", tags=["manual"])
consumer_test(name="marker_downstream", source=":failed_fixture", tags=["manual"])
""")
        operations = {
            "fixture": lambda: checked_marker("fixture", OWNER),
            "runtime_file": lambda: checked_marker("runtime_file", RESOURCE),
            "downstream": negative,
        }
        R["outcomes"] = {}
        for name, work in operations.items():
            try:
                work()
                R["outcomes"][name] = True
            except Exception as exc:
                R["outcomes"][name] = False
                R.setdefault("diagnostics", {})[name] = type(exc).__name__ + " " + str(exc)[:500]
            proof.old.save()
    finally:
        build.write_bytes(original)
        rc.unlink()
        for path in (checks, script, token):
            path.unlink(missing_ok=True)
    assert proof.git("diff", "--name-only") == b""
    docs = proof.phase("docs", "test", proof.DOC_TEST, 10)
    R["outcomes"]["docs"] = proof.passed(docs, proof.DOC_TEST)
    R["verified"] = all(R["outcomes"].values()) and fmt.returncode == 0
    assert R["verified"]


def negative():
    # The saved phase's argv mode adds --build_tests_only and --keep_going.
    row = proof.phase("argv", "test", DOWNSTREAM, 8)
    broken = actions("argv", PKG + "faulty_marker_input", "CheckedInputFailure")
    row["failed_actions"] = broken
    proof.old.save()
    assert row["exit"] != 0 and not row["tests"] and len(broken) == 1 and not broken[0]["ok"]
    text = (WORK / "argv.console").read_text(errors="replace")
    assert "SKYLIB_INTENTIONAL_BROKEN_INPUT" in (text.splitlines() + broken[0].get("stderr", []))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        R["error"] = type(exc).__name__ + " " + str(exc)[:500]
        raise
    finally:
        proof.old.save()
