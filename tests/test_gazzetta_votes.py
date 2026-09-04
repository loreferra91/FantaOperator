"""Synthetic contract tests for the public Gazzetta V/FV page."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fantaoperator.database import Database
from fantaoperator.gazzetta_votes import (
    BASE_URL, EDITION, PROVIDER, configure_preferred_source, gazzetta_votes_url,
    is_gazzetta_votes_url, parse_gazzetta_votes,
)
from fantaoperator.updater import refresh_votes


def fixture(*, day=2, season='2026/2027', rows=None, columns=('V','G','A','R','RS','AG','AM','ES','FV')):
    rows = rows or [
        ('123', 'Esempio A.', 'Att', ('6.5','1','1','1','-','-','1','-','9')),
        ('456', 'Portiere P.', 'Por', ('7','-1','-','1','-','-','-','-','9')),
        ('789', 'Senza V.', 'Cen', ('-','-','-','-','-','-','-','-','-')),
    ]
    head = ''.join(f'<div class="inParameter">{c}</div>' for c in columns)
    body = ''.join(f'''<li><div class="playerName"><span class="playerNameIn"><a href="https://www.gazzetta.it/calcio/giocatori/esempio/{pid}/">{name}</a></span><span class="playerRole">{role}</span><span class="playerRole show-for-small">X</span></div>{''.join(f'<div class="inParameter {"vParameter" if i==0 else "fvParameter" if i==8 else ""}">{v}</div>' for i,v in enumerate(values))}</li>''' for pid,name,role,values in rows)
    return f'''<html><body><h1>Voti Fantacalcio Serie A {day} Giornata Stagione {season}</h1><div class="magicDayList listView magicDayListChkDay"><ul class="magicTeamList"><li class="head"><span class="teamNameIn">Squadra</span>{head}</li>{body}</ul></div><div class="magicDayList matchView"><ul class="magicTeamList">duplicato</ul></div></body></html>'''.encode()


class GazzettaParserTests(unittest.TestCase):
    def parse(self, payload=None, **overrides):
        values = dict(source_url=gazzetta_votes_url('2026-27',2), provider=PROVIDER,
                      edition=EDITION, season='2026-27', matchday=2)
        values.update(overrides)
        return parse_gazzetta_votes(payload or fixture(), **values)

    def test_url_builder_and_allowlist(self):
        self.assertEqual(gazzetta_votes_url('2026/27'), BASE_URL + '/serie-a-2026-27/')
        self.assertTrue(is_gazzetta_votes_url(gazzetta_votes_url('2026-27',2)))
        for url in ('http://www.gazzetta.it/calcio/fantanews/voti/serie-a-2026-27/',
                    'https://evil.test/calcio/fantanews/voti/serie-a-2026-27/',
                    gazzetta_votes_url('2026-27')+'?x=1'):
            self.assertFalse(is_gazzetta_votes_url(url))

    def test_vote_fantavote_events_penalty_and_keeper(self):
        rows = self.parse().records
        attacker, keeper, no_vote = rows
        self.assertEqual((attacker['vote'],attacker['provider_fantavote']), (6.5,9))
        self.assertEqual((attacker['goals'],attacker['penalties_scored'],attacker['assists'],attacker['yellow_cards']), (1,1,1,1))
        self.assertEqual((keeper['goals_conceded'],keeper['penalties_saved']), (1,1))
        self.assertIsNone(no_vote['vote']); self.assertIsNone(no_vote['provider_fantavote'])
        self.assertEqual({r['provider_player_id'] for r in rows}, {'123','456','789'})

    def test_context_columns_identity_roles_and_counts_fail_closed(self):
        failures = [
            (fixture(day=3), {}), (fixture(season='2025/2026'), {}),
            (fixture(columns=('X','G','A','R','RS','AG','AM','ES','FV')), {}),
            (fixture(rows=[('123','X','Mantra',('6','-','-','-','-','-','-','-','6'))]), {}),
            (fixture(rows=[('123','X','Att',('6','1.5','-','-','-','-','-','-','6'))]), {}),
            (fixture(rows=[('123','X','Att',('6','-1','-','-','-','-','-','-','3'))]), {}),
            (fixture(rows=[('123','A','Att',('6','-','-','-','-','-','-','-','6')),('123','B','Att',('6','-','-','-','-','-','-','-','6'))]), {}),
            (b'<html>login</html>', {}),
        ]
        for payload, kwargs in failures:
            with self.subTest(payload=payload[:80]), self.assertRaises(ValueError): self.parse(payload,**kwargs)
        for kwargs in ({'matchday':3},{'season':'2025-26'},{'provider':'Fantacalcio.it'},
                       {'edition':'Altra'},{'source_url':'https://evil.test/voti'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): self.parse(**kwargs)

    def test_homonyms_are_distinguished_by_provider_id(self):
        rows=[('123','Rodriguez J.','Cen',('6','-','-','-','-','-','-','-','6')),
              ('456','Rodriguez J.','Att',('7','1','-','-','-','-','-','-','10'))]
        parsed=self.parse(fixture(rows=rows)).records
        self.assertEqual([r['provider_player_id'] for r in parsed],['123','456'])
        with tempfile.TemporaryDirectory() as directory:
            db=Database(Path(directory)/'homonyms.db'); league=db.league(1)
            db.save_league(1,{'vote_provider':PROVIDER,'vote_edition':EDITION,'season':'2026-27'},league['scoring'])
            db.import_records(1,2,parsed,source_name=PROVIDER,source_url='',payload_hash='h',default_status='PROVVISORIO')
            self.assertEqual(len(db.records(1,2)),2)

    def test_refresh_uses_requested_day_and_imports_published_fv(self):
        with tempfile.TemporaryDirectory() as directory:
            db=Database(Path(directory)/'g.db'); league=db.league(1)
            db.save_league(1,{'vote_provider':PROVIDER,'vote_edition':EDITION,
                'source_url':gazzetta_votes_url('2026-27'),'season':'2026-27'},league['scoring'])
            with patch('fantaoperator.updater.fetch_url',return_value=(fixture(), 'text/html', gazzetta_votes_url('2026-27',2))) as fetch:
                result=refresh_votes(db,db.league(1),2)
            self.assertTrue(result['ok']); self.assertEqual(result['rows'],3)
            fetch.assert_called_once_with(gazzetta_votes_url('2026-27',2),allow_html=True)
            self.assertEqual(db.records(1,2)[0]['provider_fantavote'],9)

    def test_project_preference_is_applied_once(self):
        with tempfile.TemporaryDirectory() as directory:
            db=Database(Path(directory)/'g.db')
            configure_preferred_source(db)
            self.assertEqual(db.league(1)['vote_provider'],PROVIDER)
            db.save_league(1,{'vote_provider':'Altro','vote_edition':'Custom','source_url':''},db.league(1)['scoring'])
            configure_preferred_source(Database(db.path))
            self.assertEqual(db.league(1)['vote_provider'],'Altro')


if __name__=='__main__': unittest.main()
