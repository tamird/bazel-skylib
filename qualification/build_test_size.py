"""Regenerate Skylib's docs and compare the effective size of each test shape."""

import hashlib
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
        query = "set(" + " ".join(expected) + ")"
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


def documentation():
    doc = proof.ROOT / proof.DOC
    before = doc.read_bytes()
    updater = proof.phase("updater", "run", "//docs:update", 23)
    assert updater["exit"] == 0
    data = doc.read_bytes()
    assert 0 < len(data) < 20000
    (proof.OUT / "build_test_doc.md").write_bytes(data)
    changed = proof.git("diff", "--name-only", "--", "docs").decode().splitlines()
    proof.R["stardoc"] = {"producer": "//docs:update", "bytes": len(data),
                          "sha256": hashlib.sha256(data).hexdigest(), "changed": changed}
    proof.old.save()
    assert changed == [proof.DOC] and data != before
    assert data.count(b'<a id="build_test-size">') == 1 and b'href="#build_test-size"' in data
    row = proof.phase("docs", "test", proof.DOC_TEST, 6)
    assert proof.passed(row, proof.DOC_TEST)


def main():
    assert proof.R["checked_out"] == proof.PRODUCT and proof.R["checked_out_tree"] == proof.R["tree"]
    proof.old.assets.DIST.mkdir(parents=True, exist_ok=True)
    proof.old.DIRS.add(proof.old.assets.DIST)
    fmt = subprocess.run([proof.WORK / "buildifier", "-mode=diff", "rules/build_test.bzl", "rules/BUILD",
                          "tests/native_binary/BUILD"], capture_output=True)
    proof.R["format"] = {"exit": fmt.returncode, "diff": (fmt.stdout + fmt.stderr).decode()[:2000]}
    outcomes = proof.R["outcomes"] = {"format": fmt.returncode == 0}
    for task in (documentation, sizes):
        try:
            task()
            outcomes[task.__name__] = True
        except (AssertionError, KeyError, OSError, ValueError, subprocess.SubprocessError, ET.ParseError) as exc:
            outcomes[task.__name__] = False
            proof.R.setdefault("errors", {})[task.__name__] = type(exc).__name__ + " " + str(exc)[:700]
        proof.old.save()
    proof.R["verified"] = all(outcomes.values())
    proof.old.save()
    assert proof.R["verified"], outcomes


if __name__ == "__main__":
    main()
