import copy, json
from pathlib import Path
import pytest
from tools import campaign_planner as p

EXAMPLE = Path(__file__).parents[2] / 'examples' / 'stage_a_campaign.json'
def example(): return json.loads(EXAMPLE.read_text())
def compact():
 c=example(); c['campaign_id']='tiny'; c['view_registry']={'v':c['view_registry']['elevation']}; c['stages']=[{'stage_id':'one','description':'one','jobs':[{'job_key':'align','view_key':'v','probe_id':'stage_a_image_alignment','case':'a','variant':'x','repetition':1,'settings':{}}]},{'stage_id':'two','description':'two','jobs':[{'job_key':'mutate','view_key':'v','probe_id':'stage_a_minimum_id_mutations','case':'m','variant':'x','repetition':1,'settings':{}},{'job_key':'independent','view_key':'v','probe_id':'stage_a_image_alignment','case':'i','variant':'x','repetition':1,'settings':{}}]}]; c['dependencies']=[{'job':'mutate','requires_job':'align','statuses':['PASS']}]; c['conditional_fallbacks']=[]; c['host_color_id_feasibility']={}; c['execution_defaults']['batch_size']=2; return c
def get(state,key): return next(x for x in state['jobs'].values() if x['job_key']==key)
def manifest(state, job, status='completed', run='r1', artifact_paths=('raw.tif',)):
 bid=job['batch_ids'][-1]; return {'schema_version':'1.0','campaign_id':state['campaign_id'],'batch_id':bid,'run_id':run,'document_identity':{},'environment':{},'batch_source':'batch.json','started_at':'x','completed_at':'y','execution_status':'completed','jobs':[{'job_id':job['job_id'],'configuration_fingerprint':job['execution_fingerprints'][bid],'execution_status':status,'raw_result_envelope':{'artifact_paths':list(artifact_paths)}}],'jobs_not_attempted':[]}
def analysis(state,job,status='PASS',run='r1',reason_codes=None):
 return {'analysis_schema_version':'1.0','campaign_id':state['campaign_id'],'batch_id':job['batch_ids'][-1],'run_id':run,'job_id':job['job_id'],'probe_id':job['probe_id'],'analyzer_version':'x','source_report':{'path':'raw.json','sha256':'a'},'artifact_references':['raw.tif'],'acceptance_status':status,'execution_status':'completed','reason_codes':reason_codes or []}
def run_batch(c,s,job_key,status='PASS',reason_codes=None,run=None,artifact_paths=('raw.tif',)):
 """Generate the next batch, execute+analyze exactly the named job, return it."""
 p.generate_next_batch(c,s); j=get(s,job_key); run=run or j['job_id']
 p.ingest_manifests(c,s,[manifest(s,j,run=run,artifact_paths=artifact_paths)]); p.ingest_analysis(c,s,[analysis(s,j,status,run=run,reason_codes=reason_codes)]); return j

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

# --- Stage 1 mutation-closure conditional fallback, and real example-campaign shape ---

def test_initial_campaign_produces_only_stage1_batch():
 c=example(); s=p.initialize_state(c)
 eligible=[j['job_key'] for j in s['jobs'].values() if j['status']=='ELIGIBLE']
 assert eligible==['s1.attached_as']
 b=p.generate_next_batch(c,s); assert len(b['jobs'])==1 and b['jobs'][0]['job_id']==get(s,'s1.attached_as')['job_id']

def test_batch_settings_are_separate_from_planner_fingerprints():
 c=example(); s=p.initialize_state(c); b=p.generate_next_batch(c,s); job=b['jobs'][0]
 assert 'campaign_configuration_fingerprint' not in job['settings'] and 'job_configuration_fingerprint' not in job['settings']
 assert job['job_configuration_fingerprint']==get(s,'s1.attached_as')['configuration_fingerprint']
 assert job['variant']=='attached_AS'
 assert set(job['settings'])=={'dpi','comparison_reference'}

def test_execution_fingerprint_includes_dispatched_variant():
 # variant is dispatch identity (the adapter maps it to run_probe(selection=...)),
 # so two jobs identical in every other batch field but a different variant
 # must never collide on the same execution fingerprint - a tampered/corrupted
 # next_batch.json that only changed `variant` must be caught as drift.
 import tests.dynamo.revit_batch_contract as contract
 c=example(); s=p.initialize_state(c); b=p.generate_next_batch(c,s)
 job=get(s,'s1.attached_as')
 planner_fp=job['execution_fingerprints'][b['batch_id']]
 executor_fp=contract.job_fingerprint(b['jobs'][0])
 assert planner_fp==executor_fp
 tampered=copy.deepcopy(b['jobs'][0]); tampered['variant']='detached_AS'
 assert contract.job_fingerprint(tampered)!=planner_fp

def test_gate_scoped_review_is_not_seeded_for_a_mixed_inconclusive_reason():
 c=compact(); s=p.initialize_state(c)
 a=run_batch(c,s,'align','INCONCLUSIVE',reason_codes=['MANUAL_SEMANTIC_REVIEW_REQUIRED','NO_CASES_ANALYZED'])
 assert a['status']=='INCONCLUSIVE'
 assert a['job_id'] not in s['manual_review_requirements']

def test_attached_as_fully_attested_and_accepted_unlocks_stage2():
 c=example(); s=p.initialize_state(c); run_batch(c,s,'s1.attached_as','PASS')
 closure=s['closures']['elevation_mutation_closure']
 assert closure['status']=='RESOLVED_PRIMARY' and closure['candidate_job_id']==get(s,'s1.attached_as')['job_id']
 assert all(get(s,k)['status']=='ELIGIBLE' for k in ('s2.align.r1','s2.align.r2'))
 assert not any(j['job_key']=='s1.detached_as' for j in s['jobs'].values())

def test_attached_as_blocked_by_template_schedules_detached_as_fallback():
 c=example(); s=p.initialize_state(c)
 run_batch(c,s,'s1.attached_as','FAIL',reason_codes=['MUTATION_ATTESTATION_FAILED','MUTATION_BLOCKED_BY_TEMPLATE_ONLY'])
 assert get(s,'s1.attached_as')['status']=='FAILED'
 closure=s['closures']['elevation_mutation_closure']
 assert closure['status']=='AWAITING_FALLBACK_EXECUTION'
 fb=next(j for j in s['jobs'].values() if j['job_key']=='s1.detached_as')
 assert fb['status']=='ELIGIBLE' and fb['variant']=='detached_AS'
 assert fb['provenance']=={'closure_id':'elevation_mutation_closure','fallback_of_job_id':get(s,'s1.attached_as')['job_id'],'trigger_reason_codes_matched':['MUTATION_BLOCKED_BY_TEMPLATE_ONLY']}
 assert all(get(s,k)['status']=='PLANNED' for k in ('s2.align.r1','s2.align.r2'))
 assert s['stage_status']['01_mutation_recipe_confirmation']=='ACTIVE'

