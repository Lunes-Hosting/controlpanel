"""Plan bandwidth, addon delivery and account isolation without live services."""
from copy import deepcopy
from functools import wraps
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import requests
from flask import Flask, Blueprint, request, session, flash, redirect, jsonify

from runtime.bandwidth import plan_bandwidth, creation_limits, resource_limits, bandwidth_view
from runtime.display_cache import DisplayCache
from tests.test_credit_transactions import load_functions
from tests import test_server_verification


def plan(**limits):
    return dict(id=1, enabled=True, name='Test', price=50,
                limits=dict(memory=512, disk=3000, cpu=100, **limits), product_limits=dict(backups=1))


def policy_data(limit=1000, speed=100, action='throttle', overage=5):
    return dict(bandwidth_custom=True,
                saved=dict(bandwidth_limit_gb=limit, bandwidth_speed_mbps=speed,
                           bandwidth_overage_action=action, bandwidth_overage_mbps=overage),
                effective=dict(limit_gb=limit, speed_mbps=speed, overage_action=action, overage_mbps=overage),
                usage=dict(used_bytes=2500000000, reset_at='2026-11-23 12:00:00'))


def response(data=None, status=200):
    result=Mock(status_code=status)
    result.json.return_value={'data':data}
    if status >= 400:result.raise_for_status.side_effect=requests.HTTPError('API unavailable')
    return result


class BandwidthConfigurationTests(unittest.TestCase):
    def test_missing_zero_and_none_explicitly_override_fleet_defaults(self):
        for fields in ({},{'bandwidth_gb':0},{'bandwidth_gb':None},
                       dict(bandwidth_gb=0,network_speed_mbps=100,bandwidth_overage_action='throttle',bandwidth_overage_mbps=3)):
            with self.subTest(fields=fields):
                result=creation_limits(plan(**fields))
                self.assertTrue(result['bandwidth_custom'])
                self.assertEqual((result['bandwidth_gb'],result['network_speed_mbps']),(0,0))

    def test_throttle_configuration_uses_correct_creation_and_patch_field_names(self):
        product=plan(bandwidth_gb=1000,network_speed_mbps=100,bandwidth_overage_action='throttle',bandwidth_overage_mbps=5)
        original=deepcopy(product)
        self.assertEqual(plan_bandwidth(product),dict(bandwidth_limit_gb=1000,bandwidth_speed_mbps=100,
            bandwidth_overage_action='throttle',bandwidth_overage_mbps=5,bandwidth_custom=True))
        self.assertEqual(creation_limits(product)['bandwidth_gb'],1000)
        self.assertNotIn('bandwidth_limit_gb',creation_limits(product))
        self.assertNotIn('bandwidth_gb',resource_limits(product))
        self.assertEqual(product,original)

    def test_block_default_and_blocked_alias(self):
        for action in ('block','blocked'):
            result=plan_bandwidth(plan(bandwidth_gb=10,bandwidth_overage_action=action))
            self.assertEqual(result['bandwidth_overage_action'],'block')
            self.assertEqual(result['bandwidth_speed_mbps'],0)

    def test_invalid_policy_values_fail_closed(self):
        for fields in (dict(bandwidth_gb=-1),dict(bandwidth_gb=True),dict(bandwidth_gb=1.5),
                       dict(bandwidth_gb='100'),dict(bandwidth_gb=1000001),
                       dict(bandwidth_gb=1,network_speed_mbps=100001),
                       dict(bandwidth_gb=1,bandwidth_overage_action='ignore'),
                       dict(bandwidth_gb=1,bandwidth_overage_action='throttle',bandwidth_overage_mbps=0)):
            with self.subTest(fields=fields),self.assertRaises(ValueError):plan_bandwidth(plan(**fields))

    def test_usage_is_decimal_gb_and_effective_policy_is_authoritative(self):
        data=policy_data();data['saved']['bandwidth_limit_gb']=1
        view=bandwidth_view(data)
        self.assertEqual(view['limit_gb'],1000)
        self.assertEqual(view['used_gb'],2.5)
        self.assertFalse(view['exhausted'])
        data['usage']['used_bytes']=1000000000000
        self.assertTrue(bandwidth_view(data)['exhausted'])


