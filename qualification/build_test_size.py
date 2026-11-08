"""Compare effective test sizes on the generated-documentation source tree."""

import subprocess
import xml.etree.ElementTree as ET

import proof


def sizes():
    package = proof.ROOT / "tests/native_binary"
    source = package / "size_stock.bzl"
    build = package / "BUILD"
    original = build.read_bytes()
    assert not source.exists()
    expected = {}
    lines = []
    for variant, macro in (("stock", "stock_test"), ("fallback", "build_test"), ("native", "build_test")):
        tools = ', marker_tool=":write_marker", test_runner=":assertarg"' if variant == "native" else ""
        for suffix, value, effective in (("default", "", "small"), ("explicit", ', size="large"', "large"), ("none", ", size=None", "medium")):
            name = f"size_{variant}_{suffix}"
            label = "//tests/native_binary:" + name
            kind = "native_test" if variant == "native" else "_empty_test"
            expected[label] = {"kind": kind, "size": effective}
            lines.append(f'{macro}(name="{name}", targets=[":assertdata"]{tools}{value})')
    expected["//tests/native_binary:declared_build_test!"] = {"kind": "native_test", "size": "small"}
    try:
        source.write_bytes(proof.git("show", proof.PRODUCT + "^:rules/build_test.bzl"))
        build.write_bytes(b'load(":size_stock.bzl", stock_test="build_test")\n' + original + ("\n" + "\n".join(lines) + "\n").encode())
        query = "set(" + " ".join(f'"{label}"' for label in expected) + ")"
        cmd = [proof.WORK / "bazel-8.5.0", "--nosystem_rc", "--nohome_rc", "--host_jvm_args=-Xmx4g",
               "query", "--lockfile_mode=off", "--output=xml", "--xml:default_values"]
        report = proof.R["size_query"] = {"expression": query, "attempts": []}
        for _ in range(5):
            result = subprocess.run([*cmd, *["--distdir=" + str(d) for d in sorted(proof.old.DIRS)], query],
                                    capture_output=True, timeout=120)
            err = result.stderr.decode(errors="replace")
            report["attempts"].append({"exit": result.returncode, "stderr_tail": err[-1600:]})
            proof.old.save()
            if result.returncode == 0 or not proof.old.recover(err):
                break
        result.check_returncode()
        assert 0 < len(result.stdout) < 512000
        (proof.OUT / "sizes.xml").write_bytes(result.stdout)
        rows = {}
        for rule in ET.fromstring(result.stdout).findall("rule"):
            label = rule.get("name").removeprefix("@@")
            size = rule.find("string[@name='size']")
            rows[label] = {"kind": rule.get("class"), "size": size.get("value") if size is not None else None}
        report["rows"] = rows
        proof.old.save()
        assert rows == expected, rows
    finally:
        build.write_bytes(original)
        source.unlink(missing_ok=True)


if __name__ == "__main__":
    assert proof.R["checked_out"] == proof.PRODUCT and proof.R["checked_out_tree"] == proof.R["tree"]
    proof.R["generated_documentation_artifact"] = 13623870953
    proof.old.assets.DIST.mkdir(parents=True, exist_ok=True)
    proof.old.DIRS.add(proof.old.assets.DIST)
    try:
        sizes()
        proof.R["verified"] = True
    except Exception as exc:
        proof.R["error"] = type(exc).__name__ + " " + str(exc)[:700]
        raise
    finally:
        proof.old.save()