def test_unrelated_attached_as_failure_does_not_schedule_fallback():
 c=example(); s=p.initialize_state(c)
 run_batch(c,s,'s1.attached_as','FAIL',reason_codes=['MUTATION_ATTESTATION_FAILED'])
 assert get(s,'s1.attached_as')['status']=='FAILED'
 closure=s['closures']['elevation_mutation_closure']; assert closure['status']=='RESOLVED_FAILED'
 assert not any(j['job_key']=='s1.detached_as' for j in s['jobs'].values())
 assert all(get(s,k)['status']=='BLOCKED' for k in ('s2.align.r1','s2.align.r2'))

def test_detached_as_execution_and_analysis_resolve_closure_deterministically():
 for outcome,expected in (('PASS','RESOLVED_FALLBACK_PASS'),('FAIL','RESOLVED_FALLBACK_FAILED')):
  c=example(); s=p.initialize_state(c)
  run_batch(c,s,'s1.attached_as','FAIL',reason_codes=['MUTATION_ATTESTATION_FAILED','MUTATION_BLOCKED_BY_TEMPLATE_ONLY'])
  fb=next(j for j in s['jobs'].values() if j['job_key']=='s1.detached_as')
  run_batch(c,s,'s1.detached_as',outcome)
  closure=s['closures']['elevation_mutation_closure']; assert closure['status']==expected and closure['candidate_job_id']==fb['job_id']
  wants_open=(outcome=='FAIL')
  assert all((get(s,k)['status']=='BLOCKED')==wants_open for k in ('s2.align.r1','s2.align.r2'))
  assert all((get(s,k)['status']=='ELIGIBLE')==(not wants_open) for k in ('s2.align.r1','s2.align.r2'))

def test_stage2_cannot_run_before_closure_resolved():
 c=example(); s=p.initialize_state(c)
 run_batch(c,s,'s1.attached_as','FAIL',reason_codes=['MUTATION_ATTESTATION_FAILED','MUTATION_BLOCKED_BY_TEMPLATE_ONLY'])
 b=p.generate_next_batch(c,s)
 assert all(j['job_id']!=get(s,'s2.align.r1')['job_id'] for j in b['jobs'])

def test_stage_status_never_reads_complete_when_a_job_failed():
 c=example(); s=p.initialize_state(c)
 run_batch(c,s,'s1.attached_as','FAIL',reason_codes=['MUTATION_ATTESTATION_FAILED'])
 assert s['stage_status']['01_mutation_recipe_confirmation']=='COMPLETE_WITH_FAILURES'

def test_existing_failed_stage1_state_migrates_and_resupersedes_on_campaign_change():
 # Simulates the supplied evidence: an old state file (pre-conditional_fallbacks)
 # with a FAILED attached_AS job, migrated forward, then re-bound to the
 # corrected campaign. Evidence (run_ids/analysis_record_ids/history) survives
 # on the superseded job; nothing is silently discarded.
 old_campaign=example(); old_campaign['conditional_fallbacks']=[]; old_campaign['host_color_id_feasibility']={}
 for dep in old_campaign['dependencies']:
  if dep.get('requires_closure')=='elevation_mutation_closure': dep.pop('requires_closure',None); dep['requires_job']='s1.attached_as'
 s=p.initialize_state(old_campaign)
 attached=run_batch(old_campaign,s,'s1.attached_as','FAIL',reason_codes=['MUTATION_ATTESTATION_FAILED','MUTATION_BLOCKED_BY_TEMPLATE_ONLY'])
 assert attached['status']=='FAILED' and attached['run_ids'] and attached['analysis_record_ids']
 migrated=p.migrate_state(json.loads(json.dumps(s))); assert migrated['closures']=={}
 new_campaign=example()
 pytest.raises(p.CampaignError,p.generate_next_batch,new_campaign,migrated)
 assert migrated['jobs'][attached['job_id']]['status']=='SUPERSEDED'
 assert migrated['jobs'][attached['job_id']]['run_ids']==attached['run_ids']
 assert migrated['jobs'][attached['job_id']]['analysis_record_ids']==attached['analysis_record_ids']
 assert any(e['kind']=='CONFIGURATION_DRIFT' or e.get('reason_code')=='CONFIGURATION_DRIFT' for e in migrated['history'])

def test_authorized_manual_review_resolves_only_its_configured_gate():
 c=compact(); s=p.initialize_state(c); a=run_batch(c,s,'align','INCONCLUSIVE',reason_codes=['MANUAL_SEMANTIC_REVIEW_REQUIRED'])
 assert a['status']=='INCONCLUSIVE'
 requirement=s['manual_review_requirements'][a['job_id']]; assert requirement['gate']=='rendered_semantic_preservation'
 p.record_manual_review(c,s,a['job_id'],'ACCEPTED','operator')
 assert get(s,'align')['status']=='PASSED'
 assert get(s,'mutate')['status']=='ELIGIBLE'

def test_gate_metadata_merges_into_a_preexisting_campaign_authored_review():
 # A job can be BOTH campaign-authored manual_review_required (seeded at
 # init, no "gate" key) AND later found INCONCLUSIVE solely for
 # MANUAL_SEMANTIC_REVIEW_REQUIRED. The gate marker must merge into the
 # existing requirement rather than being skipped because one already
 # exists - otherwise ACCEPTED could never transition the job out of
 # INCONCLUSIVE.
 c=compact(); c['stages'][0]['jobs'][0]['manual_review_required']=True; c['stages'][0]['jobs'][0]['manual_review_reason']='CONFIGURED_INSPECTION'
 s=p.initialize_state(c)
 requirement_before=s['manual_review_requirements'][get(s,'align')['job_id']]
 assert requirement_before['reason']=='CONFIGURED_INSPECTION' and 'gate' not in requirement_before
 a=run_batch(c,s,'align','INCONCLUSIVE',reason_codes=['MANUAL_SEMANTIC_REVIEW_REQUIRED'])
 assert a['status']=='INCONCLUSIVE'
 requirement=s['manual_review_requirements'][a['job_id']]
 assert requirement['gate']=='rendered_semantic_preservation'
 assert requirement['reason']=='CONFIGURED_INSPECTION'  # campaign-authored reason preserved, not clobbered
 p.record_manual_review(c,s,a['job_id'],'ACCEPTED','operator')
 assert get(s,'align')['status']=='PASSED'
 assert get(s,'mutate')['status']=='ELIGIBLE'

