"""Compare the declared build_test contracts on one native Linux worker."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import unquote, urlsplit

import public_assets as assets
import tldk_public_inputs as inputs

WORK = Path(os.environ["RUNNER_TEMP"]) / "skylib-build-test"
OUT = WORK / "evidence"
ROOT = Path.cwd()
PROBE = ROOT / "qualification" / "build_test"
RULE = ROOT / "rules" / "build_test.bzl"
PRODUCT = "b20ce8f04a864694bbc0462370f4a171a9014a4d"
BASE = "a920cdfe02154b916b493b4ffdd08087f0498fc7"
VERSIONS = ("7.7.0","8.5.0","9.3.1")
REPORT = {"source": PRODUCT, "base": BASE, "carrier":os.environ["GITHUB_SHA"],"versions":{}, "complete": False}
END = time.monotonic() + 62*60
DIRS = set()
INPUTS = REPORT["public_inputs"] = {}
OWNERS = ("passes","fails","broken_suite","inherits_runfiles","requires_one","inspect_passes")
TAG = "SKYLIB_INTENTIONAL_BROKEN_INPUT"


def git(*args):
    return subprocess.check_output(["git",*args],cwd=ROOT)


def source_id(s):
    b=s.encode()
    return hashlib.sha1(b"blob "+str(len(b)).encode()+b"\0"+b).hexdigest()


def save():
    tmp=OUT/"result.tmp"
    tmp.write_text(json.dumps(REPORT,indent=2,sort_keys=True)+"\n")
    tmp.replace(OUT/"result.json")


def variants(original, candidate):
    key = "        OutputGroupInfo(default = depset(transitive = [\n            target[DefaultInfo].files\n            for target in ctx.attr.targets\n        ])),\n"
    assert candidate.count(key) == 1 and source_id(candidate) == "bccf1891812ecd474200440552ee46b720c58ffb"
    without = candidate.replace(key, "")
    deps = "[target[DefaultInfo].files for target in ctx.attr.targets]"
    files = without.replace("files = depset([executable]),", "files = depset([executable], transitive = "+deps+"),")
    data = without.replace("runfiles = ctx.runfiles(),", "runfiles = ctx.runfiles(transitive_files = depset(transitive = "+deps+")),")
    hidden = candidate.replace("OutputGroupInfo(default =", "OutputGroupInfo(_hidden_top_level_INTERNAL_ =")
    native = candidate.replace("    extension =", """    if ctx.executable.runner:
        executable = ctx.actions.declare_file(ctx.label.name + (".exe" if is_windows else ""))
        ctx.actions.symlink(output = executable, target_file = ctx.executable.runner, is_executable = True)
        runfiles = ctx.runfiles(files = [ctx.executable.runner]).merge(ctx.attr.runner[DefaultInfo].default_runfiles)
    else:
        executable = _success_script(ctx, is_windows)
        runfiles = ctx.runfiles()
    return _providers(ctx, executable, runfiles)