class BandwidthCreationTests(unittest.TestCase):
    def fixture(self, product):
        case=test_server_verification.ServerVerificationTests();case.setUp();case.login()
        case.ns['get_user_verification_status'].return_value=True
        case.ns['products']=[product]
        case.ns['reserve_server_creation']=Mock(return_value='creation-id')
        return case

    def test_creation_sends_configured_limits_not_client_supplied_policy(self):
        case=self.fixture(plan(bandwidth_gb=1000,network_speed_mbps=100,bandwidth_overage_action='throttle',bandwidth_overage_mbps=5))
        original=deepcopy(case.ns['products'])
        case.form.update(bandwidth_gb='0',network_speed_mbps='0',bandwidth_custom='false')
        self.assertEqual(case.client.post('/servers/create/submit',data=case.form).location,'/servers/1')
        limits=case.ns['requests'].post.call_args.kwargs['json']['limits']
        self.assertEqual(limits['bandwidth_gb'],1000)
        self.assertEqual(limits['network_speed_mbps'],100)
        self.assertEqual(limits['bandwidth_overage_mbps'],5)
        self.assertTrue(limits['bandwidth_custom'])
        self.assertEqual(case.ns['products'],original)

    def test_invalid_plan_does_not_debit_or_reserve_a_server(self):
        case=self.fixture(plan(bandwidth_gb=-1))
        self.assertEqual(case.client.post('/servers/create/submit',data=case.form).status_code,503)
        case.ns['remove_credits'].assert_not_called()
        case.ns['reserve_server_creation'].assert_not_called()
        self.assertEqual(case.ns['requests'].post.call_count,1) # captcha only

    def test_unconfigured_plan_is_created_with_explicit_unlimited_policy(self):
        case=self.fixture(plan())
        self.assertEqual(case.client.post('/servers/create/submit',data=case.form).status_code,302)
        limits=case.ns['requests'].post.call_args.kwargs['json']['limits']
        self.assertEqual((limits['bandwidth_gb'],limits['network_speed_mbps']),(0,0))
        self.assertTrue(limits['bandwidth_custom'])