def test_manual_review_rejection_only_fails_its_gated_job():
 c=compact(); s=p.initialize_state(c); a=run_batch(c,s,'align','INCONCLUSIVE',reason_codes=['MANUAL_SEMANTIC_REVIEW_REQUIRED'])
 p.record_manual_review(c,s,a['job_id'],'REJECTED','operator')
 assert get(s,'align')['status']=='FAILED' and get(s,'mutate')['status']=='BLOCKED'

def test_manual_review_never_overrides_an_unrelated_failure_reason():
 c=compact(); s=p.initialize_state(c); a=run_batch(c,s,'align','FAIL',reason_codes=['MANUAL_SEMANTIC_REVIEW_REQUIRED','MUTATION_ATTESTATION_FAILED'])
 assert a['status']=='FAILED'  # a real automated failure code alongside the manual-review code, so it never became gate-scoped-only INCONCLUSIVE
 assert a['job_id'] not in s['manual_review_requirements']

FOLLOWUP_EXAMPLE = Path(__file__).parents[2] / 'examples' / 'stage_a_followup_campaign.json'
def followup_example(): return json.loads(FOLLOWUP_EXAMPLE.read_text())

def test_core_campaign_contains_no_external_source_or_linework_jobs():
 # External sources and linework diagnostics were moved out of the core host
 # color-ID feasibility campaign entirely (a separate follow-up campaign
 # document, not merely a non-blocking dependency within this one) - the core
 # campaign must complete on its own, whatever state that follow-up work is in.
 c=example()
 probe_ids={job['probe_id'] for stage in c['stages'] for job in stage['jobs']}
 assert probe_ids=={'stage_a_image_alignment','stage_a_minimum_id_mutations'}
 assert 'rvt_link' not in c['view_registry'] and 'dwg' not in c['view_registry']

def test_unrelated_stage1_failure_cascades_only_within_the_core_campaign():
 # A Stage 1 failure for an unrelated reason cascades BLOCKED through Stage 2
 # and Stage 3 - there is no Stage 7/external-source work in this campaign
 # left to spuriously unlock, and no eligible work remains at all.
 c=example(); s=p.initialize_state(c)
 run_batch(c,s,'s1.attached_as','FAIL',reason_codes=['MUTATION_ATTESTATION_FAILED'])
 for stage_id in ('02_alignment_resolution_confirmation','03_cross_view_host_raster_matrix'):
  assert s['stage_status'][stage_id]=='BLOCKED', stage_id
 assert not any(j['status']=='ELIGIBLE' for j in s['jobs'].values())
 feasibility=p.host_color_id_feasibility(c,s)
 assert feasibility['conclusion']=='HOST_COLOR_ID_FEASIBILITY_INCONCLUSIVE'
 assert feasibility['views']['elevation']['result']=='FAIL'
 assert all(feasibility['views'][v]['result']=='NOT_TESTED' for v in ('floor_active','floor_inactive','rcp','section','callout'))

def test_followup_campaign_validates_and_is_independent_of_the_core_campaign():
 fc=followup_example(); assert p.validate_campaign(fc)
 assert fc['campaign_id']!=example()['campaign_id']
 probe_ids={job['probe_id'] for stage in fc['stages'] for job in stage['jobs']}
 assert probe_ids=={'stage_a_image_alignment','stage_a_minimum_id_mutations','stage_a_model_linework','stage_a_external_sources'}
 fs=p.initialize_state(fc)
 eligible=sorted(j['job_key'] for j in fs['jobs'].values() if j['status']=='ELIGIBLE')
 # Every stage's jobs are independently runnable from the start - none of
 # them is gated on the (separate) core campaign's own state.
 assert {'s7.rvt_link.fixed','s7.dwg.fixed','s3.line.75','s3.line.150','s3.line.300'} <= set(eligible)
 assert any(k.startswith('s5.') for k in eligible)  # alternate mutation experiments
 assert any(k.startswith('s4.') for k in eligible)  # resolution sensitivity

def test_cli_persists_superseded_state_to_disk_on_configuration_drift(tmp_path):
 c=compact(); s=p.initialize_state(c)
 campaign_path=tmp_path/'campaign.json'; state_path=tmp_path/'state.json'
 campaign_path.write_text(json.dumps(c)); state_path.write_text(json.dumps(s))
 drifted=copy.deepcopy(c); drifted['description']='drift'
 campaign_path.write_text(json.dumps(drifted))
 batch_path=tmp_path/'batch.json'
 pytest.raises(p.CampaignError,p.main,['next-batch',str(campaign_path),str(state_path),str(batch_path)])
 assert not batch_path.exists()
 on_disk=json.loads(state_path.read_text())
 assert all(j['status']=='SUPERSEDED' for j in on_disk['jobs'].values())
 assert on_disk['next_recommendation']['code']=='INVALID_CAMPAIGN_STATE'

def test_cli_invalid_manifest_does_not_partially_persist_earlier_ingestion(tmp_path):
 # A multi-manifest ingest where the first is valid (and would fully
 # mutate state) and the second is malformed must not persist the first's
 # ingestion either - only ConfigurationDriftError is ever persisted after
 # an error; every other CampaignError must leave the on-disk file exactly
 # as it was before the command ran, even though the valid manifest was
 # genuinely applied to the in-memory object before the raise.
 c=compact(); s=p.initialize_state(c)
 campaign_path=tmp_path/'campaign.json'; state_path=tmp_path/'state.json'
 campaign_path.write_text(json.dumps(c))
 p.generate_next_batch(c,s)
 state_path.write_text(json.dumps(s))
 a=get(s,'align')
 valid_manifest=manifest(s,a)
 bad_manifest=copy.deepcopy(valid_manifest); bad_manifest['run_id']=None
 m1=tmp_path/'m1.json'; m1.write_text(json.dumps(valid_manifest))
 m2=tmp_path/'m2.json'; m2.write_text(json.dumps(bad_manifest))
 pytest.raises(p.CampaignError,p.main,['ingest-runs',str(campaign_path),str(state_path),str(m1),str(m2)])
 on_disk=json.loads(state_path.read_text())
 assert get(on_disk,'align')['status']=='BATCHED'
 assert on_disk['ingested_revit_runs']=={}

