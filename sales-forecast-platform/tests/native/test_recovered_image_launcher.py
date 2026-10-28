"""Host stdlib-only tests. No frameworks, models, databases or real Docker calls."""
import contextlib
import io
import sys
import os
import socket
import time
import hashlib
import importlib.util
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('recovered_image_launcher_test_target', HERE/'run_recovered_image.py')
launcher = importlib.util.module_from_spec(spec); spec.loader.exec_module(launcher)


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='p0-test-')
        self.addCleanup(self.temp.cleanup)
        self.evidence = Path(self.temp.name)
        # Use the actual operator-pinned evidence; no substitute expected digest.
        self.original = launcher._MANIFEST
        for name in ('fresh-manifest.json',*launcher._PROOFS):
            shutil.copyfile(self.original.parent/name, self.evidence/name)
        self.manifest = json.loads((self.evidence/'fresh-manifest.json').read_text())
        self.base = self.manifest['fresh_provenance']['base']
        self.calls = []; self.image_fault = None; self.container_fault = None
        self.start = 0; self.rm = 0; self.remains = ''; self.create = 0
        self.addCleanup(patch.stopall)
        patch.object(launcher,'_MANIFEST',self.evidence/'fresh-manifest.json').start()
        patch.object(launcher.subprocess,'run',side_effect=self.docker).start()

    def docker(self, command, **kw):
        self.calls.append((command,kw))
        self.assertEqual(command[:2],['docker','--host=unix:///var/run/docker.sock'])
        self.assertNotIn('shell',kw)
        self.assertFalse(set(kw['env']).intersection(('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH')))
        args=command[2:];code=0;out='';err=''
        if args[:2]==['image','inspect']:
            self.assertEqual(args[2],launcher._IMAGE)
            image={'Id':launcher._IMAGE,'Os':'linux','Architecture':'amd64','Config':{'Entrypoint':None},'RootFS':{'Layers':self.base['base_diff_ids']+['sha256:extra']}}
            if self.image_fault=='missing':code=1;err='PRIVATE_DOCKER_PATH'
            elif self.image_fault=='id':image['Id']='sha256:'+'f'*64
            elif self.image_fault=='platform':image['Architecture']='arm64'
            elif self.image_fault=='base':image['RootFS']['Layers']=[]
            elif self.image_fault=='entrypoint':image['Config']['Entrypoint']=['unsafe']
            elif self.image_fault=='json':out='not-json'
            if self.image_fault!='json':out=json.dumps([image])
        elif args[0]=='create':code=self.create
        elif args[0]=='inspect':
            root=launcher._PROJECT.resolve()
            data={'Image':launcher._IMAGE,'Config':{'Image':launcher._IMAGE,'User':'1000:1000','Env':[k+'=' for k in launcher._PROXY_KEYS]},
              'HostConfig':{'NetworkMode':'none','ReadonlyRootfs':True,'Privileged':False,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges'],
                 'Memory':1073741824,'MemorySwap':1073741824,'NanoCpus':1000000000,'PidsLimit':64,
                 'Ulimits':[{'Name':'cpu','Hard':180,'Soft':180}],'Tmpfs':{'/tmp':'rw,nosuid,nodev,size=256m'}},
              'Mounts':[{'Type':'bind','Source':str(root/d),'Destination':'/app/'+d,'RW':False} for d in ('src','tests')]}
            f=self.container_fault
            if f=='image':data['Image']='sha256:other'
            elif f=='network':data['HostConfig']['NetworkMode']='host'
            elif f=='root':data['HostConfig']['ReadonlyRootfs']=False
            elif f=='privileged':data['HostConfig']['Privileged']=True
            elif f=='user':data['Config']['User']='0:0'
            elif f=='caps':data['HostConfig']['CapDrop']=[]
            elif f=='resources':data['HostConfig']['Memory']=0
            elif f=='writable':data['Mounts'][0]['RW']=True
            elif f=='swapped':
                data['Mounts'][0]['Destination'],data['Mounts'][1]['Destination']='/app/tests','/app/src'
            elif f=='pid':data['HostConfig']['PidMode']='host'
            elif f=='ipc':data['HostConfig']['IpcMode']='host'
            elif f=='production':data['Mounts'].append({'Type':'bind','Source':'/root','Destination':'/root','RW':False})
            elif f=='credentials':data['Config']['Env'].append('AWS_SECRET_ACCESS_KEY=PRIVATE')
            elif f=='proxy':data['Config']['Env'][0]='HTTP_PROXY=PRIVATE'
            out=json.dumps([data])
        elif args[0]=='start':
            if self.start=='timeout':raise subprocess.TimeoutExpired(command,1)
            if self.start=='cancel':raise KeyboardInterrupt()
            code=self.start
        elif args[0]=='rm':code=self.rm
        elif args[0]=='ps':out=self.remains
        else:self.fail('unexpected command '+repr(args))
        return SimpleNamespace(returncode=code,stdout=out,stderr=err)

    def launch(self, **kw):
        return launcher.run(['tests/native/test_recovered_worker_smoke.py'],use_recovered_image=True,**kw)

    def test_explicit_valid_selection(self):
        result=launcher.validate_recovery(use_recovered_image=True)
        self.assertEqual(result,{'image_id':launcher._IMAGE,'manifest_sha256':launcher._MANIFEST_SHA,'classification':'TEST_ENVIRONMENT_ONLY'})

    def test_disabled_by_default(self):
        with self.assertRaises(launcher.CompatibilityError):launcher.run(['tests/native/test_recovered_worker_smoke.py'])
        self.assertEqual(self.calls,[])

    def test_no_environment_opt_in(self):
        with patch.dict('os.environ',{'USE_RECOVERED_IMAGE':'1','B1_ISOLATED_NATIVE_TEST':'1'}):
            with self.assertRaises(launcher.CompatibilityError):launcher.validate_recovery()
        self.assertFalse(self.calls)

    def test_opt_in_is_exact_true(self):
        with self.assertRaises(launcher.CompatibilityError):launcher.validate_recovery(use_recovered_image=1)

    def test_no_caller_image_parameter(self):
        with self.assertRaises(TypeError):launcher.run(['tests/native/test_recovered_worker_smoke.py'],image_id=launcher._IMAGE,use_recovered_image=True)
        self.assertFalse(self.calls)

    def test_no_caller_manifest_digest_parameter(self):
        with self.assertRaises(TypeError):launcher.validate_recovery(use_recovered_image=True,manifest_sha256='f'*64)

    def test_missing_manifest(self):
        launcher._MANIFEST.unlink()
        with self.assertRaises(launcher.CompatibilityError):self.launch()
        self.assertFalse(self.calls)

    def test_modified_manifest(self):
        launcher._MANIFEST.write_bytes(launcher._MANIFEST.read_bytes()+b' ')
        with self.assertRaises(launcher.CompatibilityError):self.launch()
        self.assertFalse(self.calls)

    def test_substituted_manifest_image(self):
        self.manifest['image']['id']='sha256:'+'f'*64
        launcher._MANIFEST.write_text(json.dumps(self.manifest))
        with self.assertRaises(launcher.CompatibilityError):self.launch()
        self.assertFalse(self.calls)

    def test_symlink_manifest(self):
        valid=self.evidence/'valid';launcher._MANIFEST.rename(valid);launcher._MANIFEST.symlink_to(valid)
        with self.assertRaises(launcher.CompatibilityError):self.launch()

    def test_missing_provenance(self):
        (self.evidence/'distribution-provenance.json').unlink()
        with self.assertRaises(launcher.CompatibilityError):self.launch()
        self.assertFalse(self.calls)

    def test_tampered_provenance(self):
        (self.evidence/'base-provenance.json').write_text('{}')
        with self.assertRaises(launcher.CompatibilityError):self.launch()
        self.assertFalse(self.calls)

    def test_strict_json(self):
        for raw in (b'{"x":1,"x":2}',b'{"x":NaN}',b'\xff',b'{'):
            with self.assertRaises(Exception):launcher._json(raw)

    def test_run_and_fixed_arguments(self):
        self.assertEqual(self.launch(),0)
        c=next(c for c,k in self.calls if c[2]=='create')
        for option,value in (('--network','none'),('--user','1000:1000'),('--cap-drop','ALL'),('--security-opt','no-new-privileges'),('--memory','1g'),('--cpus','1'),('--pids-limit','64')):
            self.assertEqual(c[c.index(option)+1],value)
        self.assertIn('--read-only',c);self.assertIn(launcher._IMAGE,c)
        self.assertNotIn('r7-05b1-recovered-test:20261027',c)
        self.assertEqual(sum(c[2:4]==['image','inspect'] for c,k in self.calls),2)
        self.assertEqual([c[2] for c,k in self.calls][-2:],['rm','ps'])

    def test_timeout_cleanup(self):
        self.start='timeout';self.assertEqual(self.launch(timeout=.1),124)
        self.assertEqual([c[2] for c,k in self.calls][-2:],['rm','ps'])

    def test_failure_exit_cleanup(self):
        self.start=7;self.assertEqual(self.launch(),7)
        self.assertEqual([c[2] for c,k in self.calls][-2:],['rm','ps'])

    def test_interrupt_cleanup(self):
        self.start='cancel'
        with self.assertRaises(KeyboardInterrupt):self.launch()
        self.assertEqual([c[2] for c,k in self.calls][-2:],['rm','ps'])

    def test_create_failure_cleanup(self):
        self.create=1
        with self.assertRaises(launcher.CompatibilityError):self.launch()
        self.assertFalse(any(c[2]=='start' for c,k in self.calls))
        self.assertEqual([c[2] for c,k in self.calls][-2:],['rm','ps'])

    def test_cleanup_failure_is_not_success(self):
        self.remains='container'
        with self.assertRaises(launcher.CompatibilityError):self.launch()

    def test_second_image_check_rejects_before_start(self):
        real=self.docker;count=0
        def changed(command,**kw):
            nonlocal count
            if command[2:4]==['image','inspect']:
                count+=1
                if count==2:self.image_fault='id'
            return real(command,**kw)
        with patch.object(launcher.subprocess,'run',side_effect=changed):
            with self.assertRaises(launcher.CompatibilityError):self.launch()
        self.assertFalse(any(c[2]=='start' for c,k in self.calls))

    def test_cli_requires_explicit_selection(self):
        with patch.object(sys,'argv',['run_recovered_image.py','tests/native/test_recovered_worker_smoke.py']),contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:runpy.run_path(str(HERE/'run_recovered_image.py'),run_name='__main__')
        self.assertEqual(caught.exception.code,2);self.assertFalse(self.calls)

    def test_cli_explicit_selection(self):
        with patch.object(sys,'argv',['run_recovered_image.py','--use-validated-recovered-image','tests/native/test_recovered_worker_smoke.py']):
            with self.assertRaises(SystemExit) as caught:runpy.run_path(str(HERE/'run_recovered_image.py'),run_name='__main__')
        self.assertEqual(caught.exception.code,0)

    def test_default_b3_guard_unchanged(self):
        path=HERE/'run_b3.py'
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),'27c965cb607f614976515777b6dc1cd3c617b763ebc760e35a00bc1d3b880012')
        original=runpy.run_path(str(path))
        self.assertEqual(original['IMAGE'],'sha256:8adc380a27f6dfde49066ac0ed323678783ca58e553272d3409e0fe9d04cf7c8')
        with patch.object(subprocess,'check_output',return_value=launcher._IMAGE+'\n'):
            with self.assertRaisesRegex(ValueError,'pinned native environment unavailable'):original['run'](['tests/native/test_recovered_worker_smoke.py'])
        self.assertFalse(self.calls)


