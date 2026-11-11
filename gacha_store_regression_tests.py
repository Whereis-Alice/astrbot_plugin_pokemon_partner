"""Pity precedence, active-pool progress and persistent collection regressions."""
import random
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from gacha import GachaError, GachaRules, Partner
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

    def batch_rules(self, daily_limit=1, **options):
        return GachaRules.from_config(dict(
            draws_per_command=10, daily_draw_limit=daily_limit,
            draw_cooldown_seconds=0, shiny_chance_percent=0,
            ssr_pity_after=0, new_pity_after=0,
            rarity_weights={'C':100,'R':0,'SR':0,'SSR':0}, **options))

    def test_daily_one_summon_delivers_full_ten_and_second_summon_is_rejected(self):
        store=self.store((partner('1',1),partner('2',2)),self.batch_rules())
        with patch.object(store,'_choose',return_value=store.partners[0]):
            first=store.draw('group:a','alice')
        self.assertEqual((len(first.items),first.used_today,first.remaining,first.daily_limit),(10,1,0,1))
        self.assertFalse(first.capped)
        profile=store.get_profile('group:a','alice')
        self.assertEqual((profile.used_today,profile.total_draws),(1,10))
        before=store.snapshot()
        with self.assertRaises(GachaError):
            store.draw('group:a','alice')
        self.assertEqual(store.snapshot(),before)

    def test_two_daily_summons_each_deliver_complete_ten(self):
        store=self.store((partner('1',1),partner('2',2)),self.batch_rules(2))
        with patch.object(store,'_choose',return_value=store.partners[0]):
            first=store.draw('group:a','alice')
            second=store.draw('group:a','alice')
        self.assertEqual((len(first.items),first.remaining),(10,1))
        self.assertEqual((len(second.items),second.used_today,second.remaining),(10,2,0))
        self.assertEqual(store.get_profile('group:a','alice').total_draws,20)
        with self.assertRaises(GachaError):
            store.draw('group:a','alice')

    def test_extra_summon_bonus_delivers_another_complete_batch(self):
        store=self.store((partner('1',1),partner('2',2)),self.batch_rules())
        store.add_bonus('group:a','alice',1)
        with patch.object(store,'_choose',return_value=store.partners[0]):
            results=[store.draw('group:a','alice') for _ in range(2)]
        self.assertEqual([len(result.items) for result in results],[10,10])
        self.assertEqual((results[-1].daily_limit,results[-1].remaining),(2,0))
        with self.assertRaises(GachaError):
            store.draw('group:a','alice')

    def test_milestone_reward_adds_one_summon_rather_than_one_card(self):
        store=self.store(tuple(partner(str(n),n) for n in range(1,121)),self.batch_rules())
        self.seed(store,{str(n):{'normal':1} for n in range(1,100)},
                  milestones={str(n):'2026-11-10' for n in (10,30,60)})
        with patch.object(store,'_choose',return_value=store.partners[99]):
            first=store.draw('group:a','alice')
            second=store.draw('group:a','alice')
        self.assertEqual((len(first.items),first.daily_limit,first.remaining),(10,2,1))
        self.assertEqual([(reward.threshold,reward.bonus) for reward in first.milestones],[(100,1)])
        self.assertEqual((len(second.items),second.used_today,second.remaining),(10,2,0))

    def test_pity_and_total_draws_continue_to_count_each_card_in_a_batch(self):
        rules=GachaRules.from_config(dict(draws_per_command=10,daily_draw_limit=1,
            draw_cooldown_seconds=0,shiny_chance_percent=0,ssr_pity_after=2,new_pity_after=0,
            rarity_weights={'C':100,'R':0,'SR':0,'SSR':0}))
        store=self.store((partner('1',1),partner('2',2,'SSR'),partner('3',3)),rules)
        result=store.draw('group:a','alice')
        self.assertEqual([item.rarity for item in result.items],['C','SSR']*5)
        self.assertEqual((result.used_today,result.pity_ssr),(1,0))
        self.assertEqual(store.get_profile('group:a','alice').total_draws,10)

    def test_partial_batch_failure_restores_quota_collection_and_random_state(self):
        store=self.store(tuple(partner(str(n),n) for n in range(1,41)),self.batch_rules())
        self.seed(store,{})
        before,rng=store.snapshot(),store.rng.getstate()
        original=store._draw_one
        calls=0
        def fail_after_two(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==3:raise RuntimeError('third card failed')
            return original(*args,**kwargs)
        with patch.object(store,'_draw_one',side_effect=fail_after_two),self.assertRaises(RuntimeError):
            store.draw('group:a','alice')
        self.assertEqual(store.snapshot(),before)
        self.assertEqual(store.rng.getstate(),rng)
        self.assertEqual(len(store.draw('group:a','alice').items),10)

    def test_two_instances_share_daily_summon_limit_under_concurrent_requests(self):
        partners=(partner('1',1),partner('2',2))
        stores=[self.store(partners,self.batch_rules(2)) for _ in range(2)]
        def draw(index):
            try:return stores[index%2].draw('group:a','alice')
            except GachaError:return None
        with patch.object(stores[0],'_choose',return_value=partners[0]), \
                patch.object(stores[1],'_choose',return_value=partners[0]):
            with ThreadPoolExecutor(max_workers=6) as pool:
                results=[result for result in pool.map(draw,range(12)) if result is not None]
        self.assertEqual(len(results),2)
        self.assertEqual([len(result.items) for result in results],[10,10])
        self.assertEqual(sorted(result.used_today for result in results),[1,2])
        profile=stores[0].get_profile('group:a','alice')
        self.assertEqual((profile.used_today,profile.remaining,profile.total_draws),(2,0,20))

    def test_existing_daily_usage_is_kept_when_batch_quantity_changes(self):
        store=self.store((partner('1',1),partner('2',2)),self.batch_rules(5))
        from gacha_store import today_key
        self.seed(store,{},counts={today_key():3})
        self.assertEqual(store.get_profile('group:a','alice').used_today,3)
        with patch.object(store,'_choose',return_value=store.partners[0]):
            result=store.draw('group:a','alice')
        self.assertEqual((len(result.items),result.used_today,result.remaining),(10,4,1))


if __name__=='__main__':
    unittest.main(verbosity=2)
