import copy, json
from pathlib import Path
import pytest
from tools import campaign_planner as p

EXAMPLE = Path(__file__).parents[2] / 'examples' / 'stage_a_campaign.json'
def example(): return json.loads(EXAMPLE.read_text())
def compact():
 c=example(); c['campaign_id']='tiny'; c['view_registry']={'v':c['view_registry']['elevation']}; c['stages']=[{'stage_id':'one','description':'one','jobs':[{'job_key':'align','view_key':'v','probe_id':'stage_a_image_alignment','case':'a','variant':'x','repetition':1,'settings':{}}]},{'stage_id':'two','description':'two','jobs':[{'job_key':'mutate','view_key':'v','probe_id':'stage_a_minimum_id_mutations','case':'m','variant':'x','repetition':1,'settings':{}},{'job_key':'independent','view_key':'v','probe_id':'stage_a_image_alignment','case':'i','variant':'x','repetition':1,'settings':{}}]}]; c['dependencies']=[{'job':'mutate','requires_job':'align','statuses':['PASS']}]; c['execution_defaults']['batch_size']=2; return c
def get(state,key): return next(x for x in state['jobs'].values() if x['job_key']==key)
def manifest(state, job, status='completed', run='r1'):
 bid=job['batch_ids'][-1]; return {'schema_version':'1.0','campaign_id':state['campaign_id'],'batch_id':bid,'run_id':run,'document_identity':{},'environment':{},'batch_source':'batch.json','started_at':'x','completed_at':'y','execution_status':'completed','jobs':[{'job_id':job['job_id'],'configuration_fingerprint':job['execution_fingerprints'][bid],'execution_status':status,'raw_result_envelope':{'artifact_paths':['raw.tif']}}],'jobs_not_attempted':[]}
def analysis(state,job,status='PASS',run='r1'):
 return {'analysis_schema_version':'1.0','campaign_id':state['campaign_id'],'batch_id':job['batch_ids'][-1],'run_id':run,'job_id':job['job_id'],'probe_id':job['probe_id'],'analyzer_version':'x','source_report':{'path':'raw.json','sha256':'a'},'artifact_references':['raw.tif'],'acceptance_status':status,'execution_status':'completed','reason_codes':[]}

def test_schema_validation_ids_fingerprints_and_drafting_rejection():
 c=example(); assert p.validate_campaign(c); assert p.canonical_fingerprint(c)==p.canonical_fingerprint(copy.deepcopy(c)); c2=copy.deepcopy(c); c2['description']='changed'; assert p.canonical_fingerprint(c)!=p.canonical_fingerprint(c2)
 assert p.stable_job_id('c','s','v','p','case','var',1)==p.stable_job_id('c','s','v','p','case','var',1)
 c['view_registry']['elevation']['expected_view_type']='Drafting'; pytest.raises(p.CampaignError,p.validate_campaign,c)
def test_legal_illegal_transitions():
 c=compact(); s=p.initialize_state(c); j=get(s,'align'); assert j['status']=='ELIGIBLE'; p.transition(s,j['job_id'],'BATCHED','TEST'); pytest.raises(p.CampaignError,p.transition,s,j['job_id'],'PASSED','BAD')
def test_end_to_end_partial_missing_analysis_and_idempotence():
 c=compact(); s=p.initialize_state(c); b=p.generate_next_batch(c,s); assert len(b['jobs'])==2
 a=get(s,'align'); m=manifest(s,a); p.ingest_manifests(c,s,[m,m]); assert a['status']=='EXECUTED' and len(s['history'])==len({x['event_id'] for x in s['history']}); assert get(s,'mutate')['status']=='PLANNED'
 rec=analysis(s,a); p.ingest_analysis(c,s,[rec,rec]); assert a['status']=='PASSED' and get(s,'mutate')['status']=='ELIGIBLE'; assert a['artifact_references']==['raw.tif']
 assert len(s['ingested_revit_runs'])==1 and len(s['ingested_analysis_records'])==1
def test_envelope_less_executor_failure_is_final_without_analysis():
 c=compact(); c['stages'][1]['jobs']=c['stages'][1]['jobs'][:1]; s=p.initialize_state(c); p.generate_next_batch(c,s); a=get(s,'align'); failed=manifest(s,a,status='failed'); failed['jobs'][0]['raw_result_envelope']=None; failed['jobs'][0]['errors']=[{'type':'RuntimeError','message':'probe raised'}]; p.ingest_manifests(c,s,[failed]); assert a['status']=='FAILED'; assert a['status_reason']['code']=='REVIT_EXECUTION_FAILED'; assert get(s,'mutate')['status']=='BLOCKED'; p.generate_next_batch(c,s); assert s['next_recommendation']['code']=='BLOCKED_BY_FAILURE'
def test_failed_execution_with_envelope_still_awaits_analysis():
 c=compact(); s=p.initialize_state(c); p.generate_next_batch(c,s); a=get(s,'align'); p.ingest_manifests(c,s,[manifest(s,a,status='failed')]); assert a['status']=='EXECUTED'
def test_fail_inconclusive_gating_and_independent_progression():
 for outcome in ('FAIL','INCONCLUSIVE'):
  c=compact(); s=p.initialize_state(c); p.generate_next_batch(c,s); a=get(s,'align'); p.ingest_manifests(c,s,[manifest(s,a)]); p.ingest_analysis(c,s,[analysis(s,a,outcome)]); assert get(s,'mutate')['status']=='BLOCKED'; assert get(s,'independent')['status']=='BATCHED'