def test_cli_malformed_analysis_does_not_partially_ingest_records(tmp_path):
 c=compact(); s=p.initialize_state(c)
 campaign_path=tmp_path/'campaign.json'; state_path=tmp_path/'state.json'
 campaign_path.write_text(json.dumps(c))
 p.generate_next_batch(c,s)
 a=get(s,'align'); i=get(s,'independent')
 p.ingest_manifests(c,s,[manifest(s,a,run='r1'),manifest(s,i,run='r2')])
 assert a['status']=='EXECUTED' and i['status']=='EXECUTED'
 state_path.write_text(json.dumps(s))
 rec_a=analysis(s,a,'PASS',run='r1')
 rec_i=analysis(s,i,'PASS',run='r2'); rec_i['acceptance_status']='BOGUS'
 r1=tmp_path/'r1.json'; r1.write_text(json.dumps(rec_a))
 r2=tmp_path/'r2.json'; r2.write_text(json.dumps(rec_i))
 pytest.raises(p.CampaignError,p.main,['ingest-analysis',str(campaign_path),str(state_path),str(r1),str(r2)])
 on_disk=json.loads(state_path.read_text())
 assert get(on_disk,'align')['status']=='EXECUTED'
 assert get(on_disk,'independent')['status']=='EXECUTED'
 assert on_disk['ingested_analysis_records']=={}

def test_cli_unexpected_campaign_error_leaves_state_byte_for_byte_unchanged(tmp_path):
 c=compact(); s=p.initialize_state(c)
 campaign_path=tmp_path/'campaign.json'; state_path=tmp_path/'state.json'
 campaign_path.write_text(json.dumps(c))
 before=json.dumps(s, sort_keys=True); state_path.write_text(before)
 a=get(s,'align')
 pytest.raises(p.CampaignError,p.main,['diagnostic',str(campaign_path),str(state_path),'request',a['job_id'],'--rule','not_a_real_rule'])
 assert state_path.read_text()==before

def test_end_to_end_simulated_progression_through_fallback_and_elevation_alignment():
 c=example(); s=p.initialize_state(c)
 run_batch(c,s,'s1.attached_as','FAIL',reason_codes=['MUTATION_ATTESTATION_FAILED','MUTATION_BLOCKED_BY_TEMPLATE_ONLY'])
 assert any(j['job_key']=='s1.detached_as' for j in s['jobs'].values())
 run_batch(c,s,'s1.detached_as','PASS')
 assert s['closures']['elevation_mutation_closure']['status']=='RESOLVED_FALLBACK_PASS'
 b=p.generate_next_batch(c,s)
 opened=('s2.align.r1','s2.align.r2','s2.floor_active.confirm','s2.floor_inactive.confirm','s2.rcp.confirm','s2.section.confirm','s2.callout.confirm')
 assert {j['job_id'] for j in b['jobs']}=={get(s,k)['job_id'] for k in opened}

# --------------------------------------------------------------------------
# Manual-review artifact-integrity: a review may only be accepted/rejected
# when a concrete reviewable raster artifact backs it.
# --------------------------------------------------------------------------

def test_linework_visual_inspection_with_raster_artifact_accepts_and_rejects_normally():
 c=compact(); c['stages']=c['stages'][:1]; c['dependencies']=[]
 c['stages'][0]['jobs'][0]['manual_review_required']=True
 c['stages'][0]['jobs'][0]['manual_review_reason']='LINEWORK_VISUAL_INSPECTION'
 s=p.initialize_state(c); p.generate_next_batch(c,s); a=get(s,'align')
 p.ingest_manifests(c,s,[manifest(s,a)]); p.ingest_analysis(c,s,[analysis(s,a,'PASS')])
 requirement=s['manual_review_requirements'][a['job_id']]
 assert requirement['status']=='PENDING' and requirement['artifact_references']==['raw.tif']
 p.record_manual_review(c,s,a['job_id'],'ACCEPTED','operator')
 assert s['manual_review_requirements'][a['job_id']]['status']=='ACCEPTED'
 assert p.generate_next_batch(c,s) is None and s['next_recommendation']['code']=='CAMPAIGN_COMPLETE'
 # A separate rejecting run of the same shape still works normally too.
 c2=copy.deepcopy(c); s2=p.initialize_state(c2); p.generate_next_batch(c2,s2); a2=get(s2,'align')
 p.ingest_manifests(c2,s2,[manifest(s2,a2)]); p.ingest_analysis(c2,s2,[analysis(s2,a2,'PASS')])
 p.record_manual_review(c2,s2,a2['job_id'],'REJECTED','operator')
 assert s2['manual_review_requirements'][a2['job_id']]['status']=='REJECTED'
 assert p.generate_next_batch(c2,s2) is None
 assert s2['next_recommendation']=={'code':'BLOCKED_BY_FAILURE','reason':'MANUAL_REVIEW_REJECTED','job_ids':[a2['job_id']]}

def test_manual_review_cannot_be_accepted_or_rejected_with_empty_artifact_references():
 c=compact(); c['stages']=c['stages'][:1]; c['dependencies']=[]
 c['stages'][0]['jobs'][0]['manual_review_required']=True
 c['stages'][0]['jobs'][0]['manual_review_reason']='LINEWORK_VISUAL_INSPECTION'
 s=p.initialize_state(c); p.generate_next_batch(c,s); a=get(s,'align')
 p.ingest_manifests(c,s,[manifest(s,a,artifact_paths=())]); p.ingest_analysis(c,s,[analysis(s,a,'PASS')])
 requirement=s['manual_review_requirements'][a['job_id']]
 assert requirement['status']=='EVIDENCE_MISSING'
 assert requirement['artifact_references']==[]
 assert 'diagnostic' in requirement
 with pytest.raises(p.CampaignError):
  p.record_manual_review(c,s,a['job_id'],'ACCEPTED','operator')
 with pytest.raises(p.CampaignError):
  p.record_manual_review(c,s,a['job_id'],'REJECTED','operator')
 # The requirement is unchanged by the refused attempts.
 assert s['manual_review_requirements'][a['job_id']]['status']=='EVIDENCE_MISSING'

def test_missing_review_artifact_blocks_campaign_completion_as_evidence_missing():
 c=compact(); c['stages']=c['stages'][:1]; c['dependencies']=[]
 c['stages'][0]['jobs'][0]['manual_review_required']=True
 s=p.initialize_state(c); p.generate_next_batch(c,s); a=get(s,'align')
 p.ingest_manifests(c,s,[manifest(s,a,artifact_paths=())]); p.ingest_analysis(c,s,[analysis(s,a,'PASS')])
 assert p.generate_next_batch(c,s) is None
 assert s['next_recommendation']=={'code':'BLOCKED_BY_FAILURE','reason':'MANUAL_REVIEW_EVIDENCE_MISSING','job_ids':[a['job_id']]}

