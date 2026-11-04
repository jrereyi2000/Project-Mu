"""Feature-independent core behavior; synthetic identities only."""
from copy import deepcopy
import pytest
import json
from pathlib import Path
from personality.core import (Identity, PersonalityError, ConflictError, initialize,
                              validate_profile, feature_view, apply_change, apply_override,
                              carry_over, profile_as_of, adopt, FEATURE_VIEW_VERSION, legacy)


def identity(key='prospect:2027:one', universe='scratch-universe', role='player'):
    return Identity(universe,role,key)


def proof(features=('voice',), confidence='medium', source='https://example.invalid/interview'):
    return dict(source=source,date='2026-10-31',confidence=confidence,
                reviewer='synthetic review',reason='fixture evidence only',features=list(features))


def event(p, **overrides):
    e=dict(event_id='review-2028',source_event_id='simulation-event-1',season=2028,
           expected_revision=p['revision'],kind='trait',remove=p['traits'][0],
           reason='reviewed synthetic major event',rule_version='test-rule-v1')
    e.update(overrides)
    return e


def test_identity_scopes_and_invalid_namespaces():
    assert identity().persona_id==identity().persona_id
    assert identity().persona_id!=identity(universe='other').persona_id
    assert identity().persona_id!=identity(role='coach').persona_id
    for args in [('', 'player','p'),('u','agent','p'),('u','player',' ')]:
        with pytest.raises(PersonalityError): Identity(*args)


def test_generated_distribution_replay_and_no_global_side_effects():
    import random
    state=random.getstate()
    seen=set()
    for i in range(150):
        p=initialize(identity(str(i)),'generated',2027)
        assert p==initialize(identity(str(i)),'generated',2027)
        assert validate_profile(p)
        assert sum(p['sliders'].values())==30
        seen.add(tuple(p['traits']))
    assert random.getstate()==state
    assert len(seen)>10


def test_real_reads_in_full_unknown_neutral_and_view_version():
    sliders=dict(temperament=7,sociability=3,confidence=6,openness=4,warmth=5,drive=5)
    real=initialize(identity(),'real',2027,sliders=sliders,traits=['GUARDED'],evidence={'trait:GUARDED':proof()})
    for feature in ('voice','contracts','progression'):
        view=feature_view(real,feature)
        assert view['traits']==['GUARDED'] and view['sliders']==sliders
        assert view['origin']=='real' and view['view_version']==FEATURE_VIEW_VERSION=='views-v2'
    unknown=feature_view(initialize(identity(),'unknown',2027),'contracts')
    assert unknown['sliders']==dict.fromkeys(sliders,5) and unknown['traits']==[]
    assert unknown['origin']=='unknown' and unknown['view_version']=='views-v2'
    assert feature_view(None,'contracts')['view_version']=='views-v2'
    for origin in ('real','unknown'):
        assert initialize(identity(),origin,2027)['traits']==[]
    assert feature_view(None,'contracts')['status']=='missing'
    with pytest.raises(PersonalityError):
        initialize(identity(),'unknown',2027,traits=['GUARDED'])
    with pytest.raises(PersonalityError):
        initialize(identity(),'real',2027,traits=['MONEY_FIRST'],evidence={'trait:MONEY_FIRST':proof(('contracts',),'low')})


def test_invalid_catalogue_profile_and_role_not_silently_reseeded():
    p=initialize(identity(),'generated',2027)
    p['catalogue']['GUARDED']['label']='tampered'
    with pytest.raises(PersonalityError): feature_view(p,'contracts')
    with pytest.raises(PersonalityError):
        initialize(identity(),'real',2027,traits=['TEACHER'])
    with pytest.raises(PersonalityError):
        initialize(identity(),'real',2027,traits=['MONEY_FIRST','RING_CHASER'])


def test_pure_evolution_annual_revision_and_catalogue_constraints():
    p=initialize(identity(),'generated',2027)
    before=deepcopy(p)
    after,audit=apply_change(p,event(p))
    assert p==before
    assert after['revision']==1 and len(after['traits'])==3
    assert audit['before']==p and audit['after']==after
    with pytest.raises(ConflictError): apply_change(after,event(after,event_id='other'))
    with pytest.raises(ConflictError): apply_change(after,event(p,season=2029))
    with pytest.raises(PersonalityError): apply_change(after,event(after,season=2029))  # cannot leave only two
    with pytest.raises(ConflictError): apply_change(p,event(p,season=2026))