class BandwidthApiTests(unittest.TestCase):
    def setUp(self):
        self.api=Mock();self.cache=DisplayCache()
        self.ns=load_functions('managers/bandwidth_manager.py',dict(
            requests=self.api,PTERODACTYL_URL='https://panel.example.com/',HEADERS={'Authorization':'private'},
            display_cache=self.cache,bandwidth_view=bandwidth_view,logger=Mock()))
        self.api.get.return_value=response(policy_data())
        self.api.patch.return_value=response(policy_data())
        self.policy=plan_bandwidth(plan(bandwidth_gb=1000,network_speed_mbps=100,
            bandwidth_overage_action='throttle',bandwidth_overage_mbps=5))

    def test_display_cache_reuses_only_bandwidth_data(self):
        for _ in range(3):self.assertEqual(self.ns['read_bandwidth'](7)['limit_gb'],1000)
        self.api.get.assert_called_once_with('https://panel.example.com/api/application/servers/7/bandwidth',headers={'Authorization':'private'},timeout=(3,5))

    def test_missing_addon_is_reported_as_unavailable(self):
        self.api.get.return_value=response(status=404)
        self.assertEqual(self.ns['read_bandwidth'](7),{'available':False})

    def test_plan_update_applies_policy_before_build_and_invalidates_cache(self):
        self.ns['read_bandwidth'](7)
        self.ns['update_plan'](7,{'memory':512},self.policy)
        self.assertEqual(len(self.api.patch.call_args_list),2)
        self.assertTrue(self.api.patch.call_args_list[0].args[0].endswith('/7/bandwidth'))
        self.assertEqual(self.api.patch.call_args_list[0].kwargs['json'],self.policy)
        self.assertTrue(self.api.patch.call_args_list[1].args[0].endswith('/7/build'))
        self.assertFalse(self.cache.entries)

    def test_downgrade_to_unlimited_explicitly_clears_all_caps(self):
        self.api.patch.return_value=response(policy_data(limit=0,speed=0,action='block',overage=1))
        self.ns['update_plan'](7,{'memory':512},plan_bandwidth(plan()))
        payload=self.api.patch.call_args_list[0].kwargs['json']
        self.assertEqual((payload['bandwidth_limit_gb'],payload['bandwidth_speed_mbps']),(0,0))
        self.assertTrue(payload['bandwidth_custom'])

    def test_stock_panel_allows_unlimited_updates_but_never_ignores_enabled_limits(self):
        self.api.get.return_value=response(status=404)
        self.ns['update_plan'](7,{'memory':512},plan_bandwidth(plan()))
        self.assertTrue(self.api.patch.call_args.args[0].endswith('/build'))
        self.api.patch.reset_mock()
        with self.assertRaises(requests.HTTPError):self.ns['update_plan'](7,{},self.policy)
        self.api.patch.assert_not_called()

    def test_build_failure_restores_prior_saved_and_inherited_settings(self):
        previous=policy_data(limit=10,speed=2,action='block',overage=1)
        previous['bandwidth_custom']=False
        self.api.get.return_value=response(previous)
        self.api.patch.side_effect=[response(policy_data()),response(status=422),response(),response()]
        with self.assertRaises(requests.HTTPError):self.ns['update_plan'](7,{},self.policy)
        calls=self.api.patch.call_args_list
        self.assertEqual(calls[2].kwargs['json'],dict(previous['saved'],bandwidth_custom=True))
        self.assertEqual(calls[3].kwargs['json'],{'bandwidth_custom':False})
        self.assertFalse(self.cache.entries)

    def test_mismatched_effective_policy_does_not_update_build(self):
        self.api.patch.return_value=response(policy_data(limit=123))
        with self.assertRaises(ValueError):self.ns['update_plan'](7,{},self.policy)
        self.assertTrue(all('/build' not in call.args[0] for call in self.api.patch.call_args_list))


class BandwidthOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.app=Flask(__name__);self.app.secret_key='test'
        self.info={'attributes':{'id':7,'user':42}}
        self.reader=Mock(return_value={'available':True,'limit_gb':1000})
        self.lookup=Mock(return_value=self.info)
        ns=dict(servers=Blueprint('servers',__name__),login_required=lambda f:f,
            get_ptero_id=lambda _: (42,),get_server_information=self.lookup,read_bandwidth=self.reader,
            session=session,jsonify=jsonify)
        load_functions('Routes/Servers.py',ns,{'server_bandwidth'})
        self.app.register_blueprint(ns['servers'],url_prefix='/servers')
        self.client=self.app.test_client()
        with self.client.session_transaction() as state:state['email']='user@example.com'

    def test_owner_can_read_policy_with_private_cache_headers(self):
        result=self.client.get('/servers/7/bandwidth')
        self.assertEqual(result.json,{'available':True,'limit_gb':1000})
        self.assertIn('no-store',result.headers['Cache-Control'])
        self.reader.assert_called_once_with(7)

    def test_non_owner_never_reads_even_cached_policy(self):
        self.info['attributes']['user']=99
        self.assertEqual(self.client.get('/servers/7/bandwidth').status_code,403)
        self.reader.assert_not_called()

    def test_missing_server_and_remote_failure_are_safe(self):
        self.lookup.return_value=None
        self.assertEqual(self.client.get('/servers/7/bandwidth').status_code,404)
        self.reader.assert_not_called()
        self.lookup.return_value=self.info
        self.reader.side_effect=RuntimeError('private Application API key')
        result=self.client.get('/servers/7/bandwidth')
        self.assertEqual(result.status_code,503)
        self.assertNotIn('private',result.text)