def test_gate_scoped_semantic_review_without_artifact_is_not_an_actionable_review():
 # A bare "not automatically verified" analyzer outcome must never become an
 # actionable operator review task unless a reviewable artifact exists.
 c=compact(); s=p.initialize_state(c); p.generate_next_batch(c,s); a=get(s,'align')
 p.ingest_manifests(c,s,[manifest(s,a,artifact_paths=())])
 p.ingest_analysis(c,s,[analysis(s,a,'INCONCLUSIVE',reason_codes=['MANUAL_SEMANTIC_REVIEW_REQUIRED'])])
 assert a['status']=='INCONCLUSIVE'
 requirement=s['manual_review_requirements'][a['job_id']]
 assert requirement['status']=='EVIDENCE_MISSING'
 assert requirement['reason']=='SEMANTIC_PRESERVATION_NOT_AUTOMATICALLY_VERIFIED'
 assert requirement['gate']=='rendered_semantic_preservation'
 with pytest.raises(p.CampaignError):
  p.record_manual_review(c,s,a['job_id'],'ACCEPTED','operator')

def test_manual_review_evidence_warnings_flags_historical_state_without_rewriting_it():
 c=compact(); s=p.initialize_state(c)
 # Simulate state written before this evidentiary check existed: an
 # ACCEPTED decision with no artifact_references at all.
 s['manual_review_requirements']['legacy-job']={'status':'ACCEPTED','reason':'LINEWORK_VISUAL_INSPECTION',
     'reviewer':'op','reviewed_at':'2023-01-01T00:00:00Z'}
 warnings=p.manual_review_evidence_warnings(s)
 assert warnings==[{'job_id':'legacy-job','status':'ACCEPTED','issue':'DECISION_RECORDED_WITHOUT_REVIEWABLE_ARTIFACT'}]
 summary=p.status_summary(c,s)
 assert summary['manual_review_evidence_warnings']==warnings
 assert s['manual_review_requirements']['legacy-job']['status']=='ACCEPTED'  # untouched, not rewritten

def test_package_campaign_copies_referenced_artifacts_and_rewrites_state_paths(tmp_path):
 c=compact(); c['stages']=c['stages'][:1]; c['dependencies']=[]
 c['stages'][0]['jobs'][0]['manual_review_required']=True
 s=p.initialize_state(c); p.generate_next_batch(c,s); a=get(s,'align')
 evidence_dir=tmp_path/'evidence'; evidence_dir.mkdir()
 artifact_path=evidence_dir/'raw.tif'; artifact_path.write_bytes(b'tiff-bytes')
 p.ingest_manifests(c,s,[manifest(s,a,artifact_paths=(str(artifact_path),))])
 p.ingest_analysis(c,s,[analysis(s,a,'PASS')])
 p.record_manual_review(c,s,a['job_id'],'ACCEPTED','operator')
 out_dir=tmp_path/'package'
 result=p.package_campaign(c,s,out_dir)
 packaged=result['packaged_artifacts'][a['job_id']]
 assert len(packaged)==1
 assert (out_dir/packaged[0]).read_bytes()==b'tiff-bytes'
 packaged_state=json.loads((out_dir/'campaign_state.json').read_text())
 assert packaged_state['manual_review_requirements'][a['job_id']]['artifact_references']==packaged
 for ref in packaged_state['manual_review_requirements'][a['job_id']]['artifact_references']:
  assert (out_dir/ref).is_file()  # no dangling reference in the package

def test_package_campaign_refuses_to_ship_a_dangling_artifact_reference(tmp_path):
 c=compact(); c['stages']=c['stages'][:1]; c['dependencies']=[]
 c['stages'][0]['jobs'][0]['manual_review_required']=True
 s=p.initialize_state(c); p.generate_next_batch(c,s); a=get(s,'align')
 p.ingest_manifests(c,s,[manifest(s,a)])  # 'raw.tif' is never actually created on disk
 p.ingest_analysis(c,s,[analysis(s,a,'PASS')])
 p.record_manual_review(c,s,a['job_id'],'ACCEPTED','operator')
 with pytest.raises(p.CampaignError):
  p.package_campaign(c,s,tmp_path/'package')
 assert not (tmp_path/'package').exists() or not any((tmp_path/'package').rglob('*.tif'))

def test_package_campaign_preserves_both_artifacts_when_filenames_collide(tmp_path):
 # Two references from different source directories sharing a basename must
 # not flatten to the same destination and silently overwrite each other.
 c=compact(); c['stages']=c['stages'][:1]; c['dependencies']=[]
 c['stages'][0]['jobs'][0]['manual_review_required']=True
 s=p.initialize_state(c); p.generate_next_batch(c,s); a=get(s,'align')
 dir_a=tmp_path/'run_a'; dir_a.mkdir(); dir_b=tmp_path/'run_b'; dir_b.mkdir()
 artifact_a=dir_a/'detail.tiff'; artifact_a.write_bytes(b'AAAA')
 artifact_b=dir_b/'detail.tiff'; artifact_b.write_bytes(b'BBBB')
 p.ingest_manifests(c,s,[manifest(s,a,artifact_paths=(str(artifact_a),str(artifact_b)))])
 p.ingest_analysis(c,s,[analysis(s,a,'PASS')])
 p.record_manual_review(c,s,a['job_id'],'ACCEPTED','operator')
 result=p.package_campaign(c,s,tmp_path/'package')
 packaged=result['packaged_artifacts'][a['job_id']]
 assert len(packaged)==2 and len(set(packaged))==2  # distinct destinations
 contents={(tmp_path/'package'/ref).read_bytes() for ref in packaged}
 assert contents=={b'AAAA', b'BBBB'}  # both preserved, neither overwritten

# --------------------------------------------------------------------------
# Regressions found by automated review on the manual-review evidence guard.
# --------------------------------------------------------------------------

