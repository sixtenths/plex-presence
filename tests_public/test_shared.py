import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import Mock, patch
from plex_presence.notifications import NotificationWatcher, connect_notifications
from plex_presence.plex_account import PlexAccount
from plex_presence.service import Service


CONFIG = {"plex_url":"http://192.0.2.1:32400", "plex_token":"PRIVATE_SHARED", "plex_username":"viewer",
          "plex_account_user_id":"42", "plex_source":"notifications"}


class SharedPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.watcher = NotificationWatcher(CONFIG)
        self.watcher.thread = Mock()  # Tests deliver notifications without real sockets.
        self.watcher.connected = True
        self.watcher.metadata["123"] = {"type":"movie", "title":"Example", "duration":600000, "ratingKey":"123"}
        self.addCleanup(self.watcher.close)

    def event(self, state="playing", session="1", **extra):
        self.watcher.accept({"NotificationContainer":{"type":"playing", "PlaySessionStateNotification":[
            dict(state=state, sessionKey=session, ratingKey="123", viewOffset=1000, **extra)]}})

    def test_own_feed_works_without_admin_session_endpoint(self):
        self.event()
        self.assertEqual(self.watcher.poll()["details"], "Example")
        self.assertEqual(self.watcher.status, "playing")
        self.event("paused")
        self.assertIsNone(self.watcher.poll())
        self.event()
        self.assertIsNotNone(self.watcher.poll())
        self.event("stopped")
        self.assertIsNone(self.watcher.poll())

    def test_foreign_explicit_account_is_ignored(self):
        self.event(userID="99")
        self.assertIsNone(self.watcher.poll())
        self.event(userID="42")
        self.assertIsNotNone(self.watcher.poll())

    def test_stop_of_other_session_does_not_clear_active_playback(self):
        self.event(session="1")
        self.event("stopped", session="2")
        self.assertIsNotNone(self.watcher.poll())

    def test_disconnect_and_stale_feed_clear(self):
        self.event()
        self.watcher.connected = False
        self.assertIsNone(self.watcher.poll())
        self.watcher.connected = True
        with patch('plex_presence.notifications.time.monotonic', return_value=10):
            self.event()
        with patch('plex_presence.notifications.time.monotonic', return_value=100):
            self.assertIsNone(self.watcher.poll())

    def test_end_and_metadata_race_do_not_republish(self):
        self.event()
        self.watcher.metadata.clear()
        def metadata(*args, **kwargs):
            self.event("stopped")
            return {"MediaContainer":{"Metadata":[{"type":"movie", "title":"Old", "duration":600000}]}}
        with patch('plex_presence.notifications.request_json', side_effect=metadata):
            self.assertIsNone(self.watcher.poll())
        self.watcher.metadata["123"] = {"type":"movie", "title":"Ended", "duration":1001}
        self.event()
        self.assertIsNone(self.watcher.poll())

    def test_headers_no_token_in_url_and_redirects_rejected(self):
        socket = Mock()
        socket.getstatus.return_value = 101
        with patch('plex_presence.notifications.websocket.create_connection', return_value=socket) as connect:
            self.assertIs(connect_notifications(CONFIG), socket)
        self.assertNotIn('PRIVATE_SHARED', connect.call_args.args[0])
        self.assertEqual(connect.call_args.kwargs['header']['X-Plex-Token'], 'PRIVATE_SHARED')
        self.assertEqual(connect.call_args.kwargs['redirect_limit'], 0)
        socket.getstatus.return_value = 302
        with patch('plex_presence.notifications.websocket.create_connection', return_value=socket), self.assertRaises(OSError):
            connect_notifications(CONFIG)


class SharedSetupTests(unittest.TestCase):
    def test_shared_resource_does_not_require_sessions_permission(self):
        account = PlexAccount('installation')
        resource = {'owned':False, 'clientIdentifier':'server', 'accessToken':'PERSONAL_SHARE',
                    'connections':[{'uri':'http://192.0.2.1:32400'}]}
        with patch('plex_presence.plex_account.request_json', side_effect=[{'MediaContainer':{'machineIdentifier':'server'}}, {}]) as request, patch('plex_presence.plex_account.connect_notifications') as connect:
            result = account.choose(resource)
        self.assertEqual(result['plex_source'], 'notifications')
        self.assertEqual(result['plex_token'], 'PERSONAL_SHARE')
        self.assertTrue(all('/status/sessions' not in call.args[0] for call in request.call_args_list))
        connect.return_value.close.assert_called_once()

    def test_home_listing_and_pin_use_are_sanitized(self):
        account = PlexAccount('installation')
        root = ET.fromstring('<MediaContainer><User id="42" title="My profile" protected="1" authenticationToken="PRIVATE"/></MediaContainer>')
        with patch.object(account, '_home_request', return_value=root):
            self.assertEqual(account.home_users('ROOT'), [{'id':'42','name':'My profile','protected':True}])
        with patch.object(account, '_home_request', return_value=ET.fromstring('<user authenticationToken="PROFILE_TOKEN"/>')) as request, patch('plex_presence.plex_account.request_json', return_value={'id':42,'title':'My profile'}):
            value = account.switch_home('ROOT','42','1234')
        self.assertEqual(value['token'], 'PROFILE_TOKEN')
        self.assertEqual(request.call_args.args, ('ROOT','/42/switch','1234'))

    def test_switch_replaces_server_access_and_never_saves_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Service(directory)
            service.config.update(plex_root_token='ROOT', plex_account_token='OLD', plex_url=CONFIG['plex_url'], plex_token='OLD_SERVER', plex_username='Old profile')
            service.home_profiles = {'42': {'id':'42','protected':True}}
            with patch.object(service.account, 'switch_home', return_value={'id':'42','token':'NEW_PROFILE','username':'My profile'}):
                service.action('/api/plex/profile', {'profile_id':'42','pin':'1234'})
            self.assertEqual(service.config['plex_account_token'], 'NEW_PROFILE')
            self.assertNotIn('plex_token', service.config)
            self.assertNotIn('1234', service.config_path.read_text())
            public = json.dumps(service.status())
            for secret in ('ROOT','NEW_PROFILE','OLD_SERVER'):
                self.assertNotIn(secret, public)
            self.assertEqual(service.resources, {})

    def test_unknown_profile_cannot_switch_and_shared_name_is_locked(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Service(directory)
            with patch.object(service.account, 'switch_home') as switch, self.assertRaises(ValueError):
                service.action('/api/plex/profile', {'profile_id':'999','pin':'1234'})
            switch.assert_not_called()
            service.config.update(CONFIG)
            with self.assertRaises(ValueError):
                service.action('/api/settings', {'plex_username':'someone else'})
