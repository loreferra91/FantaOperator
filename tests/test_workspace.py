import json
import tempfile
import unittest
from pathlib import Path

from fantaoperator.database import Database
from fantaoperator.engine import optimize_lineup, player_score
from fantaoperator.workspace import lineup_score, parse_roster_csv, roster_csv
from fixtures import complete_roster


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / 'test.db')
        self.db.replace_roster(1, complete_roster())

    def tearDown(self):
        self.temp.cleanup()

    def test_roster_add_remove_and_reopen(self):
        self.db.replace_roster(1, [{'name': 'Nuovo', 'role': 'ATT', 'purchase_cost': 12}])
        roster = Database(self.db.path).roster(1)
        self.assertEqual([r['name'] for r in roster], ['Nuovo'])
        self.assertEqual(roster[0]['purchase_cost'], 12)
        self.db.replace_roster(1, [])
        self.assertEqual(self.db.roster(1), [])

    def test_bad_batch_does_not_partially_replace_roster(self):
        before = self.db.roster(1)
        for bad in ({'name':'Errato','role':'XYZ'}, {'name':'Errato','role':'POR','expected':float('nan')}, {'name':'Errato','role':'POR','purchase_cost':-1}):
            with self.assertRaises(ValueError):
                self.db.replace_roster(1, [{'name':'Valido','role':'POR'}, bad])
            self.assertEqual(self.db.roster(1), before)
        with self.assertRaises(ValueError):
            self.db.replace_roster(1, [{'name':'Nome','role':'POR'}, {'name':'nome','role':'ATT'}])
        self.assertEqual(self.db.roster(1), before)

    def test_csv_roundtrip_and_malformed_rows(self):
        parsed = parse_roster_csv(roster_csv(self.db.roster(1)).encode())
        self.assertEqual(len(parsed), 20)
        self.assertEqual(parsed[0]['name'], self.db.roster(1)[0]['name'])
        for payload in (b'name,role\nA,POR,extra', b'name,role,name\nA,POR,B', b'name,role\nA', b'name,role\nA,Mantra'):
            with self.assertRaises(ValueError):
                parse_roster_csv(payload)
        self.assertEqual(parse_roster_csv(b'name,role\nA,POR')[0]['expected'], 6)

    def test_saved_lineup_survives_roster_edits_and_is_scoped(self):
        formation, selected, _ = optimize_lineup(self.db.roster(1))
        self.db.save_lineup(1, 2, formation, [p['id'] for p in selected])
        saved = self.db.saved_lineup(1, 2)
        self.assertEqual(len(saved['players']), 11)
        self.assertIsNone(self.db.saved_lineup(1, 3))
        self.db.replace_roster(1, [])
        self.assertEqual(Database(self.db.path).saved_lineup(1, 2), saved)
        self.db.save_league(1, {'season':'2027-28'}, {})
        self.assertIsNone(self.db.saved_lineup(1, 2))

    def test_invalid_lineup_does_not_overwrite_saved(self):
        formation, selected, _ = optimize_lineup(self.db.roster(1))
        ids = [p['id'] for p in selected]
        self.db.save_lineup(1, 2, formation, ids)
        before = self.db.saved_lineup(1, 2)
        for invalid in (ids[:10], [ids[0]]*11, [9999]+ids[1:]):
            with self.assertRaises(ValueError):
                self.db.save_lineup(1, 2, formation, invalid)
            self.assertEqual(self.db.saved_lineup(1, 2), before)
        self.db.save_league(1, {'mode':'Mantra'}, {})
        with self.assertRaises(ValueError):
            self.db.save_lineup(1, 2, formation, ids)

    def test_backup_roundtrip_and_invalid_restore_atomic(self):
        formation, selected, _ = optimize_lineup(self.db.roster(1))
        self.db.save_lineup(1, 2, formation, [p['id'] for p in selected])
        backup = self.db.export_workspace(1)
        original = json.loads(backup)
        self.db.replace_roster(1, [])
        self.db.restore_workspace(1, backup.encode())
        self.assertEqual(len(self.db.roster(1)), 20)
        self.assertEqual([p['name'] for p in self.db.saved_lineup(1,2)['players']], [p['name'] for p in selected])
        original['league']['name'] = 'Must not save'
        original['lineups'][0]['players'] = original['lineups'][0]['players'][:10]
        before = self.db.export_workspace(1)
        with self.assertRaises(ValueError):
            self.db.restore_workspace(1, json.dumps(original).encode())
        self.assertEqual(self.db.export_workspace(1), before)

    def test_backup_does_not_export_private_feed_url(self):
        self.db.save_league(1, {'source_url':'https://feed.example/votes?secret=test', 'auto_sync_minutes':5}, {})
        backup = json.loads(self.db.export_workspace(1))
        self.assertEqual(backup['league']['source_url'], '')
        self.assertEqual(backup['league']['auto_sync_minutes'], 0)

    def test_version_one_backup_remains_importable(self):
        data = json.loads(self.db.export_workspace(1))
        data['version'] = 1
        data.pop('transactions'); data.pop('assistant_messages')
        for key in ('bench_size','max_substitutions','defense_modifier_enabled','defense_threshold_low',
                    'defense_threshold_mid','defense_threshold_high','defense_bonus_low','defense_bonus_mid','defense_bonus_high'):
            data['league'].pop(key)
        for lineup in data['lineups']:
            lineup.pop('bench', None)
        self.db.replace_roster(1, [])
        self.db.restore_workspace(1, json.dumps(data).encode())
        self.assertEqual(len(self.db.roster(1)), 20)

    def test_partial_scores_do_not_invent_zeros_or_match_other_team(self):
        selected = [{'name':'A', 'team':'Inter'}, {'name':'B','team':'Milan'}]
        self.assertIsNone(lineup_score(selected, [])['total'])
        self.assertIsNone(lineup_score(selected, [{'name':'A','team':'Roma','fantavote':6}])['total'])
        result = lineup_score(selected, [{'name':'a','team':'Inter','fantavote':0}, {'name':'B','team':'Milan','fantavote':None}])
        self.assertEqual(result['total'], 0)
        self.assertEqual(result['count'], 1)
        self.assertFalse(result['complete'])
        self.assertEqual(result['missing'], ['B'])

    def test_zero_start_probability_is_not_replaced_by_default(self):
        self.assertEqual(player_score({'expected':6, 'start_probability':0, 'risk':'Basso'}), 0)

    def test_ordered_role_substitutions_respect_limit(self):
        starters = [{'name':'Titolare D','role':'DIF'}, {'name':'Titolare A','role':'ATT'}]
        bench = [{'name':'Riserva A','role':'ATT'}, {'name':'Riserva D SV','role':'DIF'}, {'name':'Riserva D','role':'DIF'}]
        records = [
            {'name':'Riserva A','team':'','fantavote':8},
            {'name':'Riserva D SV','team':'','fantavote':None},
            {'name':'Riserva D','team':'','fantavote':6.5},
        ]
        one = lineup_score(starters, records, bench, 1)
        self.assertEqual(one['total'], 6.5)
        self.assertEqual(one['substitutions'], [{'out':'Titolare D','in':'Riserva D','role':'DIF'}])
        self.assertEqual(one['missing'], ['Titolare A'])
        two = lineup_score(starters, records, bench, 2)
        self.assertEqual(two['total'], 14.5)
        self.assertEqual(len(two['substitutions']), 2)

    def test_configurable_defense_modifier_uses_base_votes(self):
        players = [{'name':'P','role':'POR'}] + [{'name':f'D{i}','role':'DIF'} for i in range(4)]
        records = [{'name':p['name'],'team':'','official_vote':6.5,'fantavote':6.5} for p in players]
        rules = {'defense_modifier_enabled':1, 'defense_threshold_low':6, 'defense_bonus_low':1,
                 'defense_threshold_mid':6.5, 'defense_bonus_mid':3,
                 'defense_threshold_high':7, 'defense_bonus_high':6}
        result = lineup_score(players, records, defense_modifier=rules)
        self.assertEqual(result['defense_average'], 6.5)
        self.assertEqual(result['defense_modifier'], 3)
        self.assertEqual(result['total'], 35.5)

    def test_market_operations_are_atomic_and_backed_up(self):
        self.db.replace_roster(1, [{'name':'Uscita','role':'DIF','purchase_cost':10}])
        incoming = {'name':'Entrata','role':'ATT','team':'Roma','expected':7,'vote_provider':'Gazzetta','provider_player_id':'99'}
        self.db.exchange_player(1, self.db.roster(1)[0]['id'], incoming, 12, counterparty='Mario')
        self.assertEqual([p['name'] for p in self.db.roster(1)], ['Entrata'])
        self.assertEqual(len(self.db.transactions(1)), 2)
        with self.assertRaises(ValueError):
            self.db.acquire_player(1, {'name':'Troppo caro','role':'ATT'}, 5000)
        self.assertEqual([p['name'] for p in self.db.roster(1)], ['Entrata'])
        self.db.add_assistant_message(1, 'user', 'memo personale')
        backup = self.db.export_workspace(1)
        self.db.release_player(1, self.db.roster(1)[0]['id'])
        self.db.clear_assistant(1)
        self.db.restore_workspace(1, backup.encode())
        self.assertEqual([p['name'] for p in self.db.roster(1)], ['Entrata'])
        self.assertEqual(len(self.db.transactions(1)), 2)
        self.assertEqual(self.db.assistant_messages(1)[0]['content'], 'memo personale')

    def test_sale_credits_adjust_real_available_budget(self):
        self.db.replace_roster(1, [{'name':'Costo dieci','role':'ATT','purchase_cost':10}])
        self.assertEqual(self.db.available_budget(1), 490)
        self.db.release_player(1, self.db.roster(1)[0]['id'], 4)
        self.assertEqual(self.db.available_budget(1), 494)
        self.db.acquire_player(1, {'name':'Nuovo','role':'ATT'}, 494)
        self.assertEqual(self.db.available_budget(1), 0)


if __name__ == '__main__':
    unittest.main()