def test_manual_review_evidence_syncs_on_envelope_less_executor_failure():
 # A job can go straight to terminal FAILED from ingest_manifests alone (no
 # envelope, so the analyzer never runs and ingest_analysis is never
 # called). Its manual-review requirement must still be classified
 # EVIDENCE_MISSING rather than staying a falsely-actionable PENDING that
 # record_manual_review can never resolve.
 c=compact(); c['stages']=c['stages'][:1]; c['dependencies']=[]
 c['stages'][0]['jobs'][0]['manual_review_required']=True
 s=p.initialize_state(c); p.generate_next_batch(c,s); a=get(s,'align')
 bid=a['batch_ids'][-1]
 failed_no_envelope={'schema_version':'1.0','campaign_id':s['campaign_id'],'batch_id':bid,'run_id':'r1',
     'document_identity':{},'environment':{},'batch_source':'batch.json','started_at':'x','completed_at':'y',
     'execution_status':'completed',
     'jobs':[{'job_id':a['job_id'],'configuration_fingerprint':a['execution_fingerprints'][bid],
              'execution_status':'failed','raw_result_envelope':None,
              'errors':[{'type':'RuntimeError','message':'probe raised'}]}],
     'jobs_not_attempted':[]}
 p.ingest_manifests(c,s,[failed_no_envelope])
 assert a['status']=='FAILED'
 requirement=s['manual_review_requirements'][a['job_id']]
 assert requirement['status']=='EVIDENCE_MISSING'
 assert requirement['artifact_references']==[]
 with pytest.raises(p.CampaignError):
  p.record_manual_review(c,s,a['job_id'],'ACCEPTED','operator')
 assert p.generate_next_batch(c,s) is None
 assert s['next_recommendation']=={'code':'BLOCKED_BY_FAILURE','reason':'MANUAL_REVIEW_EVIDENCE_MISSING','job_ids':[a['job_id']]}

def test_migrate_state_backfills_pending_review_evidence_from_old_schema():
 # A state written by a pre-evidence-guard planner never had
 # artifact_references on its requirements at all, even though the job
 # itself already carries a real raster artifact. Re-ingesting the already-
 # recorded analysis is a no-op duplicate, so migration is the only place
 # this can be backfilled without permanently stranding an in-progress
 # review behind the new guard.
 c=compact(); c['stages']=c['stages'][:1]; c['dependencies']=[]
 c['stages'][0]['jobs'][0]['manual_review_required']=True
 s=p.initialize_state(c); p.generate_next_batch(c,s); a=get(s,'align')
 p.ingest_manifests(c,s,[manifest(s,a)]); p.ingest_analysis(c,s,[analysis(s,a,'PASS')])
 old_shape=copy.deepcopy(s)
 del old_shape['manual_review_requirements'][a['job_id']]['artifact_references']
 migrated=p.migrate_state(old_shape)
 requirement=migrated['manual_review_requirements'][a['job_id']]
 assert requirement['status']=='PENDING' and requirement['artifact_references']==['raw.tif']
 p.record_manual_review(c,migrated,a['job_id'],'ACCEPTED','operator')
 assert migrated['manual_review_requirements'][a['job_id']]['status']=='ACCEPTED'

def test_migrate_state_never_rewrites_an_already_recorded_decision():
 c=compact(); c['stages']=c['stages'][:1]; c['dependencies']=[]
 c['stages'][0]['jobs'][0]['manual_review_required']=True
 s=p.initialize_state(c)
 job_id=get(s,'align')['job_id']
 s['manual_review_requirements'][job_id]={'status':'ACCEPTED','reason':'LINEWORK_VISUAL_INSPECTION',
     'reviewer':'op','reviewed_at':'2023-01-01T00:00:00Z'}
 migrated=p.migrate_state(copy.deepcopy(s))
 requirement=migrated['manual_review_requirements'][job_id]
 assert requirement['status']=='ACCEPTED'  # untouched, never resynced/rewritten
 assert requirement['artifact_references']==[]  # only the missing key is backfilled empty, never fabricated

def test_manual_review_on_a_blocked_job_surfaces_the_upstream_diagnostic_not_a_review():
 # A manual-review job that is BLOCKED by a failed dependency may never run
 # at all. It must not be advertised as an actionable PENDING review (nor
 # outrank the upstream failure with a "manual review evidence missing"
 # message) - the operator needs to see the real, fixable problem (align
 # needs a diagnostic), not a review task for a job with nothing to look at.
 c=compact(); c['stages'][1]['jobs']=[j for j in c['stages'][1]['jobs'] if j['job_key']=='mutate']
 c['stages'][1]['jobs'][0]['manual_review_required']=True
 s=p.initialize_state(c)
 run_batch(c,s,'align','FAIL',reason_codes=['MUTATION_ATTESTATION_FAILED'])
 mutate=get(s,'mutate')
 assert mutate['status']=='BLOCKED'
 requirement=s['manual_review_requirements'][mutate['job_id']]
 assert requirement['status']=='EVIDENCE_MISSING'
 assert requirement['artifact_references']==[]
 with pytest.raises(p.CampaignError):
  p.record_manual_review(c,s,mutate['job_id'],'ACCEPTED','operator')
 assert p.generate_next_batch(c,s) is None
 assert s['next_recommendation']=={'code':'BLOCKED_BY_FAILURE'}  # upstream failure surfaced, not a review prompt

def test_stale_missing_evidence_diagnostic_is_cleared_once_a_real_artifact_arrives():
 c=compact(); c['stages']=c['stages'][:1]; c['dependencies']=[]
 c['stages'][0]['jobs'][0]['manual_review_required']=True
 s=p.initialize_state(c); p.generate_next_batch(c,s); a=get(s,'align')
 p.ingest_manifests(c,s,[manifest(s,a,artifact_paths=())]); p.ingest_analysis(c,s,[analysis(s,a,'PASS')])
 requirement=s['manual_review_requirements'][a['job_id']]
 assert requirement['status']=='EVIDENCE_MISSING' and 'diagnostic' in requirement
 # A later manifest for the same job (a fresh run_id) supplies the missing raster.
 p.ingest_manifests(c,s,[manifest(s,a,run='r2',artifact_paths=('raw.tif',))])
 requirement=s['manual_review_requirements'][a['job_id']]
 assert requirement['status']=='PENDING'
 assert requirement['artifact_references']==['raw.tif']
 assert 'diagnostic' not in requirement  # stale "nothing was ever recorded" text must not survive
 p.record_manual_review(c,s,a['job_id'],'ACCEPTED','operator')
 assert s['manual_review_requirements'][a['job_id']]['status']=='ACCEPTED'

# --------------------------------------------------------------------------
# Host color-ID feasibility: core acceptance checks (simplified Stage A
# campaign - see examples/stage_a_campaign.json).
# --------------------------------------------------------------------------

CROSS_VIEWS = ('floor_active', 'floor_inactive', 'rcp', 'section', 'callout')