class BandwidthPlanRouteTests(unittest.TestCase):
    def setUp(self):
        self.app=Flask(__name__);self.app.secret_key='test'
        self.product=plan(bandwidth_gb=1000,network_speed_mbps=100,bandwidth_overage_action='throttle',bandwidth_overage_mbps=5)
        self.target={'attributes':{'user':42,'limits':{'memory':1024},'allocation':7}}
        thread=Mock();thread.join.return_value.json.return_value=self.target
        self.ns=dict(servers=SimpleNamespace(route=lambda *a,**kw:lambda f:f),login_required=lambda f:f,
            serialized_plan_update=lambda f:f,
            ThreadWithReturnValue=Mock(return_value=thread),requests=Mock(),PTERODACTYL_URL='https://panel.example/',HEADERS={},
            webhook_log=Mock(),check_if_user_suspended=lambda _:False,get_ptero_id=lambda _:(42,),
            session=session,request=request,products=[self.product],plan_bandwidth=plan_bandwidth,
            resource_limits=resource_limits,update_plan=Mock(),remove_credits=Mock(return_value=None),
            add_credits=Mock(),flash=flash,redirect=redirect,url_for=lambda *a,**kw:'/servers/7')
        load_functions('Routes/Servers.py',self.ns,{'update_server_submit','_update_server_plan'})

    def submit(self):
        with self.app.test_request_context('/',method='POST',data={'plan':'1'}):
            session['email']='user@example.com'
            return self.ns['update_server_submit']('7')

    def test_selected_plan_applies_bandwidth_without_mutating_configuration(self):
        original=deepcopy(self.product)
        self.assertEqual(self.submit().status_code,302)
        args=self.ns['update_plan'].call_args.args
        self.assertEqual(args[2]['bandwidth_limit_gb'],1000)
        self.assertNotIn('bandwidth_gb',args[1])
        self.assertEqual(self.product,original)

    def test_invalid_configuration_is_rejected_before_debit(self):
        self.product['limits']['bandwidth_gb']=-1
        self.assertEqual(self.submit()[1],503)
        self.ns['remove_credits'].assert_not_called();self.ns['update_plan'].assert_not_called()

    def test_failed_update_returns_error_and_refunds_debit(self):
        self.ns['update_plan'].side_effect=requests.Timeout()
        self.assertEqual(self.submit()[1],503)
        self.ns['add_credits'].assert_called_once_with('user@example.com',50/30/24,False)

    def test_ownership_is_checked_before_plan_mutation(self):
        self.target['attributes']['user']=99
        self.submit()
        self.ns['remove_credits'].assert_not_called();self.ns['update_plan'].assert_not_called()


class PlanLockTests(unittest.TestCase):
    def test_competing_update_is_rejected_before_any_mutation_and_releases_connection(self):
        database=Mock();connection=Mock();cursor=Mock()
        database.get_connection.return_value=(connection,cursor)
        ns=load_functions('managers/bandwidth_manager.py',dict(wraps=wraps,DatabaseManager=database),{'serialized_plan_update'})
        view=Mock(return_value='updated')
        wrapped=ns['serialized_plan_update'](view)
        cursor.fetchone.return_value=(0,)
        self.assertEqual(wrapped('7')[1],409)
        view.assert_not_called();connection.close.assert_called_once();cursor.close.assert_called_once()
        cursor.execute.assert_called_once_with('SELECT GET_LOCK(%s, 0)',('server-plan:7',))

    def test_success_and_error_both_release_lock(self):
        database=Mock();connection=Mock();cursor=Mock()
        database.get_connection.return_value=(connection,cursor);cursor.fetchone.return_value=(1,)
        ns=load_functions('managers/bandwidth_manager.py',dict(wraps=wraps,DatabaseManager=database),{'serialized_plan_update'})
        view=Mock(return_value='updated');wrapped=ns['serialized_plan_update'](view)
        self.assertEqual(wrapped('7'),'updated')
        cursor.execute.assert_called_with('SELECT RELEASE_LOCK(%s)',('server-plan:7',))
        view.side_effect=RuntimeError('failed')
        with self.assertRaises(RuntimeError):wrapped('7')
        self.assertEqual(connection.close.call_count,2)
        cursor.execute.assert_called_with('SELECT RELEASE_LOCK(%s)',('server-plan:7',))


if __name__=='__main__':unittest.main()
