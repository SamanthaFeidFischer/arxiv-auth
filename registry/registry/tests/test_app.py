"""End-to-end test for :mod:`registry`."""

import os
import subprocess
import time
import json
from datetime import datetime, timedelta
from hashlib import sha256
from unittest import TestCase
from urllib.parse import urlencode, urlparse, parse_qs, unquote

from arxiv import status
from arxiv.users.helpers import generate_token

from registry.factory import create_web_app
from registry.services import datastore
from registry.domain import Client, ClientGrantType, ClientCredential, \
    ClientAuthorization, Scope


def stop_container(container):
    subprocess.run(f"docker rm -f {container}",
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                   shell=True)
    from registry.services import datastore
    datastore.drop_all()


class TestClientAuthentication(TestCase):
    __test__ = int(bool(os.environ.get('WITH_INTEGRATION', False)))

    def setUp(self):
        """Spin up redis."""
        # self.redis = subprocess.run(
        #     "docker run -d -p 7000:7000 redis",
        #     stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True
        # )
        # time.sleep(2)    # In case it takes a moment to start.
        # if self.redis.returncode > 0:
        #     raise RuntimeError('Could not start redis. Is Docker running?')
        #
        # self.container = self.redis.stdout.decode('ascii').strip()
        self.db = 'db.sqlite'

        self.client = Client(
            owner_id='252',
            name='fooclient',
            url='http://asdf.com',
            description='a client',
            redirect_uri='https://foo.com/bar'
        )
        self.secret = 'foohashedsecret'
        self.hashed_secret = sha256(self.secret.encode('utf-8')).hexdigest()
        self.cred = ClientCredential(client_secret=self.hashed_secret)
        self.auths = [
            ClientAuthorization(
                scope='foo:bar',
                requested=datetime.now() - timedelta(seconds=30),
                authorized=datetime.now()
            ),
            ClientAuthorization(
                scope='baz:bat',
                requested=datetime.now() - timedelta(seconds=30),
                authorized=datetime.now()
            )
        ]
        self.grant_types = [
            ClientGrantType(
                grant_type='client_credentials',
                requested=datetime.now() - timedelta(seconds=30),
                authorized=datetime.now()
            )
        ]
        try:
            os.environ['AUTHLIB_INSECURE_TRANSPORT'] = 'true'
            self.app = create_web_app()
            self.app.config['REGISTRY_DATABASE_URI'] = f'sqlite:///{self.db}'

            self.test_client = self.app.test_client()
            with self.app.app_context():
                datastore.create_all()
                self.client_id = datastore.save_client(
                    self.client,
                    self.cred,
                    auths=self.auths,
                    grant_types=self.grant_types
                )

        except Exception as e:
            # stop_container(self.container)
            raise

    def tearDown(self):
        """Tear down redis."""
        # stop_container(self.container)
        os.remove(self.db)

    def test_post_credentials(self):
        """POST request to /token returns auth token."""
        payload = {
            'client_id': self.client_id,
            'client_secret': self.secret,
            'grant_type': 'client_credentials'
        }
        response = self.test_client.post('/token', data=payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.content_type, 'application/json')
        data = json.loads(response.data)
        self.assertIn('access_token', data)
        self.assertIn('expires_in', data)
        self.assertIn('token_type', data)

    def test_post_invalid_credentials(self):
        """POST request with bad creds returns 400 Bad Request."""
        payload = {
            'client_id': self.client_id,
            'client_secret': 'not the secret',
            'grant_type': 'client_credentials'
        }
        response = self.test_client.post('/token', data=payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.content_type, 'application/json')
        self.assertEqual(response.data, b'{"error": "invalid_client"}')

    def test_post_invalid_grant_type(self):
        """POST request with bad grant type returns 400 Bad Request."""
        payload = {
            'client_id': self.client_id,
            'client_secret': self.secret,
            'grant_type': 'implicit'
        }
        response = self.test_client.post('/token', data=payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.content_type, 'application/json')
        self.assertEqual(response.data, b'{"error": "invalid_grant"}')

    def test_post_invalid_scope(self):
        """POST request with unauthorized scope returns 400 Bad Request."""
        payload = {
            'client_id': self.client_id,
            'client_secret': self.secret,
            'grant_type': 'client_credentials',
            'scope': 'not:authorized delete:everything'
        }
        response = self.test_client.post('/token', data=payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.content_type, 'application/json')
        data = json.loads(response.data)
        self.assertEqual(data['error'], "invalid_scope")