def _pass_elevation(c, s):
 a=run_batch(c,s,'s1.attached_as','INCONCLUSIVE',reason_codes=['MANUAL_SEMANTIC_REVIEW_REQUIRED'])
 assert a['status']=='INCONCLUSIVE'
 p.record_manual_review(c,s,a['job_id'],'ACCEPTED','operator')
 assert get(s,'s1.attached_as')['status']=='PASSED'

def _accept_cross_view(c, s, view):
 run_batch(c,s,f's2.{view}.confirm','PASS')
 j=run_batch(c,s,f's3.{view}.attached_AS','INCONCLUSIVE',reason_codes=['MANUAL_SEMANTIC_REVIEW_REQUIRED'])
 p.record_manual_review(c,s,j['job_id'],'ACCEPTED','operator')
 assert get(s,f's3.{view}.attached_AS')['status']=='PASSED'

def _reject_cross_view(c, s, view):
 run_batch(c,s,f's2.{view}.confirm','PASS')
 j=run_batch(c,s,f's3.{view}.attached_AS','INCONCLUSIVE',reason_codes=['MANUAL_SEMANTIC_REVIEW_REQUIRED'])
 p.record_manual_review(c,s,j['job_id'],'REJECTED','operator')
 assert get(s,f's3.{view}.attached_AS')['status']=='FAILED'

def test_elevation_accepted_review_satisfies_feasibility_without_second_semantic_gate():
 # Acceptance check: a technically valid elevation raster with ACCEPTED
 # visual review satisfies the elevation feasibility gate outright - no
 # further "semantic preservation" gate independently blocks it once the
 # gate-scoped MANUAL_SEMANTIC_REVIEW_REQUIRED reason was the job's only
 # blocker and the operator accepted the raster.
 c=example(); s=p.initialize_state(c)
 _pass_elevation(c,s)
 feasibility=p.host_color_id_feasibility(c,s)
 assert feasibility['views']['elevation']=={'result':'PASS','job_id':get(s,'s1.attached_as')['job_id'],
     'job_key':'s1.attached_as','closure_id':'elevation_mutation_closure','artifact_references':['raw.tif']}

def test_rollback_failure_still_blocks_the_view():
 c=example(); s=p.initialize_state(c)
 a=run_batch(c,s,'s1.attached_as','FAIL',reason_codes=['ROLLBACK_FAILED'])
 assert a['status']=='FAILED'
 assert p.host_color_id_feasibility(c,s)['views']['elevation']['result']=='FAIL'
 assert p.host_color_id_feasibility(c,s)['conclusion']=='HOST_COLOR_ID_FEASIBILITY_INCONCLUSIVE'  # other views NOT_TESTED

def test_state_restoration_failure_still_blocks_the_view():
 c=example(); s=p.initialize_state(c)
 a=run_batch(c,s,'s1.attached_as','FAIL',reason_codes=['STATE_RESTORATION_FAILED'])
 assert a['status']=='FAILED'
 assert p.host_color_id_feasibility(c,s)['views']['elevation']['result']=='FAIL'

def test_mutation_attestation_failure_still_blocks_the_view():
 c=example(); s=p.initialize_state(c)
 a=run_batch(c,s,'s1.attached_as','FAIL',reason_codes=['MUTATION_ATTESTATION_FAILED'])
 assert a['status']=='FAILED'
 assert p.host_color_id_feasibility(c,s)['views']['elevation']['result']=='FAIL'

def test_missing_raster_still_blocks_the_view_as_not_tested():
 c=example(); s=p.initialize_state(c); p.generate_next_batch(c,s); a=get(s,'s1.attached_as')
 p.ingest_manifests(c,s,[manifest(s,a,artifact_paths=())])
 p.ingest_analysis(c,s,[analysis(s,a,'INCONCLUSIVE',reason_codes=['MANUAL_SEMANTIC_REVIEW_REQUIRED'])])
 assert a['status']=='INCONCLUSIVE'
 with pytest.raises(p.CampaignError):
  p.record_manual_review(c,s,a['job_id'],'ACCEPTED','operator')
 assert p.host_color_id_feasibility(c,s)['views']['elevation']['result']=='NOT_TESTED'

def test_cross_view_matrix_uses_the_single_selected_recipe_not_a_full_factorial():
 # Acceptance check: cross-view validation applies the recipe selected at
 # the elevation reference view, not a mandatory 3-way mutation factorial
 # rerun on every view.
 c=example()
 cross_view_stage=next(st for st in c['stages'] if st['stage_id']=='03_cross_view_host_raster_matrix')
 assert len(cross_view_stage['jobs'])==5
 assert {j['variant'] for j in cross_view_stage['jobs']}=={'attached_AS'}
 assert {j['probe_id'] for j in cross_view_stage['jobs']}=={'stage_a_minimum_id_mutations'}

def test_each_core_view_reaches_independent_review_outcomes_and_aggregate_is_mixed():
 c=example(); s=p.initialize_state(c)
 _pass_elevation(c,s)
 _accept_cross_view(c,s,'floor_active')
 _accept_cross_view(c,s,'floor_inactive')
 _accept_cross_view(c,s,'rcp')
 _accept_cross_view(c,s,'section')
 _reject_cross_view(c,s,'callout')
 feasibility=p.host_color_id_feasibility(c,s)
 assert feasibility['views']['elevation']['result']=='PASS'
 for view in ('floor_active','floor_inactive','rcp','section'):
  assert feasibility['views'][view]['result']=='PASS'
 assert feasibility['views']['callout']['result']=='FAIL'
 assert feasibility['conclusion']=='HOST_COLOR_ID_FEASIBILITY_MIXED'

def test_aggregate_reports_pass_when_every_required_core_view_passes():
 # Rerun-target acceptance check: a fresh campaign run that reaches an
 # ACCEPTED review on every one of the six target views ends with an
 # explicit PASS conclusion, not left implicitly successful.
 c=example(); s=p.initialize_state(c)
 _pass_elevation(c,s)
 run_batch(c,s,'s2.align.r1','PASS'); run_batch(c,s,'s2.align.r2','PASS')
 for view in CROSS_VIEWS:
  _accept_cross_view(c,s,view)
 feasibility=p.host_color_id_feasibility(c,s)
 assert feasibility['conclusion']=='HOST_COLOR_ID_FEASIBILITY_PASS'
 assert {v['result'] for v in feasibility['views'].values()}=={'PASS'}
 assert p.generate_next_batch(c,s) is None
 assert s['next_recommendation']['code']=='CAMPAIGN_COMPLETE'

