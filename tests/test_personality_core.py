"""Feature-independent core behavior; synthetic identities only."""
from copy import deepcopy
import pytest
from personality.core import (Identity, PersonalityError, ConflictError, initialize,
                              validate_profile, feature_view, apply_change)


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


def test_safe_real_unknown_missing_and_explicit_permissions():
    real=initialize(identity(),'real',2027,traits=['GUARDED'],evidence={'trait:GUARDED':proof()})
    assert feature_view(real,'voice')['traits']==['GUARDED']
    assert feature_view(real,'contracts')['traits']==[]
    assert feature_view(real,'contracts')['sliders']==dict.fromkeys(real['sliders'],5)
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


def test_real_evolution_cannot_reuse_public_evidence_for_new_permissions():
    p=initialize(identity(),'real',2027,traits=['GUARDED'])
    e=event(p,add='WORKOUT_FANATIC')
    with pytest.raises(PersonalityError): apply_change(p,e)
    e['evidence']=proof(('voice',),source='simulation:simulation-event-1')
    after,_=apply_change(p,e)
    assert feature_view(after,'voice')['traits']==['WORKOUT_FANATIC']
    assert feature_view(after,'progression')['traits']==[]


def test_noop_and_boolean_revision_are_rejected():
    p=initialize(identity(),'generated',2027)
    with pytest.raises(PersonalityError): apply_change(p,event(p,add=p['traits'][0]))
    with pytest.raises(PersonalityError): apply_change(p,event(p,expected_revision=False))
