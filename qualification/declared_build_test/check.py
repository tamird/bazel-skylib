"""Qualify the two optional build_test tools with published uutils on Linux."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import time

import matrix as old

ROOT, WORK, OUT, PROBE = old.ROOT, old.WORK, old.OUT, old.PROBE
BASE="a920cdfe02154b916b493b4ffdd08087f0498fc7"
PRODUCT="e5b0424e0c72d44c7933b5ce17888fc3a201222e"
PREVIOUS="966a5570b6d8c02b5cd9c92f780bf792d6540a31"
V="9.3.1"
R=old.REPORT
R.update(product=PRODUCT,base=BASE,source=PRODUCT,previous=PREVIOUS,versions={},complete=False)
old.END=time.monotonic()+49*60
EDGES={}
NATIVE={}
ASSETS={
  "x86_64":("dc3ced6fa8ea63f20640b4cb610017c4afa8fdaf6318c998161bbaf5e7bfc830",5371041,328240364,62),
  "aarch64":("a1ffa4faf1e1b6267edb58a5015fbc09dd6f787d80c4d59d781876374edb0f8e",4961404,328238887,183),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def executable(path, arch):
    data=path.read_bytes()
    assert data[:4]==b"\x7fELF" and struct.unpack_from("<H",data,18)[0]==ASSETS[arch][3]
    assert b"INTERP" not in subprocess.check_output(["readelf","-lW",path]) and sha(data)==NATIVE[arch]
    return {"sha256":sha(data),"bytes":len(data),"machine":ASSETS[arch][3]}


def prepare_inputs():
    lib=(WORK/"coreutils_toolchain.bzl").read_bytes()
    assert old.source_id(lib.decode())=="6a1d6bd9ec39e898469066a5bb164e904332e8fe"
    old.assets.DIST.mkdir(parents=True,exist_ok=True)
    old.DIRS.add(old.assets.DIST)
    url="https://github.com/bazel-contrib/bazel-lib/releases/download/v3.4.0/bazel-lib-v3.4.0.tar.gz"
    directory,row=old.inputs.materialize("bazel_lib","3.4.0",url,None,None,None,old.inputs.LIMIT)
    old.DIRS.add(directory);old.INPUTS[url]=row
    for arch,(digest,size,asset,_) in ASSETS.items():
        name=f"coreutils-0.5.0-{arch}-unknown-linux-musl.tar.gz"
        sri="sha256-"+base64.b64encode(bytes.fromhex(digest)).decode()
        assert name.encode() in lib and sri.encode() in lib
        url=f"https://github.com/uutils/coreutils/releases/download/0.5.0/{name}"
        directory,row=old.inputs.materialize(None,None,url,digest,size,asset,old.inputs.LIMIT)
        old.DIRS.add(directory);old.INPUTS[url]=row
        with tarfile.open(directory/name) as archive:
            member,=[x for x in archive if Path(x.name).name=="coreutils" and x.isfile()]
            data=archive.extractfile(member).read()
        assert struct.unpack_from("<H",data,18)[0]==ASSETS[arch][3]
        NATIVE[arch]=sha(data)
    R["released_executables"]=NATIVE
    old.save()


def toolgraph(mode, opts):
    graphargs=["--nosystem_rc","--nohome_rc","aquery","--output=jsonproto","--lockfile_mode=off",
               "--jobs=4","--remote_executor=","--remote_cache=","--bes_backend=",
               "--define=SKYLIB_CHECK_MODE="+V+"-"+mode,*opts,*["--distdir="+str(d) for d in sorted(old.DIRS)],
               'mnemonic("RunBinary", deps(//cases:passes))']
    p=subprocess.run([WORK/("bazel-"+V),*graphargs],cwd=PROBE,capture_output=True,timeout=120)
    assert p.returncode==0,p.stderr.decode(errors="replace")[-450:]
    g=json.loads(p.stdout)
    fragments={str(r["id"]):r for r in g["pathFragments"]}
    def path(i):
        f=fragments[str(i)]
        return (path(f["parentId"])+"/" if f.get("parentId") else "")+f["label"]
    arts={str(a["id"]):path(a["pathFragmentId"]) for a in g["artifacts"]}
    sets={str(a["id"]):a for a in g["depSetOfFiles"]}
    targets={str(t["id"]):t["label"].removeprefix("@@") for t in g["targets"]}
    def leaves(i):
        a=sets[str(i)]
        return {arts[str(j)] for j in a.get("directArtifactIds",[])}|set().union(*(leaves(j) for j in a.get("transitiveDepSetIds",[])))
    checked={}
    root=Path(subprocess.check_output([WORK/("bazel-"+V),"--nosystem_rc","--nohome_rc","info","execution_root"],cwd=PROBE,text=True).strip())
    for a in g.get("actions",[]):
        owner=targets[str(a["targetId"])]
        m=re.fullmatch(r"//cases:passes_(\d+)__deps",owner)
        if not m: continue
        n=int(m[1])
        args=a["arguments"];out,=[arts[str(i)] for i in a["outputIds"]]
        assert len(args)==2 and args[0].endswith("/applets/touch") and args[1]==out
        assert (root/out).is_file() and (root/out).stat().st_size==0
        inputs=set().union(*(leaves(i) for i in a["inputDepSetIds"]))
        names=sorted(Path(s).name for s in inputs if re.search(r"/cases/checked_\d+_\d+[.]dat$",s))
        assert names==[f"checked_{n}_{i}.dat" for i in range(2 if n==0 else 1)]
        tool=root/args[0]; identity=executable(tool,"x86_64")
        checked[owner]={"output":out,"marker_bytes":0,"inputs":names,"executable":args[0],"elf":identity}
        EDGES[mode]=tool
    assert len(checked)==16
    return checked


def exercise(version,mode,options,physical=False):
    arm=R["versions"].setdefault(version,{})[mode]={"cases":{},"checks":{}}
    checks=arm["checks"]
    test=lambda key,label,*flags: old.run(version,mode,key,"test",[*options,"--build_tests_only",*flags,label])
    good=old.run(version,mode,"good_build","build",[*options,"//cases:passes"])
    action_rows={}
    for event in old.events(WORK/"action.bep"):
        if "action" not in event:
            continue
        ident=event["id"]["actionCompleted"]
        owner=ident.get("label","").removeprefix("@@")
        if re.fullmatch(r"//cases:passes_\d+__deps",owner):
            action=event["action"]
            assert owner not in action_rows
            action_rows[owner]={"type":action["type"],"success":action.get("success",False),
                "output":ident.get("primaryOutput",""),"strategy":action.get("strategy","")}
    arm["original_marker_actions"]=action_rows
    old.save()
    mnemonic="Genrule" if mode=="fallback" else "RunBinary"
    checks["marker_actions"]=(set(action_rows)=={f"//cases:passes_{n}__deps" for n in range(16)} and
        all(a["type"]==mnemonic and a["success"] for a in action_rows.values()))
    expected={f"checked_{n}_{i}.dat":sha(f"physical checked file {version}-{mode} {i}\n".encode()) for n in range(16) for i in range(2 if n==0 else 1)}
    checks["producer"]=good["exit"]==0 and good.get("materialized")==expected
    if not checks["producer"]:return
    meta=old.run(version,mode,"metadata","build",[*options,"//cases:inspect_passes"])
    provider=meta.get("provider",{})
    runfiles=provider.get("runfiles",[])
    markers=[f for f in runfiles if re.fullmatch(r"cases/passes_\d+__deps[.]out",f)]
    arm["file_counts"]={"default":len(provider.get("files",[])), "runfiles":len(runfiles),"markers":len(markers),
                        "checked_in_runfiles":len([p for p in runfiles if Path(p).name in expected])}
    checks["bounded"]=(meta["exit"]==0 and provider.get("files")==[provider.get("executable")] and len(markers)==16
                       and not arm["file_counts"]["checked_in_runfiles"] and len(runfiles)<=20)
    if mode=="fallback":
        checks["default_script"]=provider.get("executable")=="cases/passes.sh"
    elif physical:
        arm["marker_actions"]=toolgraph(mode,options)
        arch="aarch64" if mode=="arm64" else "x86_64"
        selected=PROBE/"bazel-bin"/provider["executable"]
        arm["runner"]=executable(selected,arch)
        checks["target_role"]=provider["executable"]=="cases/passes/build_test_runner.exe"
        if mode=="amd64":
            empty=WORK/"empty-root";empty.mkdir(mode=0o755)
            work=empty/"work";work.mkdir();work.chmod(0o777)
            shutil.copyfile(EDGES[mode],empty/"touch");shutil.copyfile(selected,empty/"build_test_runner.exe")
            (empty/"touch").chmod(0o755);(empty/"build_test_runner.exe").chmod(0o755)
            cmd=["sudo","-n","chroot","--userspec=65534:65534",empty]
            entries=sorted(str(f.relative_to(empty)) for f in empty.rglob("*"))
            rc=[subprocess.run([*cmd,*argv],capture_output=True).returncode for argv in
                (["/touch","/work/marker"],["/build_test_runner.exe","true"],["/build_test_runner.exe","false"])]
            arm["empty_root"]={"before":entries,"exit":rc,"marker_bytes":(work/"marker").stat().st_size}
            checks["empty_root"]=rc==[0,0,1] and arm["empty_root"]["marker_bytes"]==0 and entries==["build_test_runner.exe","touch","work"]
    if mode=="arm64":
        old.save()
        return
    checks["passes"]=old.good_test(test("selected_test","//cases:passes"),"//cases:passes")
    for k,label in (("broken","//cases:fails"),("suite","//cases:broken_suite"),("transitive","//cases:inherits_runfiles")):
        r=test(k,label,"--run_validations=false")
        checks[k]=old.negative(r) and any(a["owner"].removeprefix("@@")=="//cases:broken" for a in r["actions"])
    if mode=="amd64":
        checks["single"]=old.run(version,mode,"single","build",["//cases:requires_one"])["exit"]==0
        ft=test("filtered","//wild/...","--test_tag_filters=-omit-me")
        checks["filtered"]=old.good_test(ft,"//wild:selected") and set(l for l,x in ft["tests"].items() if x["status"]=="PASSED")=={"//wild:selected"}
        for name in ("marker_only","runner_only"):
            label="//roles:"+name
            checks[name]=old.good_test(test(name,label),label)
        neg=test("false_runner","//roles:negative")
        a=neg["tests"].get("//roles:negative",{})
        checks["real_false"]=neg["exit"]!=0 and a.get("status")=="FAILED" and not a.get("cached") and set(a.get("files",{}))=={"test.log","test.xml"}
    old.save()



def documentation():
    doc=R["documentation"]={"verified":False}
    ex=WORK/("bazel-"+V)
    fmt=WORK/"buildifier"
    if fmt.exists():
        p=subprocess.run([fmt,"-mode=diff","rules/BUILD","rules/build_test.bzl"],cwd=ROOT,capture_output=True)
        doc["buildifier_exit"]=p.returncode
        if p.stdout or p.stderr:doc["format_diff"]=(p.stdout+p.stderr).decode(errors="replace")[:3800]
    bep=WORK/"stardoc.bep"
    for _ in range(4):
        bep.unlink(missing_ok=True)
        args=[ex,"--nosystem_rc","--nohome_rc","build","--jobs=4","--lockfile_mode=off","--remote_executor=","--remote_cache=",
              "--bes_backend=","--build_event_json_file_path_conversion=false","--build_event_json_file="+str(bep),
              *["--distdir="+str(d) for d in sorted(old.DIRS)],"//docs:build_test"]
        try:
            p=subprocess.run(args,cwd=ROOT,capture_output=True,timeout=180)
        except subprocess.TimeoutExpired:
            doc["exit"]=124;return
        doc["exit"]=p.returncode
        if p.returncode:
            log=p.stderr.decode(errors="replace")
            doc["diagnostics"]=[l[:450] for l in log.splitlines() if l.startswith("ERROR:") or "Error downloading [" in l][:7]
            if old.recover(log): continue
            return
        ev=old.events(bep)
        names={e["id"]["namedSet"]["id"]:e["namedSetOfFiles"] for e in ev if "namedSetOfFiles" in e}
        def listfiles(key):
            group=names[key]
            return [*group.get("files",[]),*[f for k in group.get("fileSets",[]) for f in listfiles(k["id"])]]
        owner,=[e["completed"] for e in ev if "completed" in e and not e["id"]["targetCompleted"].get("aspect") and
                e["id"]["targetCompleted"]["label"].removeprefix("@@")=="//docs:build_test"]
        group,=[g for g in owner["outputGroup"] if g["name"]=="default"]
        item,=[f for k in group["fileSets"] for f in listfiles(k["id"]) if f["name"].endswith(".md")]
        data=old.read_item(item)
        assert b"marker_tool" in data and b"test_runner" in data and len(data)<20000
        (OUT/"build_test_doc.md").write_bytes(data)
        doc.update(verified=True,sha256=sha(data),bytes=len(data))
        return

def main():
    assert old.git("rev-parse","HEAD^").decode().strip()==PRODUCT and old.git("rev-parse",PRODUCT+"^").decode().strip()==BASE
    prepare_inputs()
    versions=("7.7.0","8.5.0",V)
    for v in versions:
        for mode,opts in ([("fallback",[]),("amd64",[]),("arm64",["--platforms=//tools:arm64"])] if v==V else [("declared",[])]):
            for pkg in ("cases","wild"):
                file=PROBE/pkg/"BUILD.bazel"; original=old.git("show",PREVIOUS+":qualification/build_test/"+pkg+"/BUILD.bazel").decode()
                file.write_text(original if mode=="fallback" else original.replace('load("@bazel_skylib//rules:build_test.bzl", "build_test")','load("//tools:defs.bzl", "build_test")'))
            try:
                exercise(v,mode,opts,physical=(v==V and mode!="fallback"))
            except (OSError,ValueError,KeyError,AssertionError,subprocess.SubprocessError) as e:
                R["versions"][v][mode]["error"]=type(e).__name__+" "+str(e)[:420]
                old.save()
        subprocess.run([WORK/("bazel-"+v),"--nosystem_rc","--nohome_rc","shutdown"],cwd=PROBE,timeout=90)
    for v,arms in R["versions"].items():
        for mode,a in arms.items():
            assert "error" not in a and a["checks"] and all(a["checks"].values()), (v,mode,a["checks"])
            if mode!="arm64":assert all(a["checks"].get(k) for k in ("passes","broken","transitive","suite","bounded"))
            if mode=="amd64":assert a["checks"].get("empty_root") and a["checks"].get("real_false")
            if mode=="arm64":assert a["checks"].get("target_role") and len(a.get("marker_actions",{}))==16
    R["complete"]=True
    old.save()
    try:
        documentation()
    except (AssertionError,KeyError,ValueError,OSError,subprocess.SubprocessError) as error:
        R["documentation_error"]=type(error).__name__+" "+str(error)[:380]


if __name__=="__main__":
    try:
        main()
    except Exception as e:
        R["error"]=type(e).__name__+" "+str(e)[:480]
        raise
    finally:
        old.save()