def test_aggregate_reports_fail_when_every_reachable_core_view_fails():
 c=example(); s=p.initialize_state(c)
 a=run_batch(c,s,'s1.attached_as','INCONCLUSIVE',reason_codes=['MANUAL_SEMANTIC_REVIEW_REQUIRED'])
 p.record_manual_review(c,s,a['job_id'],'REJECTED','operator')
 feasibility=p.host_color_id_feasibility(c,s)
 # Only the reachable (non-cascade-blocked) elevation view concluded FAIL;
 # the rest never ran (cascade-blocked), so they read NOT_TESTED, not FAIL -
 # the overall conclusion stays INCONCLUSIVE rather than fabricating FAIL
 # for views that were never actually exercised.
 assert feasibility['views']['elevation']['result']=='FAIL'
 assert feasibility['conclusion']=='HOST_COLOR_ID_FEASIBILITY_INCONCLUSIVE'

def test_default_feasibility_conclusion_is_inconclusive_before_any_review():
 c=example(); s=p.initialize_state(c)
 feasibility=p.host_color_id_feasibility(c,s)
 assert feasibility['conclusion']=='HOST_COLOR_ID_FEASIBILITY_INCONCLUSIVE'
 assert all(v['result']=='NOT_TESTED' for v in feasibility['views'].values())

def test_external_source_jobs_cannot_block_host_feasibility_because_none_exist_in_core_campaign():
 c=example(); s=p.initialize_state(c)
 _pass_elevation(c,s)
 run_batch(c,s,'s2.align.r1','PASS'); run_batch(c,s,'s2.align.r2','PASS')
 for view in CROSS_VIEWS:
  _accept_cross_view(c,s,view)
 assert not any(j['job_key'].startswith('s7.') for j in s['jobs'].values())
 assert p.host_color_id_feasibility(c,s)['conclusion']=='HOST_COLOR_ID_FEASIBILITY_PASS'
 assert p.generate_next_batch(c,s) is None
 assert s['next_recommendation']['code']=='CAMPAIGN_COMPLETE'

# --------------------------------------------------------------------------
# Review-fixup regressions (PR #187 review findings).
# --------------------------------------------------------------------------

def test_fallback_resolution_retires_the_superseded_primarys_own_review():
 # A closure's primary trigger job (blocked-by-template, no TIFF exported,
 # so its own manual_review_required requirement is EVIDENCE_MISSING) must
 # never strand the campaign after its fallback is materialized, reviewed,
 # and PASSED - only the fallback (the closure's actual candidate) still
 # matters for completion.
 c=example(); s=p.initialize_state(c)
 run_batch(c,s,'s1.attached_as','FAIL',reason_codes=['MUTATION_ATTESTATION_FAILED','MUTATION_BLOCKED_BY_TEMPLATE_ONLY'],artifact_paths=())
 assert s['manual_review_requirements'][get(s,'s1.attached_as')['job_id']]['status']=='EVIDENCE_MISSING'
 fb=run_batch(c,s,'s1.detached_as','INCONCLUSIVE',reason_codes=['MANUAL_SEMANTIC_REVIEW_REQUIRED'])
 p.record_manual_review(c,s,fb['job_id'],'ACCEPTED','operator')
 assert get(s,'s1.detached_as')['status']=='PASSED'
 run_batch(c,s,'s2.align.r1','PASS'); run_batch(c,s,'s2.align.r2','PASS')
 for view in CROSS_VIEWS:
  _accept_cross_view(c,s,view)
 assert p.host_color_id_feasibility(c,s)['conclusion']=='HOST_COLOR_ID_FEASIBILITY_PASS'
 assert p.generate_next_batch(c,s) is None
 assert s['next_recommendation']['code']=='CAMPAIGN_COMPLETE'  # not stranded on the primary's EVIDENCE_MISSING

def test_status_and_feasibility_reject_a_mismatched_state_instead_of_crashing():
 c=example(); s=p.initialize_state(c)
 drifted=copy.deepcopy(c); drifted['description']='changed after init'
 summary=p.status_summary(drifted,s)
 assert summary['next_recommendation']['code']=='INVALID_CAMPAIGN_STATE'
 assert summary['host_color_id_feasibility']=={'conclusion':'HOST_COLOR_ID_FEASIBILITY_INCONCLUSIVE','views':{},'reason':'CAMPAIGN_STATE_MISMATCH'}

def test_feasibility_cli_rejects_a_mismatched_state(tmp_path, capsys):
 c=example(); s=p.initialize_state(c)
 campaign_path=tmp_path/'campaign.json'; state_path=tmp_path/'state.json'
 campaign_path.write_text(json.dumps(c)); state_path.write_text(json.dumps(s))
 drifted=copy.deepcopy(c); drifted['description']='changed after init'
 campaign_path.write_text(json.dumps(drifted))
 exit_code=p.main(['feasibility',str(campaign_path),str(state_path)])
 assert exit_code==1
 out=json.loads(capsys.readouterr().out)
 assert out['reason']=='CAMPAIGN_STATE_MISMATCH'

def test_core_views_mapping_is_validated_at_campaign_validation_time():
 c=example()
 c['host_color_id_feasibility']['core_views']['elevation']='not-an-object'
 pytest.raises(p.CampaignError,p.validate_campaign,c)
 c=example()
 c['host_color_id_feasibility']['core_views']['elevation']={'closure_id':'no_such_closure'}
 pytest.raises(p.CampaignError,p.validate_campaign,c)
 c=example()
 c['host_color_id_feasibility']['core_views']['bogus_view']={'closure_id':'elevation_mutation_closure'}
 pytest.raises(p.CampaignError,p.validate_campaign,c)
 c=example()
 c['host_color_id_feasibility']['core_views']['elevation']={'closure_id':'elevation_mutation_closure','job_key':'s1.attached_as'}
 pytest.raises(p.CampaignError,p.validate_campaign,c)  # exactly one of closure_id/job_key, not both

def test_core_views_mapping_rejects_a_closure_from_a_different_view():
 # A valid closure_id belonging to a *different* view must still be
 # rejected - otherwise host_color_id_feasibility() would double-count one
 # view's candidate job while never checking the mismatched view's own
 # result (e.g. mapping "callout" to the section closure would let a
 # failed callout raster hide behind a passing section result).
 c=example()
 c['host_color_id_feasibility']['core_views']['callout']={'closure_id':'section_mutation_closure'}
 pytest.raises(p.CampaignError,p.validate_campaign,c)

def test_core_views_mapping_rejects_a_job_key_from_a_different_view():
 c=example()
 c['host_color_id_feasibility']['core_views']['callout']={'job_key':'s3.section.attached_AS'}
 pytest.raises(p.CampaignError,p.validate_campaign,c)