# Separate unittest cases, so reported counts are not inflated by subtest loops.
def bad_image(fault):
    def test(self):
        self.image_fault=fault
        with self.assertRaises(launcher.CompatibilityError) as caught:self.launch()
        self.assertNotIn('PRIVATE',str(caught.exception))
        self.assertFalse(any(c[2] in ('create','pull','build','start') for c,k in self.calls))
    return test
for fault in ('missing','id','platform','base','entrypoint','json'):setattr(Tests,'test_image_'+fault,bad_image(fault))

def bad_container(fault):
    def test(self):
        self.container_fault=fault
        with self.assertRaises(launcher.CompatibilityError):self.launch()
        self.assertFalse(any(c[2]=='start' for c,k in self.calls))
        self.assertEqual([c[2] for c,k in self.calls][-2:],['rm','ps'])
    return test
for fault in ('image','network','root','privileged','user','caps','resources','writable','swapped','pid','ipc','production','credentials','proxy'):
    setattr(Tests,'test_container_'+fault,bad_container(fault))

def bad_arguments(arguments):
    def test(self):
        with self.assertRaises(launcher.CompatibilityError):launcher.run(arguments,use_recovered_image=True)
        self.assertFalse(self.calls)
    return test
for i,args in enumerate(([],['-s'],['--image=evil'],['/etc/passwd'],['tests/../evil.py'],['tests/a.py;touch'],['tests/a.py\n'],['$(id)'])):
    setattr(Tests,'test_arguments_'+str(i),bad_arguments(args))


