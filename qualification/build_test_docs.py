"""Check the supplier test and regenerate its published documentation."""
import hashlib
import os
from pathlib import Path
import subprocess
import time
import tarfile
import xml.etree.ElementTree as ET
import matrix as old

ROOT, WORK, OUT = Path.cwd(), old.WORK, old.OUT
OWNER = "//tests/native_binary:declared_build_test"
DOC_TEST = "//docs:docs_build_test_doc-difftest"
DOC = "docs/build_test_doc.md"
PRODUCT = "6a076cf0f4865abe17d33e645bc84c53516b02d7"
R = old.REPORT
R.clear()
R.update(product=PRODUCT, tree="7a74ecb6ef8cec0d23784e4b6d468c21b4545038", carrier=os.environ["GITHUB_SHA"],
         previous=13621086928, public_inputs=old.INPUTS, phases={}, verified=False)
END = time.monotonic() + 65*60


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def phase(name, verb, target, minutes):
    row = R["phases"][name] = {"command": [verb, target], "attempts": []}
    deadline = min(END, time.monotonic() + minutes*60)
    for _ in range(16):
        bep = WORK / (name+".bep")
        bep.unlink(missing_ok=True)
        flags = ["--jobs=4", "--lockfile_mode=off", "--remote_executor=", "--remote_cache=", "--bes_backend=",
                 "--spawn_strategy=processwrapper-sandbox,local", "--build_event_json_file_path_conversion=false",
                 "--build_event_publish_all_actions", "--build_event_json_file="+str(bep)]
        if verb == "test":
            flags += ["--strategy=TestRunner=local", "--nocache_test_results", "--flaky_test_attempts=1", "--runs_per_test=1"]
        if name=="argv": flags += ["--keep_going","--build_tests_only"]
        cmd = [WORK/"bazel-8.5.0", "--nosystem_rc", "--nohome_rc", "--host_jvm_args=-Xmx4g", verb,
               *flags, *["--distdir="+str(d) for d in sorted(old.DIRS)], target]
        started = time.monotonic()
        with (WORK/(name+".console")).open("w+") as log:
            try:
                rc = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=max(1,deadline-started)).returncode
            except subprocess.TimeoutExpired:
                rc = 124
            log.seek(0)
            text = log.read()
        ev = old.events(bep)
        row["attempts"].append({"exit":rc,"seconds":round(time.monotonic()-started,3),
            "invocation":next((e["started"]["uuid"] for e in ev if "started" in e),None),
            "errors":[s[:750] for s in text.splitlines() if s.startswith("ERROR:") or "Error downloading [" in s][:16],
            "tail":[s[:550] for s in text.splitlines()[-18:]] if rc else []})
        row["exit"] = rc
        tests = row["tests"] = {}
        for e in ev:
            if "testResult" not in e:
                continue
            label=e["id"]["testResult"]["label"].removeprefix("@@")
            tr=e["testResult"]
            obs=tests[label]={"status":tr["status"],"strategy":tr.get("executionInfo",{}).get("strategy"),
                    "cached":bool(tr.get("cachedLocally") or tr.get("executionInfo",{}).get("cachedRemotely")),"files":{}}
            for f in tr.get("testActionOutput",[]):
                if f["name"] not in ("test.log","test.xml"):continue
                data=old.read_item(f)
                assert 0<len(data)<200000
                prefix=name if name!="argv" else "argv-"+label.split("/")[-1].replace(":","-")
                dest=prefix+"-"+f["name"]
                (OUT/dest).write_bytes(data)
                obs["files"][f["name"]]={"name":dest,"sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data)}
                if f["name"]=="test.xml":
                    obs["xml_clean"]=not any(x.tag in ("skipped","failure","error") for x in ET.fromstring(data).iter())
        if name=="permanent":
            row["markers"]=[e["action"].get("success",False) for e in ev if "action" in e and
                e["action"].get("type")=="RunBinary" and e["id"]["actionCompleted"].get("label","").removeprefix("@@")==OWNER+"_0__deps"]
        old.save()
        if tests or rc in (0,124) or time.monotonic()>=deadline or not old.recover(text):
            return row
    return row


def passed(row,label):
    r=row["tests"].get(label,{})
    return (row["exit"]==0 and r.get("status")=="PASSED" and r.get("strategy") in ("local","standalone")
            and not r.get("cached") and r.get("xml_clean") and set(r.get("files",{}))=={"test.log","test.xml"})



def multicall_names():
    name="coreutils-0.5.0-x86_64-unknown-linux-musl.tar.gz"
    url="https://github.com/uutils/coreutils/releases/download/0.5.0/"+name
    directory,row=old.inputs.materialize(None,None,url,"dc3ced6fa8ea63f20640b4cb610017c4afa8fdaf6318c998161bbaf5e7bfc830",5371041,328240364,old.inputs.LIMIT)
    old.DIRS.add(directory)
    old.INPUTS[url]=row
    with tarfile.open(directory/name) as archive:
        member,=[x for x in archive if x.isfile() and Path(x.name).name=="coreutils"]
        data=archive.extractfile(member).read()
    digest=hashlib.sha256(data).hexdigest()
    assert digest=="7483d3b009276cb72d83bd52ec8dd0962acf66ae996200e5286fde8098602e51"
    package=ROOT/"qualification/namecheck";package.mkdir(parents=True)
    executable=package/"coreutils"
    executable.write_bytes(data);executable.chmod(0o755)
    (package/"data.txt").write_text("checked input\n")
    (package/"BUILD.bazel").write_text('exports_files(["coreutils","data.txt"])\n')
    specs=[("default","false","true",None),("default","true","false",None),("named","false","true","false/coreutils.exe")]
    prefix="//qualification/namecheck"
    for group,label,applet,out in specs:
        target=package/group;target.mkdir(exist_ok=True)
        build=target/"BUILD.bazel"
        text=build.read_text() if build.exists() else 'load("@bazel_skylib//rules:build_test.bzl","build_test")\n'
        text+=f'build_test(name="{label}", targets=["{prefix}:data.txt"], test_runner="{prefix}:coreutils", args=["{applet}"]'
        if out:text+=f', out="{out}"'
        build.write_text(text+')\n')
    observed=phase("argv","test",prefix+"/...",4)
    expected={prefix+"/default:false":"FAILED",prefix+"/default:true":"PASSED",prefix+"/named:false":"PASSED"}
    assert observed["exit"]==1 and set(observed["tests"])==set(expected)
    witness={}
    for group,label,applet,out in specs:
        full=f"{prefix}/{group}:{label}"
        item=observed["tests"][full]
        assert item["status"]==expected[full] and not item["cached"] and set(item["files"])=={"test.log","test.xml"}
        path=ROOT/"bazel-bin/qualification/namecheck"/group/(out or label+".exe")
        assert path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest()==digest
        witness[full]={"basename":path.name, "argument":applet, "status":item["status"], "bytes":len(data), "sha256":digest}
    R["argv0"]=witness
    old.save()

def main():
    assert git("rev-parse","HEAD").decode().strip()==PRODUCT and git("rev-parse","HEAD^{tree}").decode().strip()==R["tree"]
    old.assets.DIST.mkdir(parents=True,exist_ok=True)
    old.DIRS.add(old.assets.DIST)
    old.save()
    fmt=subprocess.run([WORK/"buildifier","-mode=diff","rules/build_test.bzl","rules/BUILD","tests/native_binary/BUILD"],
                       cwd=ROOT,capture_output=True)
    R["format"]={"exit":fmt.returncode,"diff":(fmt.stdout+fmt.stderr).decode(errors="replace")[:4000]}
    try:
        test=phase("permanent","test",OWNER,16)
        R["permanent_verified"]=passed(test,OWNER) and test["markers"]==[True]
        if R["permanent_verified"]:
            binaries=ROOT/"bazel-bin/tests/native_binary"
            runner=binaries/"declared_build_test.exe"
            assert runner.is_symlink() and runner.samefile(binaries/"assertarg") and (binaries/"declared_build_test_0__deps.out").stat().st_size==0
            R["test_files"]={"marker_bytes":0,"runner_bytes":runner.stat().st_size,"runner_sha256":hashlib.sha256(runner.read_bytes()).hexdigest()}
    except (AssertionError,KeyError,ValueError,OSError,subprocess.SubprocessError) as exc:
        R["permanent_verified"]=False
        R["permanent_diagnostic"]=type(exc).__name__+" "+str(exc)[:450]
    old.save()
    try:
        multicall_names()
    except (AssertionError,KeyError,ValueError,OSError,subprocess.SubprocessError) as exc:
        R["argv0_error"]=type(exc).__name__+" "+str(exc)[:450]
        old.save()
    before=(ROOT/DOC).read_bytes()
    updater=phase("updater","run","//docs:update",35)
    R["generated_by"]=None
    if updater["exit"]==0:
        R["generated_by"]="//docs:update"
    else:
        owner=phase("generator","build","//docs:build_test",8)
        if owner["exit"]==0:
            ev=old.events(WORK/"generator.bep")
            named={e["id"]["namedSet"]["id"]:e["namedSetOfFiles"] for e in ev if "namedSetOfFiles" in e}
            def items(k):
                group=named[k]
                return [*group.get("files",[]),*[f for s in group.get("fileSets",[]) for f in items(s["id"])]]
            target,=[e["completed"] for e in ev if "completed" in e and not e["id"]["targetCompleted"].get("aspect") and e["id"]["targetCompleted"]["label"].removeprefix("@@")=="//docs:build_test"]
            group,=[g for g in target["outputGroup"] if g["name"]=="default"]
            item,=[f for k in group["fileSets"] for f in items(k["id"]) if f["name"].endswith("-docgen.md")]
            (ROOT/DOC).write_bytes(old.read_item(item))
            R["generated_by"]="//docs:build_test"
    if R["generated_by"]:
        data=(ROOT/DOC).read_bytes()
        assert len(data)<20000 and data!=before and b"marker_tool" in data and b"test_runner" in data
        (OUT/"build_test_doc.md").write_bytes(data)
        R["documentation"]={"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest(),"changed":git("diff","--name-only","--","docs").decode().splitlines()}
        old.save()
        R["docs_verified"]=passed(phase("docs","test",DOC_TEST,5), DOC_TEST)
    R["verified"]=R["permanent_verified"] and bool(R.get("argv0")) and R["format"]["exit"]==0 and R.get("docs_verified",False)
    if not R["verified"]:
        raise RuntimeError("permanent test, canonical documentation or formatting incomplete")


if __name__ == "__main__":
    try: main()
    except Exception as e:
        R["error"]=type(e).__name__+" "+str(e)[:450]
        raise
    finally: old.save()