def _success_script(ctx, is_windows):
    extension =""")
    native = native.replace("    # Build and test requests", "    return executable\n\ndef _providers(ctx, executable, runfiles):\n    # Build and test requests")
    native = native.replace("runfiles = ctx.runfiles(),", "runfiles = runfiles,").replace('        "targets": attr.label_list(', '        "runner": attr.label(allow_files = True, executable = True, cfg = "target"),\n        "targets": attr.label_list(')
    assert len({original,candidate,files,data,hidden,native})==6
    return {"stock":original,"files":files,"group":candidate,"runfiles":data,"hidden":hidden,"native":native}


def recover(text):
    before=len(DIRS)
    for group,dest in re.findall(r"Error downloading \[([^\]\r\n]+)\] to (\S+)",text):
        external=re.search(r"/external/([\w.-]+)(?:[+~][^/]*)?/",dest)
        for url in inputs.FAILED_URL.findall(group):
            if not external or url in INPUTS or len(INPUTS)>=28:
                continue
            try:
                parts=inputs.components(url)
                module=external[1]
                ver=parts[3].removeprefix("refs/tags/").removeprefix("v")
                digest=inputs.registry(module,ver,url)
                room=inputs.LIMIT-sum(r.get("bytes",0) for r in INPUTS.values())
                try:
                    directory,row=inputs.materialize(None,None,url,digest,None,None,room)
                except (ValueError,KeyError,AssertionError,OSError):
                    if not assets.URL.fullmatch(url):
                        raise
                    directory,row=assets.release_distfile(url,assets.DIST,remaining_bytes=room)
                    assert row["sha256"]==digest
                row.update(module=module,version=ver,registry=assets.REGISTRY)
                DIRS.add(directory)
            except (OSError,ValueError,KeyError,AssertionError) as exc:
                row={"unavailable":type(exc).__name__+" "+str(exc)[:180]}
            INPUTS[url]=row
    save()
    return len(DIRS)>before


def events(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines(keepends=True) if line.endswith("\n")]


def read_item(item):
    uri=urlsplit(item["uri"])
    assert uri.scheme=="file" and not uri.netloc
    data=Path(unquote(uri.path)).read_bytes()
    digest=hashlib.sha256(data).hexdigest()
    assert int(item.get("length",len(data)))==len(data) and (not item.get("digest") or item["digest"]==digest)
    return data


def run(v,mode,case,verb,args,attempt=0):
    row=REPORT["versions"][v][mode]["cases"][case]={"command":[verb,*args]}
    path=WORK/"action.bep"
    path.unlink(missing_ok=True)
    options=["--lockfile_mode=off","--jobs=4","--enable_bzlmod","--remote_executor=","--remote_cache=","--bes_backend=",
             "--build_event_json_file_path_conversion=false","--build_event_publish_all_actions","--build_event_json_file="+str(path)]
    options += ["--define=SKYLIB_CHECK_MODE="+v+"-"+mode,*["--distdir="+str(d) for d in sorted(DIRS)]]
    if verb in ("build","test"):
        options += ["--spawn_strategy=local","--strategy=TestRunner=local"]
    if verb=="test":
        options += ["--nocache_test_results","--flaky_test_attempts=1","--test_timeout=45"]
    cmd=[WORK/("bazel-"+v),"--nosystem_rc","--nohome_rc",verb,*options,*args]
    try:
        p=subprocess.run(cmd,cwd=PROBE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=min(240,max(1,END-time.monotonic())))
        text=p.stderr.decode(errors="replace")
        row["exit"]=p.returncode
    except subprocess.TimeoutExpired as exc:
        text=(exc.stderr or b"").decode(errors="replace")
        row["exit"]=124
        p=None
    row["errors"]=[s[:450] for s in text.splitlines() if s.startswith("ERROR:") or "Error downloading [" in s][:7]
    row["marker"]=TAG in text
    if row["exit"] and "Error downloading [" in text and attempt<6 and recover(text):
        return run(v,mode,case,verb,args,attempt+1)
    original=events(path)
    row["invocation"]=next((e["started"]["uuid"] for e in original if "started" in e),None)
    row["actions"]=[{"owner":e["id"]["actionCompleted"].get("label",""),"mnemonic":e["action"]["type"],
                     "success":e["action"].get("success",False)} for e in original if "action" in e
                    and e["action"]["type"] in ("CheckedInputFailure","Genrule","TestRunner")][:35]
    row["tests"]={}
    for e in original:
        if "testResult" not in e:
            continue
        owner=e["id"]["testResult"]["label"].removeprefix("@@")
        item=e["testResult"]
        result=row["tests"][owner]={"status":item["status"],"strategy":item.get("executionInfo",{}).get("strategy"),
             "cached":bool(item.get("cachedLocally") or item.get("executionInfo",{}).get("cachedRemotely")),"files":{}}
        for f in item.get("testActionOutput",[]):
            if f["name"] in ("test.xml","test.log"):
                data=read_item(f)
                assert len(data)<65536
                name="-".join([v,mode,case,owner.split(":")[-1],f["name"]])
                (OUT/name).write_bytes(data)
                result["files"][f["name"]]={"name":name,"sha256":hashlib.sha256(data).hexdigest()}
    if verb=="aquery" and p and p.returncode==0:
        graph=json.loads(p.stdout)
        actions=graph.get("actions",[])
        called={str(t["id"]):t["label"].removeprefix("@@") for t in graph.get("targets",[])}
        row["marker_owners"]=sorted(called[str(a["targetId"])] for a in actions if a["mnemonic"]=="Genrule"
            and re.fullmatch(r"//cases:passes_[0-9]+__deps",called.get(str(a["targetId"]),"")))
        row["configured_mnemonics"]={n:sum(a["mnemonic"]==n for a in actions)
                                     for n in ("Genrule","TestRunner","FileWrite","ExecutableSymlink")}
    if case=="metadata" and row["exit"]==0:
        row["provider"]=json.loads((PROBE/"bazel-bin/cases/inspect_passes.json").read_bytes())
    if case=="good_build" and row["exit"]==0:
        named={e["id"]["namedSet"]["id"]:e["namedSetOfFiles"] for e in original if "namedSetOfFiles" in e}
        def files(key):
            record=named[key]
            return [*record.get("files",[]),*[f for s in record.get("fileSets",[]) for f in files(s["id"])]]
        target,=[e["completed"] for e in original if "completed" in e and not e["id"]["targetCompleted"].get("aspect") and e["id"]["targetCompleted"]["label"].removeprefix("@@")=="//cases:passes"]
        group,=[g for g in target["outputGroup"] if g["name"]=="default"]
        row["advertised"]=sorted(f["name"] for s in group.get("fileSets",[]) for f in files(s["id"]))
        fs=sorted((PROBE/"bazel-bin/cases").glob("checked_*_*.dat"))
        row["materialized"]={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in fs}
    save()
    return row


def good_test(row,label):
    return row["exit"]==0 and label in row["tests"] and row["tests"][label]["status"]=="PASSED" and not row["tests"][label]["cached"] and set(row["tests"][label]["files"])=={"test.xml","test.log"}


def negative(row):
    return row["exit"]!=0 and row["marker"] and any(a["mnemonic"]=="CheckedInputFailure" and not a["success"] for a in row["actions"]) and not any(x["status"]=="PASSED" for x in row["tests"].values())


def arm(v,m,source):
    RULE.write_text(source)
    rec=REPORT["versions"][v][m]={"source_blob":source_id(source),"cases":{}}
    checks=rec["observations"]={}
    if m=="native":
        f=git("show",BASE+":tests/native_binary/assertarg.cc")
        assert git("rev-parse",BASE+":tests/native_binary/assertarg.cc").decode().strip()=="65066358c287f27f763d4a30fec3ebcee99e4437"
        (PROBE/"native/assertarg.cc").write_bytes(f)
        r=run(v,m,"declared_runner","test",["--build_tests_only","//native:chosen"])
        checks["declared_runner"]=good_test(r,"//native:chosen")
        if checks["declared_runner"]:
            exe=PROBE/"bazel-bin/native/chosen"
            binary=PROBE/"bazel-bin/native/assertarg"
            assert exe.is_symlink() and exe.samefile(binary)
            d=binary.read_bytes()
            assert d.startswith(b"\x7fELF") and b"INTERP" not in subprocess.check_output(["readelf","-lW",binary])
            empty=WORK/"empty"
            empty.mkdir()
            staged=empty/"chosen"
            staged.write_bytes(d)
            staged.chmod(0o755)
            outcomes=[subprocess.run(["sudo","-n","chroot","--userspec=65534:65534",empty,"/chosen",arg],capture_output=True) for arg in ("a b","wrong")]
            rec["empty_root"]={"files":[p.name for p in empty.iterdir()],"sha256":hashlib.sha256(d).hexdigest(),
                               "exit":[p.returncode for p in outcomes],"bad_arg_reported":b"expected (a b)" in outcomes[1].stderr}
            checks["empty_root"]=rec["empty_root"]["exit"]==[0,1] and rec["empty_root"]["bad_arg_reported"]
        save()
        return
    good=run(v,m,"good_build","build",["//cases:passes"])
    expected={f"checked_{n}_{i}.dat":hashlib.sha256(f"physical checked file {v}-{m} {i}\n".encode()).hexdigest()
              for n in range(16) for i in range(2 if n==0 else 1)}
    checks["good_build"]=good["exit"]==0 and good.get("materialized")==expected
    if not checks["good_build"]:
        if m=="hidden" and good["exit"] and any("hidden_top_level" in s for s in good["errors"]):
            checks["analysis_failure"]="Bazel rejected the hidden output group"
        return
    meta=run(v,m,"metadata","build",["//cases:inspect_passes"])
    checked={"cases/"+path for path in expected}
    provider=meta.get("provider",{})
    cardinality=rec["cardinality"]={key:{"total":len(provider.get(key,[])),
        "checked":len(checked & set(provider.get(key,[]))),
        "markers":sum(bool(re.fullmatch(r"cases/passes_[0-9]+__deps[.]out",p)) for p in provider.get(key,[]))}
        for key in ("files","group","runfiles")}
    checks["metadata"]=(meta["exit"]==0 and provider.get("executable")=="cases/passes.sh" and
        cardinality["files"]["total"]==(18 if m=="files" else 1) and
        cardinality["files"]["checked"]==(17 if m=="files" else 0) and
        cardinality["group"]["checked"]==(17 if m=="group" else 0) and
        cardinality["runfiles"]["checked"]==(17 if m=="runfiles" else 0) and
        cardinality["runfiles"]["markers"]==(16 if m=="stock" else 0))
    r=run(v,m,"selected_test","test",["--build_tests_only","//cases:passes"])
    checks["selected_test"]=good_test(r,"//cases:passes")
    for name,verb,opts in [
        ("broken_build","build",["//cases:fails"]),
        ("broken_test","test",["--build_tests_only","//cases:fails"]),
        ("broken_suite","test",["--build_tests_only","//cases:broken_suite"]),
        ("other_output_group","test",["--build_tests_only","--output_groups=unrelated","--run_validations=false","//cases:fails"]),
        ("runfiles_consumer","test",["--build_tests_only","--run_validations=false","//cases:inherits_runfiles"]),
    ]:
        if v!="9.3.1" and name in ("broken_build","other_output_group"):
            continue
        r=run(v,m,name,verb,opts)
        if r["exit"]:
            checks[name]=negative(r)
        elif verb=="build" or good_test(r,"//cases:inherits_runfiles" if name=="runfiles_consumer" else "//cases:fails"):
            checks[name]="accepted"
        else:
            checks[name]=False
    r=run(v,m,"single","build",["//cases:requires_one"])
    checks["single"]="accepted" if r["exit"]==0 else ("rejected_multiple_files" if any("single" in s.lower() or "one file" in s for s in r["errors"]) else False)
    if v!="9.3.1":
        save()
        return
    r=run(v,m,"filtered","test",["--build_tests_only","--test_tag_filters=-omit-me","//wild/..."])
    checks["filtered"]=good_test(r,"//wild:selected") and set(k for k,x in r["tests"].items() if x["status"]=="PASSED")=={"//wild:selected"}
    r=run(v,m,"wild_build","build",["--build_tag_filters=-omit-me","//wild/..."])
    checks["wild_build"]=r["exit"]==0 and not r["marker"]
    r=run(v,m,"graph","aquery",["--output=jsonproto","deps(//cases:passes)"])
    checks["graph"]=(r["exit"]==0 and r.get("marker_owners")==sorted(
        f"//cases:passes_{i}__deps" for i in range(16 if m=="stock" else 0)))
    save()


def main():
    assert os.uname().machine=="x86_64" and os.getuid()!=0
    save()
    original=git("show",BASE+":rules/build_test.bzl").decode()
    candidate=git("show",PRODUCT+":rules/build_test.bzl").decode()
    assert git("rev-parse","HEAD^").decode().strip()==PRODUCT and git("rev-parse",PRODUCT+"^").decode().strip()==BASE
    choices=variants(original,candidate)
    assets.DIST.mkdir(parents=True,exist_ok=True)
    DIRS.add(assets.DIST)
    for v in VERSIONS:
        REPORT["versions"][v]={}
        for m,s in choices.items():
            if v!="9.3.1" and m not in ("stock","group"):
                continue
            if END-time.monotonic()<120:
                raise RuntimeError("reserved upload deadline")
            try:
                arm(v,m,s)
                if m=="stock" and not REPORT["versions"][v][m]["observations"].get("good_build"):
                    break
            except (AssertionError, OSError, KeyError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
                REPORT["versions"][v][m]["harness_error"]=type(e).__name__+" "+str(e)[:400]
                save()
        subprocess.run([WORK/("bazel-"+v),"--nosystem_rc","--nohome_rc","shutdown"],cwd=PROBE,timeout=100)
    for v, arms in REPORT["versions"].items():
        for m in (("stock","files","group","runfiles") if v=="9.3.1" else ("stock","group")):
            obs=arms[m]["observations"]
            assert all(obs.get(k) is True for k in ("good_build","metadata","selected_test","broken_test","broken_suite")), (v,m,obs)
            if v=="9.3.1":
                assert all(obs.get(k) is True for k in ("broken_build","filtered","wild_build","graph")), (v,m,obs)
                assert obs.get("other_output_group") in (True,"accepted"), (v,m,obs)
            assert obs.get("single") in ("accepted","rejected_multiple_files") and obs.get("runfiles_consumer") in (True,"accepted"), (v,m,obs)
    hidden=REPORT["versions"]["9.3.1"]["hidden"]["observations"]
    assert hidden.get("analysis_failure") or (all(hidden.get(k) is True for k in ("good_build","metadata","broken_build","broken_test","broken_suite","filtered","wild_build","graph")) and
        all(hidden.get(k) in (True,"accepted") for k in ("other_output_group","runfiles_consumer")) and hidden["single"]=="accepted"), hidden
    assert REPORT["versions"]["9.3.1"]["native"]["observations"].get("empty_root")
    REPORT["complete"]=True


if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        REPORT["error"]=type(exc).__name__+" "+str(exc)[:500]
        raise
    finally:
        save()
