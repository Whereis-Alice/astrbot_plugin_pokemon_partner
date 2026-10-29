"""Pity precedence, active-pool progress and persistent collection regressions."""
import random
import tempfile
import unittest

from gacha import GachaRules, Partner
from gacha_store import GachaStore


def partner(key, dex, rarity='C'):
    return Partner(key,dex,str(dex).zfill(4),key,'一般',600 if rarity=='SSR' else 300,rarity,
                   {'name_zh':key,'form_name':'测试形态' if ':' in key else ''})


class GachaStoreRegressionTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(prefix='gacha-store-regression-')
        self.addCleanup(self.folder.cleanup)
        self.rules=GachaRules.from_config({'draw_cooldown_seconds':0,'shiny_chance_percent':0,
            'ssr_pity_after':2,'new_pity_after':1,'rarity_weights':{'C':100,'R':0,'SR':0,'SSR':0}})

    def store(self, partners, rules=None):
        return GachaStore(self.folder.name,partners,rules or self.rules,random.Random(1))

    @staticmethod
    def seed(store, owned, **values):
        with store._transaction():
            profile=store._profile('group:a','alice','爱丽丝')
            profile.update(owned=owned,**values)
            store._save()

    def test_ssr_guarantee_wins_and_new_guarantee_is_kept_for_the_next_draw(self):
        store=self.store((partner('1',1),partner('2',2,'SSR')))
        self.seed(store,{'2':{'normal':1}},pity_ssr=1,pity_new=1)
        first=store.draw('group:a','alice')
        self.assertEqual(first.items[0].rarity,'SSR')
        self.assertFalse(first.items[0].is_new)
        self.assertEqual(first.pity_new,2)
        self.assertEqual(first.pity_ssr,0)
        second=store.draw('group:a','alice')
        self.assertTrue(second.items[0].is_new)
        self.assertEqual(second.items[0].key,'1')
        self.assertEqual(second.pity_new,0)

    def test_both_guarantees_use_an_unowned_ssr_when_available(self):
        store=self.store((partner('1',1),partner('2',2,'SSR'),partner('3',3,'SSR')))
        self.seed(store,{'2':{'normal':1}},pity_ssr=1,pity_new=1)
        result=store.draw('group:a','alice')
        self.assertEqual(result.items[0].key,'3')
        self.assertEqual(result.items[0].rarity,'SSR')
        self.assertTrue(result.items[0].is_new)
        self.assertEqual((result.pity_ssr,result.pity_new),(0,0))

    def test_new_guarantee_can_cross_rarities_when_ssr_guarantee_is_not_due(self):
        store=self.store((partner('1',1),partner('2',2,'SSR')))
        self.seed(store,{'1':{'normal':1}},pity_ssr=0,pity_new=1)
        result=store.draw('group:a','alice')
        self.assertEqual(result.items[0].key,'2')
        self.assertTrue(result.items[0].is_new)

    def test_disallowing_duplicates_uses_unowned_partners_from_other_rarities(self):
        rules=GachaRules.from_config({'draw_cooldown_seconds':0,'shiny_chance_percent':0,
            'ssr_pity_after':0,'new_pity_after':0,'allow_duplicate_draw':False,
            'rarity_weights':{'C':100,'R':0,'SR':0,'SSR':0}})
        store=self.store((partner('1',1),partner('2',2,'R')),rules)
        self.seed(store,{'1':{'normal':1}})
        result=store.draw('group:a','alice')
        self.assertEqual(result.items[0].key,'2')
        self.assertTrue(result.items[0].is_new)

    def test_disallowing_duplicates_still_honors_a_forced_ssr(self):
        rules=GachaRules.from_config({'draw_cooldown_seconds':0,'shiny_chance_percent':0,
            'ssr_pity_after':2,'new_pity_after':0,'allow_duplicate_draw':False,
            'rarity_weights':{'C':100,'R':0,'SR':0,'SSR':0}})
        store=self.store((partner('1',1),partner('2',2,'SSR')),rules)
        self.seed(store,{'2':{'normal':1}},pity_ssr=1)
        result=store.draw('group:a','alice')
        self.assertEqual(result.items[0].rarity,'SSR')
        self.assertFalse(result.items[0].is_new)
        self.assertEqual(result.pity_ssr,0)

    def test_disallowing_duplicates_can_finish_a_draw_after_the_entire_pool_is_owned(self):
        rules=GachaRules.from_config({'draw_cooldown_seconds':0,'shiny_chance_percent':0,
            'ssr_pity_after':0,'new_pity_after':1,'allow_duplicate_draw':False,
            'rarity_weights':{'C':100,'R':0,'SR':0,'SSR':0}})
        store=self.store((partner('1',1),partner('2',2,'R')),rules)
        self.seed(store,{'1':{'normal':1},'2':{'normal':1}},pity_new=1)
        result=store.draw('group:a','alice')
        self.assertEqual(len(result.items),1)
        self.assertEqual(result.used_today,1)
        self.assertFalse(result.items[0].is_new)
        self.assertEqual(result.items[0].dust,rules.dust_for('C'))

    def test_excluded_forms_do_not_count_towards_progress_ranking_or_all_reward(self):
        base=(partner('1',1),partner('2',2))
        all_forms=(*base,partner('1:test',1))
        initial=self.store(all_forms)
        excluded={'normal':2,'shiny':3,'first_normal':'2026-10-28','first_shiny':'2026-10-29'}
        self.seed(initial,{'1':{'normal':1},'1:test':excluded})
        active=self.store(base)
        summary=active.get_profile('group:a','alice')
        self.assertEqual((summary.collected,summary.total,summary.shiny_count),(1,2,0))
        self.assertEqual(len(active.owned_partners('group:a','alice')),summary.collected)
        self.assertEqual(active.leaderboard('group:a')[0][2:],(1,0))
        with active._transaction():
            rewards=active._grant_milestones(active._profile('group:a','alice'),'2026-10-29')
            active._save()
        self.assertEqual(rewards,[])
        self.assertEqual(active.get_profile('group:a','alice').dust,0)
        self.assertEqual(active.snapshot()['scopes']['group:a']['users']['alice']['owned']['1:test'],excluded)
        restored=self.store(all_forms)
        self.assertEqual(restored.get_profile('group:a','alice').collected,2)
        self.assertEqual(restored.owned_partners('group:a','alice')[1].shiny_count,3)

    def test_temporarily_excluded_forms_do_not_repeat_already_claimed_milestones(self):
        store=self.store(tuple(partner(str(n),n) for n in range(1,41)))
        self.seed(store,{str(n):{'normal':1} for n in range(1,10)},milestones={'10':'2026-10-28'})
        self.assertEqual(store.get_profile('group:a','alice').next_milestone,30)
        with store._transaction():
            profile=store._profile('group:a','alice')
            profile['owned']['10']={'normal':1}
            self.assertEqual(store._grant_milestones(profile,'2026-10-29'),[])
            store._save()
        self.assertEqual(store.get_profile('group:a','alice').dust,0)


if __name__=='__main__':
    unittest.main(verbosity=2)