def test_one_step_sliders_follow_active_policy_and_preserve_before_state(monkeypatch):
    p=initialize(identity(),'real',2027)
    e=event(dict(p,traits=['GUARDED']),kind='slider',slider='drive',step=1,
            evidence=proof(('contracts',),'high','simulation:simulation-event-1'))
    e.pop('remove')
    if p['slider_bounds']==[30,30]:
        with pytest.raises(PersonalityError): apply_change(p,e)
    from personality.core import legacy
    monkeypatch.setattr(legacy, 'SLIDER_BUDGET_MIN', 24, raising=False)
    monkeypatch.setattr(legacy, 'SLIDER_BUDGET_MAX', 36, raising=False)
    after,_=apply_change(p,e)
    assert after['sliders']['drive']==6
    assert after['slider_bounds']==[24,36]
    assert feature_view(after,'contracts')['sliders']['drive']==6
    e['step']=2
    with pytest.raises(PersonalityError): apply_change(p,e)


def test_real_evolution_has_no_evidence_gate_and_still_records_evidence():
    p=initialize(identity(),'real',2027,traits=['GUARDED'])
    e=event(p,add='WORKOUT_FANATIC')
    e.pop('remove')
    after,_=apply_change(p,e)
    assert after['traits']==['GUARDED','WORKOUT_FANATIC'] and 'trait:WORKOUT_FANATIC' not in after['evidence']
    assert feature_view(after,'progression')['traits']==['GUARDED','WORKOUT_FANATIC']
    e['evidence']=proof(('voice',),source='simulation:simulation-event-1')
    after,_=apply_change(p,e)
    assert after['evidence']['trait:WORKOUT_FANATIC']==e['evidence']


def test_noop_and_boolean_revision_are_rejected():
    p=initialize(identity(),'generated',2027)
    with pytest.raises(PersonalityError): apply_change(p,event(p,add=p['traits'][0]))
    with pytest.raises(PersonalityError): apply_change(p,event(p,expected_revision=False))


# ---- core extensions (docs/done/PERSONALITY_CORE_EXTENSIONS_PLAN.md) ----

def writer_catalogue():
    """The real catalogue plus the two writer odds fixes (plan Q1); the file itself belongs to U2."""
    cat=legacy.load_traits()
    for tid in ('SUPERSTITIOUS','WORKOUT_FANATIC'):
        cat[tid]=dict(cat[tid],roles=dict(cat[tid]['roles'],writer=0.05))
    return cat


def shift(sliders):
    up=next(k for k,v in sliders.items() if v<10)
    down=next(k for k,v in sliders.items() if v>0 and k!=up)
    return {up:sliders[up]+1,down:sliders[down]-1}


def override(p, **kw):
    r=dict(override_id='jr-1',actor='JR',reason='JR dislikes this',season=2028,tick=5,
           expected_revision=p['revision'],date='2026-11-04')
    r.update(kw)
    return r


def test_override_validates_keeps_annual_slot_and_records_evidence():
    p=initialize(identity(),'generated',2027)
    q,_=apply_change(p,event(p,remove=None,add=None,kind='slider',slider='drive',step=1 if p['sliders']['drive']<10 else -1))
    assert q['last_changed_season']==2028
    removed=q['traits'][0]
    addable=next(t for t,v in q['catalogue'].items() if v['roles'].get('player',0)>0 and t not in q['traits']
                 and not set(v.get('conflicts',[]))&set(q['traits'][1:])
                 and sum(q['catalogue'][x]['category']==v['category'] for x in q['traits'][1:])<2)
    after,audit=apply_override(q,override(q,remove_traits=[removed],add_traits=[addable]))
    assert after['revision']==q['revision']+1 and after['last_changed_season']==2028
    assert audit['kind']=='override' and audit['before']==q and audit['after']==after
    assert removed not in after['traits'] and addable in after['traits']
    ev=after['evidence']['trait:'+addable]
    assert ev=={'source':'override:jr-1','date':'2026-11-04','confidence':'high','reviewer':'JR',
                'reason':'JR dislikes this','features':['all']}
    # Same season as the evolution: overrides are outside the annual slot.
    again,_=apply_override(after,override(after,override_id='jr-2',sliders=shift(after['sliders'])))
    assert again['revision']==after['revision']+1


