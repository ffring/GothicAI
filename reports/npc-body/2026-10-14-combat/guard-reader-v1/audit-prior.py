from pathlib import Path
import hashlib,json,sys
root=Path('/home/kaifat/gothic-ai/body/data/stage8-guard-reader-v1')
prior=Path('/home/kaifat/gothic-ai/body/data/stage8-final-gate-v11')
manifest=json.loads((root/'manifest.json').read_text())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
for name,expected in manifest['files'].items():
 assert sha(root/name)==expected,'Frozen audit dependency changed: '+name
sys.path.insert(0,str(root/'code/scripts'))
import body_final_evaluation as reader
value=json.loads((prior/'final-evaluation.json').read_text())
binding={'reader_sha256':sha(Path(reader.__file__)),'test_manifest_sha256':sha(root/'manifest.json'),'prior_environment_sha256':sha(prior/'environment.json'),'prior_declaration_sha256':sha(prior/'final-evaluation.json'),'script_sha256':sha(Path(__file__)),'model_forward':False,'engine_started':False}
try:
 candidate=reader.fixed_child(Path(value['candidate']['run']))
 assert candidate==value['candidate'],'Actual original selected child binding changed'
 result=reader.additional_failure_snapshot(prior,candidate)
 output={'schema_version':1,'kind':'body_prior_gate11_saved_audit','passed':True,'quality_credit':False,'binding':binding,'snapshot':result}
except (ValueError,OSError,KeyError,TypeError,IndexError) as error:
 output={'schema_version':1,'kind':'body_prior_gate11_saved_audit','passed':False,'quality_credit':False,'binding':binding,'error':str(error)}
(root/'prior-gate11.json').write_text(json.dumps(output,indent=2,allow_nan=False)+'\n')
print(json.dumps({'passed':output['passed'],'error':output.get('error'),'snapshot_kind':output.get('snapshot',{}).get('kind')}))
raise SystemExit(0 if output['passed'] else 2)