def test_conflicting_duplicate_analysis_and_run():
 c=compact(); s=p.initialize_state(c); p.generate_next_batch(c,s); a=get(s,'align'); m=manifest(s,a); p.ingest_manifests(c,s,[m]); changed=copy.deepcopy(m); changed['environment']={'different':1}; p.ingest_manifests(c,s,[changed]); assert s['conflicts'][0]['code']=='RUN_CONFLICT'
 rec=analysis(s,a); p.ingest_analysis(c,s,[rec]); changed=copy.deepcopy(rec); changed['warnings']=['different']; p.ingest_analysis(c,s,[changed]); assert any(x['code']=='ANALYSIS_CONFLICT' for x in s['conflicts'])
def test_batch_limit_resume_and_drift():
 c=compact(); c['execution_defaults']['batch_size']=1; s=p.initialize_state(c); assert len(p.generate_next_batch(c,s)['jobs'])==1; assert p.generate_next_batch(c,s)['jobs'][0]['job_id']==get(s,'independent')['job_id']; c['description']='drift'; pytest.raises(p.CampaignError,p.generate_next_batch,c,s); assert all(j['status']=='SUPERSEDED' for j in s['jobs'].values())
def test_diagnostic_expansion_and_manual_completion_state():
 c=compact(); c['stages'][0]['jobs'][0]['manual_review_required']=True; s=p.initialize_state(c); p.generate_next_batch(c,s); a=get(s,'align'); p.ingest_manifests(c,s,[manifest(s,a)]); p.ingest_analysis(c,s,[analysis(s,a,'FAIL')]); p.diagnostic_action(c,s,a['job_id'],'request','targeted_asf'); diagnostics=[j for j in s['jobs'].values() if j['job_key'].startswith('diagnostic.')]; assert len(diagnostics)==3; assert a['status']=='NEEDS_DIAGNOSTIC'; assert all(j['status']=='ELIGIBLE' for j in diagnostics); assert a['job_id'] not in [j['job_id'] for j in diagnostics]
 # A separate all-terminal state remains awaiting review until explicitly recorded.
 c2=compact(); c2['stages']=c2['stages'][:1]; c2['dependencies']=[]; c2['stages'][0]['jobs'][0]['manual_review_required']=True; s2=p.initialize_state(c2); p.generate_next_batch(c2,s2); a2=get(s2,'align'); p.ingest_manifests(c2,s2,[manifest(s2,a2)]); p.ingest_analysis(c2,s2,[analysis(s2,a2)]); assert p.generate_next_batch(c2,s2) is None and s2['next_recommendation']['code']=='AWAITING_MANUAL_REVIEW'; p.record_manual_review(c2,s2,a2['job_id'],'ACCEPTED','operator'); p.generate_next_batch(c2,s2); assert s2['next_recommendation']['code']=='CAMPAIGN_COMPLETE'
def test_rejected_manual_review_blocks_completion():
 c=compact(); c['stages']=c['stages'][:1]; c['dependencies']=[]; c['stages'][0]['jobs'][0]['manual_review_required']=True; s=p.initialize_state(c); p.generate_next_batch(c,s); a=get(s,'align'); p.ingest_manifests(c,s,[manifest(s,a)]); p.ingest_analysis(c,s,[analysis(s,a)]); p.record_manual_review(c,s,a['job_id'],'REJECTED','operator'); assert p.generate_next_batch(c,s) is None; assert s['next_recommendation']=={'code':'BLOCKED_BY_FAILURE','reason':'MANUAL_REVIEW_REJECTED','job_ids':[a['job_id']]}
def test_diagnostic_ids_are_scoped_to_source_job():
 c=compact(); second=copy.deepcopy(c['stages'][0]['jobs'][0]); second.update(job_key='align_repeat',repetition=2); c['stages'][0]['jobs'].append(second); s=p.initialize_state(c); p.generate_next_batch(c,s)
 sources=[get(s,'align'),get(s,'align_repeat')]
 for index,source in enumerate(sources,1): p.ingest_manifests(c,s,[manifest(s,source,run=f'r{index}')]); p.ingest_analysis(c,s,[analysis(s,source,'FAIL',run=f'r{index}')]); p.diagnostic_action(c,s,source['job_id'],'request','targeted_asf')
 diagnostics=[j for j in s['jobs'].values() if j['job_key'].startswith('diagnostic.')]; assert len(diagnostics)==6; assert len({j['job_id'] for j in diagnostics})==6; assert all(j['status']=='ELIGIBLE' for j in diagnostics); assert all(j['status']=='NEEDS_DIAGNOSTIC' for j in sources)
def test_diagnostic_rule_without_variants_is_rejected():
 c=compact(); s=p.initialize_state(c); p.generate_next_batch(c,s); a=get(s,'align'); p.ingest_manifests(c,s,[manifest(s,a)]); p.ingest_analysis(c,s,[analysis(s,a,'FAIL')]); pytest.raises(p.CampaignError,p.diagnostic_action,c,s,a['job_id'],'request','fixed_1600'); assert a['status']=='FAILED'; assert a['job_id'] not in s['diagnostic_requests']
def test_status_recomputes_stale_recommendation_without_scheduling():
 c=compact(); s=p.initialize_state(c); p.generate_next_batch(c,s); a=get(s,'align'); p.ingest_manifests(c,s,[manifest(s,a)]); p.ingest_analysis(c,s,[analysis(s,a)]); assert s['next_recommendation']['code']=='RUN_BATCH_IN_REVIT'; before=copy.deepcopy(s['jobs']); summary=p.status_summary(c,s); assert summary['next_recommendation']['code']=='GENERATE_NEXT_BATCH'; assert summary['next_recommendation']['eligible_job_count']==1; assert s['jobs']==before
def test_no_revit_import_or_probe_execution():
 source=Path(p.__file__).read_text(); assert 'Autodesk.Revit' not in source and 'revit_probe_registry' not in source and 'run_probe(' not in source