def bad_timeout(value):
    def test(self):
        with self.assertRaises(launcher.CompatibilityError):self.launch(timeout=value)
        self.assertFalse(self.calls)
    return test
for i,value in enumerate((0,241,float('nan'),True)):setattr(Tests,'test_invalid_timeout_'+str(i),bad_timeout(value))


# Each potentially blocking FIFO/TOCTOU probe runs in an independently bounded
# subprocess. The real pinned manifest stays untouched; only fixture paths move.
_READER_CHILD = r"""
import importlib.util,json,os,pathlib,sys,time,stat
spec=importlib.util.spec_from_file_location('reader_probe',sys.argv[1])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
root=pathlib.Path(sys.argv[2]);mode=sys.argv[3];m._MANIFEST=root/'fresh-manifest.json'
path=m._MANIFEST;calls=[];opened=[];observed=[];changed=False
real_open=os.open;real_fstat=os.fstat
saved=root/'saved-manifest';saved.write_bytes(path.read_bytes()) if path.is_file() else None

def docker(*a,**kw):calls.append(a);raise AssertionError('Docker must not be called')
def fstat(fd):
    info=real_fstat(fd);observed.append({'fd':fd,'regular':stat.S_ISREG(info.st_mode)})
    return info

def opening(name,flags,*args,**kwargs):
    global changed
    if pathlib.Path(name)==path and mode.startswith('race_') and not changed:
        changed=True;path.unlink()
        if mode=='race_fifo':os.mkfifo(path)
        elif mode=='race_symlink':path.symlink_to(saved)
        elif mode=='race_directory':path.mkdir()
        elif mode in ('race_mutate','race_replace'):path.write_bytes(b'{}')
        else:raise AssertionError(mode)
    elif pathlib.Path(name)==path and mode=='mutate_in_place' and not changed:
        changed=True;path.write_bytes(b'{}')
    fd=real_open(name,flags,*args,**kwargs)
    opened.append(fd)
    assert flags & os.O_NONBLOCK and flags & os.O_NOFOLLOW
    if hasattr(os,'O_CLOEXEC'):assert flags & os.O_CLOEXEC
    assert not os.get_inheritable(fd)
    return fd
m._docker=docker;os.open=opening;os.fstat=fstat
before=len(os.listdir('/proc/self/fd'));started=time.monotonic()
try:
    m.run(['tests/native/test_recovered_worker_smoke.py'],use_recovered_image=True,timeout=.05)
except m.CompatibilityError:pass
else:raise AssertionError('invalid evidence accepted')
elapsed=time.monotonic()-started
os.open=real_open;os.fstat=real_fstat
assert elapsed<1. and not calls
assert len(os.listdir('/proc/self/fd'))==before
for fd in opened:
    try:real_fstat(fd)
    except OSError:pass
    else:raise AssertionError('descriptor leaked')
if mode in ('race_fifo','race_directory'):
    assert changed and any(not item['regular'] for item in observed)
if mode=='race_symlink':assert changed and not opened
print(json.dumps({'mode':mode,'rejected':True,'seconds':elapsed,'docker_calls':len(calls),
 'opened_descriptors_closed':len(opened),'fstat_observations':observed,'native_authority_created':False}))
"""