def test_override_rules_band_traits_roles_actor_and_unknown():
    p=initialize(identity(),'real',2027,traits=['GUARDED'])
    with pytest.raises(PersonalityError): apply_override(p,override(p,sliders={s:10 for s in p['sliders']}))  # total 60
    with pytest.raises(PersonalityError): apply_override(p,override(p,sliders={'drive':11}))
    with pytest.raises(PersonalityError): apply_override(p,override(p,sliders={'luck':5}))
    with pytest.raises(PersonalityError): apply_override(p,override(p,add_traits=['TEACHER']))  # not a player trait
    with pytest.raises(PersonalityError): apply_override(p,override(p,add_traits=['MONEY_FIRST','RING_CHASER']))
    with pytest.raises(PersonalityError): apply_override(p,override(p,actor='Claude',sliders={'drive':6,'warmth':4}))
    with pytest.raises(PersonalityError): apply_override(p,override(p,reason=' '))
    with pytest.raises(PersonalityError): apply_override(p,override(p))  # no change
    with pytest.raises(PersonalityError): apply_override(p,override(p,tick=33,sliders={'drive':6,'warmth':4}))
    with pytest.raises(ConflictError): apply_override(p,override(p,expected_revision=3,sliders={'drive':6,'warmth':4}))
    u=initialize(identity(),'unknown',2027)
    with pytest.raises(PersonalityError): apply_override(u,override(u,sliders={'drive':6,'warmth':4}))
    after,_=apply_override(p,override(p,sliders={'drive':6,'warmth':4},remove_traits=['GUARDED']))
    assert after['traits']==[] and 'trait:GUARDED' not in after['evidence']
    assert sum(after['sliders'].values())==30 and after['last_changed_season'] is None


def test_adopted_identity_keeps_legacy_id_and_uuid_path_unchanged():
    a=Identity.adopted('u','writer','ESPN')
    assert a.persona_id=='ESPN' and a.birth_key=='legacy:writer:ESPN'
    assert Identity('u','writer','legacy:writer:ESPN')==a
    import uuid
    plain=identity()
    expected=str(uuid.uuid5(uuid.NAMESPACE_URL,'project-mu:personality:'+json.dumps(
        ['scratch-universe','player','prospect:2027:one'],separators=(',',':'))))
    assert plain.legacy_persona_id is None and plain.persona_id==expected
    with pytest.raises(PersonalityError): Identity('u','writer','legacy:writer:X','Y')
    with pytest.raises(PersonalityError): Identity.adopted('u','writer','x'*65)


def test_all_40_writers_validate_as_adopted_real_profiles():
    personas=json.loads((Path(__file__).resolve().parents[1]/'newsroom'/'config'/'agent_personas.json').read_text())['archetypes']
    assert len(personas)==40
    cat=writer_catalogue()
    for key,p in personas.items():
        prof=adopt(Identity.adopted('u','writer',key),'real',2026,p['sliders'],p['traits'],catalogue=cat)
        assert prof['persona_id']==key and prof['legacy_persona_id']==key
        assert validate_profile(prof)
        assert feature_view(prof,'voice')['traits']==p['traits']


def test_adopted_gm_generated_keeps_legacy_values():
    seed=legacy.seed_persona('gm','7')
    prof=adopt(Identity.adopted('u','gm','7'),'generated',2026,seed['sliders'],seed['traits'])
    assert prof['sliders']==seed['sliders'] and prof['traits']==seed['traits']
    with pytest.raises(PersonalityError): adopt(identity(),'generated',2026,seed['sliders'],seed['traits'])


