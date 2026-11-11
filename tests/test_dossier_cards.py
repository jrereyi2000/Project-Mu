import copy
import json
import pytest
from tools.dossier_cards import build_cards
from tools.prepare_colab_pilot import export


@pytest.fixture
def packet():
    ev=[]
    def add(name,result,team=None):
        ev.append({'source':name,'arguments':{'team_identifier':team} if team else {},'result':json.dumps(result)})
    for team in ['CAR','AZ']:
        add('get_team_efficiency_profile',dict(record='17-0',points_for=914,points_against=323,pass_yds_per_game=338.4,rush_yds_per_game=211.4,turnover_margin=20,situational=dict(through_week=18,red_zone_td_pct=49.3,red_zone_rank=18,red_zone_td_pct_allowed=50.,red_zone_defense_rank=16,third_down_pct=48.6,third_down_rank=1)),team)
        add('get_team_roster_depth_chart',{'depth_chart_by_position_group':{'EDGE':[dict(name='Telvin McCord',position='REDG',overall_rating=84)]}},team)
        for name in ['get_coach_profile','get_team_injury_report','get_clutch_profile']:
            add(name,{},team)
        add('get_team_season_schedule',dict(team=team,record='18-0',season_schedule=[]),team)
        add('get_record_book',dict(franchise_records=[dict(category='season_rec_tds',value=29)],records_set_this_season=[dict(category='season_rec_tds',result='tied',value=29,previous_record=29)]),team)
    add('get_matchup_edges',{})
    brief={'preview_slate':{'all_games':[dict(game_id=4131,matchup='Cardinals @ Panthers')]},'recap_slate':{'all_games':[dict(game_id=4123,matchup='49ers 37 @ Panthers 41',game_story={'storyline':'recorded winner'})]}}
    return dict(season=2036,week=21,game_id=4131,writer='Panthers_Beat_1',opponent='AZ',evidence=ev,b=dict(system_instruction='WRITER MEMORY: prior take. Creative scenes allowed.',task_instruction='ASSIGNMENT BRIEF:\n'+json.dumps(brief)+'\nBEAT WRITER OPENING VARIETY RULE:\nUse a scene.\nARTICLE STRUCTURE: three matchups.\n\nCANONICAL EVIDENCE DOSSIER:\nold'))


def test_conference_stage_and_target_game_are_explicit(packet):
    result=build_cards(packet)
    assert 'Conference Championship playoff round' in result['assignment_header']
    assert 'NOT a pre-playoff game' in result['assignment_header']
    assert 'Game 4131: Cardinals @ Panthers' in result['assignment_header']


def test_metric_value_rank_and_scope_stay_together(packet):
    card=next(c for c in build_cards(packet)['cards'] if c['kind']=='team' and c['team']=='CAR')
    assert card['metrics'][0]['value']==49.3
    assert card['metrics'][0]['league_rank']==18
    assert card['metrics'][0]['label']=='offensive red-zone TD conversion'
    assert card['regular_season_through_week']==18


def test_render_omits_ratings_but_preserves_positions_and_ties(packet):
    cards=build_cards(packet)['cards']
    roster=next(c for c in cards if c['kind']=='roster')
    assert roster['players']=={'Telvin McCord':'REDG'}
    assert '84' not in json.dumps(roster)
    record=next(c for c in cards if c['kind']=='records')
    assert record['current_season_events'][0]['result']=='tied'
    assert record['current_season_events'][0]['previous_record']==29


def test_export_preserves_persona_and_does_not_mutate_packet(packet):
    before=copy.deepcopy(packet)
    job=export(packet)
    assert packet==before
    assert job['dossier_version']==2
    assert job['messages'][0]['content']==packet['b']['system_instruction']
    assert 'Use a scene.' in job['messages'][1]['content']
    assert 'three matchups' in job['messages'][1]['content']
    assert job['source_index']


def test_missing_required_evidence_fails_instead_of_inventing(packet):
    packet['evidence']=[e for e in packet['evidence'] if e['source']!='get_team_efficiency_profile']
    with pytest.raises(ValueError,match='Missing required evidence'):
        build_cards(packet)


def test_conflicting_player_positions_fail(packet):
    item=next(e for e in packet['evidence'] if e['source']=='get_team_roster_depth_chart')
    item['result']=json.dumps({'depth_chart_by_position_group':{'EDGE':[dict(name='Telvin McCord',position='REDG'),dict(name='Telvin McCord',position='DT')]}})
    with pytest.raises(ValueError,match='Conflicting positions'):
        build_cards(packet)
