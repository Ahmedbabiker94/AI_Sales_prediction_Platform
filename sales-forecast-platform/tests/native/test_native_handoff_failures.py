"""Opt-in genuine worker failure probes; host never invokes native libraries.

Deliberate protected-host test instrumentation only. No production entrypoint or
configurable worker command is added. Each case uses a NEW owned invocation.
"""
import json
from pathlib import Path
import runpy
import sys
import time


def main():
    if sys.argv[1:]!=['--authorized-native-handoff']:
        raise SystemExit('explicit native failure-probe authorization required')
    for fault in ('decline','SIGINT','SIGTERM','cancel','timeout'):
        host=runpy.run_path(str(Path(__file__).with_name('run_native_handoff.py')))
        policy=runpy.run_path(str(Path(__file__).with_name('test_native_handoff_integration.py')))['approved_synthetic_intent']
        namespace=host['run'].__globals__; original=host['OwnedContainer']; holder={}; control={'cancel':False}
        class Tracked(original):
            def __init__(self):super().__init__();holder['resource']=self
        namespace['OwnedContainer']=Tracked
        def approve(records,batch):
            assert policy(records,batch)
            return fault!='decline'
        method=host['_HostAdmission'].reserved
        def reserved(self,body,channel):
            result=method(self,body,channel)
            resource=holder['resource']
            if fault in ('SIGINT','SIGTERM'):
                # Only exact ORIGINAL host-created CID, never name/global cleanup.
                sent=resource.command(['kill','--signal',fault,resource.cid])
                assert sent.returncode==0
            elif fault=='cancel':control['cancel']=True
            elif fault=='timeout':channel.deadline=time.monotonic()+.01;time.sleep(.03)
            return result
        host['_HostAdmission'].reserved=reserved
        try:
            host['run'](allow_native_handoff=True,use_recovered_image=True,approve=approve,
                        cancelled=lambda:control['cancel'])
        except host['HandoffFailure'] as e:
            assert e.state==('REJECTED' if fault=='decline' else 'UNKNOWN')
            resource=holder['resource']
            assert resource.status=='CLEANUP_SUCCESS'
            assert not resource.pending
            check=resource.command(['inspect',resource.cid])
            assert check.returncode and b'no such' in check.stderr.lower()
            print(json.dumps({'probe':fault,'state':e.state,'resource':resource.name,'container':resource.cid,
                'cleanup':resource.status,'events':resource.events,'database_operations':0},sort_keys=True))
        else:raise AssertionError('failure admitted as success')


if __name__=='__main__':main()