class ReaderRegressions(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='p0-reader-')
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.manifest=self.root/'fresh-manifest.json'
        for name in ('fresh-manifest.json',*launcher._PROOFS):
            shutil.copyfile(launcher._MANIFEST.parent/name,self.root/name)

    def bounded_reject(self,mode):
        # subprocess.run kills/reaps a timed-out reader. No-writer tests spawn
        # no children of their own; the waiting writer is owned by the test parent.
        result=subprocess.run([sys.executable,'-c',_READER_CHILD,str(HERE/'run_recovered_image.py'),str(self.root),mode],
                              capture_output=True,text=True,timeout=3,
                              env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        proof=json.loads(result.stdout);self.assertTrue(proof['rejected'])
        self.assertEqual(proof['docker_calls'],0);self.assertLess(proof['seconds'],1.)
        return proof

    def rejected_without_docker(self):
        before=len(os.listdir('/proc/self/fd'))
        with patch.object(launcher,'_MANIFEST',self.manifest),patch.object(launcher,'_docker') as docker:
            with self.assertRaises(launcher.CompatibilityError):
                launcher.run(['tests/native/test_recovered_worker_smoke.py'],use_recovered_image=True)
            docker.assert_not_called()
        self.assertEqual(len(os.listdir('/proc/self/fd')),before)

    def test_manifest_fifo_without_writer(self):
        self.manifest.unlink();os.mkfifo(self.manifest)
        self.bounded_reject('manifest_fifo')

    def test_proof_fifo_without_writer(self):
        proof=self.root/'base-provenance.json';proof.unlink();os.mkfifo(proof)
        self.bounded_reject('proof_fifo')

    def test_fifo_with_waiting_writer(self):
        self.manifest.unlink();os.mkfifo(self.manifest)
        writer=subprocess.Popen([sys.executable,'-c',
            "import os,sys;f=os.open(sys.argv[1],os.O_WRONLY);os.close(f);print('writer released',flush=True)",str(self.manifest)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            end=time.monotonic()+1.
            waiting=False
            while time.monotonic()<end:
                state=Path('/proc')/str(writer.pid)/'wchan'
                if state.exists() and 'wait_for_partner' in state.read_text():waiting=True;break
                if writer.poll() is not None:break
                time.sleep(.01)
            self.assertTrue(waiting,'writer never reached FIFO wait')
            self.bounded_reject('waiting_writer')
            stdout,stderr=writer.communicate(timeout=1)
            self.assertEqual(writer.returncode,0,stderr);self.assertIn('writer released',stdout)
        finally:
            if writer.poll() is None:writer.kill()
            writer.communicate(timeout=1)

    def test_opened_descriptor_flags_and_valid_content(self):
        fd_list=[];real=os.open;data=self.manifest.read_bytes()
        def inspect_open(path,flags,*a,**kw):
            fd=real(path,flags,*a,**kw);fd_list.append(fd)
            self.assertTrue(flags & os.O_NONBLOCK);self.assertTrue(flags & os.O_NOFOLLOW)
            if hasattr(os,'O_CLOEXEC'):self.assertTrue(flags & os.O_CLOEXEC)
            self.assertFalse(os.get_inheritable(fd));return fd
        with patch.object(launcher.os,'open',side_effect=inspect_open):
            self.assertEqual(launcher._read(self.manifest,128*1024),data)
        for fd in fd_list:
            with self.assertRaises(OSError):os.fstat(fd)

    def test_fstat_exception_closes_open_descriptor(self):
        fds=[];real=os.open
        def opened(*a,**kw):
            fd=real(*a,**kw);fds.append(fd);return fd
        with patch.object(launcher.os,'open',side_effect=opened),patch.object(launcher.os,'fstat',side_effect=OSError('stat failure')):
            self.rejected_without_docker()
        for fd in fds:
            with self.assertRaises(OSError):os.fstat(fd)

    def test_read_exception_closes_open_descriptor(self):
        fds=[];real=os.open
        def opened(*a,**kw):
            fd=real(*a,**kw);fds.append(fd);return fd
        stream=unittest.mock.MagicMock()
        stream.__enter__.return_value.read.side_effect=OSError('read failure')
        with patch.object(launcher.os,'open',side_effect=opened),patch.object(launcher.os,'fdopen',return_value=stream):
            self.rejected_without_docker()
        for fd in fds:
            with self.assertRaises(OSError):os.fstat(fd)

    def test_missing_required_nonblocking_flag_fails_closed(self):
        flag=launcher.os.O_NONBLOCK
        try:
            delattr(launcher.os,'O_NONBLOCK')
            self.rejected_without_docker()
        finally:launcher.os.O_NONBLOCK=flag

    def test_character_device_rejected(self):
        self.manifest=Path('/dev/null');self.rejected_without_docker()

    def test_supporting_symlink_rejected(self):
        proof=self.root/'base-provenance.json';proof.rename(self.root/'original-proof')
        proof.symlink_to(self.root/'original-proof');self.rejected_without_docker()

    def test_missing_evidence_no_descriptor_leak(self):
        self.manifest.unlink();self.rejected_without_docker()

    def test_malformed_evidence_no_descriptor_leak(self):
        self.manifest.write_bytes(b'{invalid');self.rejected_without_docker()


def nonregular_case(kind,supporting):
    def test(self):
        target=self.root/'base-provenance.json' if supporting else self.manifest
        target.unlink()
        if kind=='directory':target.mkdir();self.rejected_without_docker()
        else:
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as server:
                server.bind(str(target));self.rejected_without_docker()
    return test
for _kind in ('directory','socket'):
    for _supporting in (False,True):
        setattr(ReaderRegressions,'test_'+('proof_' if _supporting else 'manifest_')+_kind,nonregular_case(_kind,_supporting))


def oversized_case(supporting):
    def test(self):
        target=self.root/'base-provenance.json' if supporting else self.manifest
        with target.open('wb') as f:f.truncate((2*1024*1024 if supporting else 128*1024)+1)
        self.rejected_without_docker()
    return test
for _supporting in (False,True):
    setattr(ReaderRegressions,'test_oversized_'+('proof' if _supporting else 'manifest'),oversized_case(_supporting))


def racing_case(mode):
    def test(self):self.bounded_reject(mode)
    return test
for _mode in ('race_fifo','race_symlink','race_directory','mutate_in_place','race_replace'):
    setattr(ReaderRegressions,'test_'+_mode,racing_case(_mode))


if __name__=='__main__':unittest.main()