class TestAuthorizationCode(TestCase):
    __test__ = int(bool(os.environ.get('WITH_INTEGRATION', False)))

    def setUp(self):
        """Spin up redis."""
        # self.redis = subprocess.run(
        #     "docker run -d -p 7000:7000 redis",
        #     stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True
        # )
        # time.sleep(2)    # In case it takes a moment to start.
        # if self.redis.returncode > 0:
        #     raise RuntimeError('Could not start redis. Is Docker running?')
        #
        # self.container = self.redis.stdout.decode('ascii').strip()
        self.db = 'db.sqlite'

        self.client = Client(
            owner_id='252',
            name='fooclient',
            url='http://asdf.com',
            description='a client',
            redirect_uri='https://foo.com/bar'
        )
        self.secret = 'foohashedsecret'
        self.hashed_secret = sha256(self.secret.encode('utf-8')).hexdigest()
        self.cred = ClientCredential(client_secret=self.hashed_secret)
        self.auths = [
            ClientAuthorization(
                scope='something:read',
                requested=datetime.now() - timedelta(seconds=30),
                authorized=datetime.now()
            ),
            ClientAuthorization(
                scope='baz:bat',
                requested=datetime.now() - timedelta(seconds=30),
                authorized=datetime.now()
            )
        ]
        self.grant_types = [
            ClientGrantType(
                grant_type='authorization_code',
                requested=datetime.now() - timedelta(seconds=30),
                authorized=datetime.now()
            )
        ]
        try:
            os.environ['AUTHLIB_INSECURE_TRANSPORT'] = 'true'
            self.app = create_web_app()
            self.app.config['REGISTRY_DATABASE_URI'] = f'sqlite:///{self.db}'
            self.app.config['JWT_SECRET'] = 'foosecret'
            self.app.config['SERVER_NAME'] = 'localhost:5000'

            self.test_client = self.app.test_client()
            self.user_agent = self.app.test_client()
            with self.app.app_context():
                datastore.create_all()
                self.client_id = datastore.save_client(
                    self.client,
                    self.cred,
                    auths=self.auths,
                    grant_types=self.grant_types
                )

        except Exception as e:
            # stop_container(self.container)
            raise

    def tearDown(self):
        """Tear down redis."""
        # stop_container(self.container)
        os.remove(self.db)

    def test_auth_code_workflow(self):
        """Test authorization code workflow."""
        user_token = generate_token('1234', 'foo@bar.com', 'foouser',
                                    scope=[Scope('something', 'read'),
                                           Scope('baz', 'bat')])
        user_headers = {'Authorization': user_token}
        params = {
            'response_type': 'code',
            'client_id': self.client_id,
            'redirect_uri': self.client.redirect_uri,
            'scope': 'something:read baz:bat'
        }
        response = self.user_agent.get('/authorize?%s' % urlencode(params),
                                       headers=user_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK,
                         'User can access authorization page')

        params['confirm'] = 'ok'    # Embedded in confirmation page.
        response = self.user_agent.post('/authorize', data=params,
                                        headers=user_headers)

        self.assertEqual(response.status_code, status.HTTP_302_FOUND,
                         'User is redirected to client redirect URI')
        target = urlparse(response.headers.get('Location'))
        code = parse_qs(target.query).get('code')
        self.assertEqual(target.netloc,
                         urlparse(self.client.redirect_uri).netloc,
                         'User is redirected to client redirect URI')
        self.assertEqual(target.path,
                         urlparse(self.client.redirect_uri).path,
                         'User is redirected to client redirect URI')
        self.assertIsNotNone(code,
                             'Authorization code is passed in redirect URL')

        payload = {
            'client_id': self.client_id,
            'client_secret': self.secret,
            'code': code,
            'grant_type': 'authorization_code',
            'redirect_uri': self.client.redirect_uri
        }
        response = self.test_client.post('/token', data=payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.content_type, 'application/json')
        data = json.loads(response.data)

        self.assertIn('access_token', data, 'Response contains access token')
        self.assertIn('expires_in', data, 'Response contains expiration')
        self.assertGreater(data['expires_in'], 0)
        self.assertEqual(data['scope'], 'something:read baz:bat',
                         'Requested code in granted')
        self.assertEqual(data['token_type'], 'Bearer',
                         'Access token is a bearer token')

    def test_user_is_not_logged_in(self):
        """User is directed to an auth page and is not logged in."""
        params = {
            'response_type': 'code',
            'client_id': '5678',   # Invalid client ID.
            'redirect_uri': self.client.redirect_uri,
            'scope': 'something:read'
        }
        response = self.user_agent.get('/authorize?%s' % urlencode(params))
        self.assertEqual(response.status_code, status.HTTP_302_FOUND,
                         'User is redirected')
        target = urlparse(response.headers['Location'])
        self.assertEqual(target.scheme, 'https')
        self.assertEqual(target.netloc, 'arxiv.org')
        self.assertEqual(target.path, '/login')
        next_page = urlparse(unquote(parse_qs(target.query)['next_page'][0]))
        self.assertEqual(next_page.netloc, self.app.config['SERVER_NAME'])
        self.assertEqual(next_page.path, '/authorize')
        # http://localhost:5000/authorize?response_type=code&client_id=5678&redirect_uri=https://foo.com/bar&scope=something:read

    def test_auth_confirmation_has_invalid_client(self):
        """User is directed to an auth page with an invalid client ID."""
        user_token = generate_token('1234', 'foo@bar.com', 'foouser',
                                    scope=[Scope('something', 'read')])
        user_headers = {'Authorization': user_token}
        params = {
            'response_type': 'code',
            'client_id': '5678',   # Invalid client ID.
            'redirect_uri': self.client.redirect_uri,
            'scope': 'something:read'
        }
        response = self.user_agent.get('/authorize?%s' % urlencode(params),
                                       headers=user_headers)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST,
                         'A 400 Bad Request is returned')

    def test_auth_confirmation_has_unauthorized_scope(self):
        """User is directed with scope for which client is unauthorized."""
        user_token = generate_token('1234', 'foo@bar.com', 'foouser',
                                    scope=[Scope('something', 'read')])
        user_headers = {'Authorization': user_token}
        params = {
            'response_type': 'code',
            'client_id': self.client_id,
            'redirect_uri': self.client.redirect_uri,
            'scope': 'somethingelse:delete'
        }
        response = self.user_agent.get('/authorize?%s' % urlencode(params),
                                       headers=user_headers)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST,
                         'A 400 Bad Request is returned')

    def test_auth_confirmation_post_missing_confirmation(self):
        """User agent issues POST request without confirmation."""
        user_token = generate_token('1234', 'foo@bar.com', 'foouser',
                                    scope=[Scope('something', 'read')])
        user_headers = {'Authorization': user_token}
        # Missing `confirm` field.
        params = {
            'response_type': 'code',
            'client_id': self.client_id,
            'redirect_uri': self.client.redirect_uri,
            'scope': 'something:read'
        }
        response = self.user_agent.post('/authorize', data=params,
                                        headers=user_headers)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST,
                         'A 400 Bad Request is returned')

    def test_auth_invalid_code(self):
        """Client attempts to exchange an invalid code."""
        payload = {
            'client_id': self.client_id,
            'client_secret': self.secret,
            'code': 'notavalidcode',
            'grant_type': 'authorization_code',
            'redirect_uri': self.client.redirect_uri
        }
        response = self.test_client.post('/token', data=payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST,
                         'A 400 Bad Request is returned')