def test_carry_over_copies_sliders_drops_ineligible_traits_no_redraw():
    p=initialize(identity(),'real',2027,traits=['GUARDED','WORKOUT_FANATIC'],
                 evidence={'trait:GUARDED':proof(),'trait:WORKOUT_FANATIC':proof()})
    cat=deepcopy(p['catalogue'])
    cat['GUARDED']['roles']['gm']=1
    cat['WORKOUT_FANATIC']['roles']['gm']=0
    gm=Identity('scratch-universe','gm','person:one')
    c=carry_over(p,gm,2031,catalogue=cat)
    assert c['sliders']==p['sliders'] and c['traits']==['GUARDED']
    assert set(c['evidence'])=={'trait:GUARDED'} and c['origin']=='real'
    assert c['revision']==0 and c['initialized_season']==2031 and c['last_changed_season'] is None
    assert c['links']==[{'direction':'from','role':'player','persona_id':p['persona_id']}]
    assert c['persona_id']==gm.persona_id and validate_profile(c)
    with pytest.raises(PersonalityError): carry_over(p,identity(key='other'),2031)  # same role
    with pytest.raises(PersonalityError): carry_over(p,Identity('elsewhere','gm','x'),2031)
    with pytest.raises(PersonalityError): carry_over(initialize(identity(),'unknown',2027),gm,2031)


def test_generated_profile_carried_down_to_two_traits_still_validates():
    p=initialize(identity(),'generated',2027)
    cat=deepcopy(p['catalogue'])
    for t in p['traits'][2:]:
        cat[t]['roles']['coach']=0
    for t in p['traits'][:2]:
        cat[t]['roles']['coach']=1
    c=carry_over(p,Identity('scratch-universe','coach','person:one'),2033,catalogue=cat)
    assert c['traits']==p['traits'][:2] and c['origin']=='generated' and validate_profile(c)
    bad=dict(deepcopy(c),links=[])
    with pytest.raises(PersonalityError): validate_profile(bad)


def receipt(season, before, after, tick=None, seq=0, kind='evolution'):
    return dict(season=season,effective_tick=tick,seq=seq,kind=kind,before_state=before,after_state=after)


def test_profile_as_of_orders_by_season_tick_and_seq():
    p0=initialize(identity(),'generated',2027)
    p1,_=apply_change(p0,event(p0,remove=None,kind='slider',slider='drive',step=1 if p0['sliders']['drive']<10 else -1))
    p2,_=apply_override(p1,override(p1,tick=10,sliders=shift(p1['sliders'])))
    receipts=[receipt(2028,p0,p1,seq=1),receipt(2028,p1,p2,tick=10,seq=2,kind='override')]
    assert profile_as_of(p2,receipts,2026,5) is None
    assert profile_as_of(p2,receipts,2027,32)==p0
    assert profile_as_of(p2,receipts,2028,1)==p1      # untimed evolution counts as tick 1
    assert profile_as_of(p2,receipts,2028,9)==p1
    assert profile_as_of(p2,receipts,2028,10)==p2
    assert profile_as_of(p2,receipts,2030,None)==p2
    assert profile_as_of(p0,[],2027,1)==p0
    assert profile_as_of(None,[],2027,1) is None
    # Same position: seq decides.
    same=[receipt(2028,p0,p1,tick=4,seq=1),receipt(2028,p1,p2,tick=4,seq=2,kind='override')]
    assert profile_as_of(p2,same,2028,4)==p2


def test_profile_as_of_carried_profile_start():
    p=initialize(identity(),'generated',2027)
    cat=deepcopy(p['catalogue'])
    for t in p['traits']:
        cat[t]['roles']['coach']=1
    c=carry_over(p,Identity('scratch-universe','coach','person:one'),2033,catalogue=cat)
    receipts=[receipt(2033,p,c,tick=3,kind='role_link')]
    assert profile_as_of(c,receipts,2032,32) is None
    assert profile_as_of(c,receipts,2033,3)==c
    assert profile_as_of(c,receipts,2033,2)==p   # before the link: the person as he was


def test_extension_migration_finds_the_season_constraint_and_refuses_a_mismatch():
    text=(Path(__file__).resolve().parents[1]/'db'/'migrations'/'2027_01_29_personality_core_extensions.sql').read_text()
    assert 'pg_constraint' in text and "ARRAY['persona_id', 'persona_type', 'season']" in text
    assert 'RAISE EXCEPTION' in text and "WHERE kind = 'evolution'" in text
    assert text.count('BEGIN;')==1 and text.count('COMMIT;')==1
