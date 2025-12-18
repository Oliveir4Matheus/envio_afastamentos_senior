"""
Módulo de Autenticação Senior - Arquivo Único Portável

Este módulo consolida toda a funcionalidade de autenticação da plataforma Senior
e obtenção de tokens para Gestão de Ponto.

Uso Básico:
    from senior_auth import authenticate_complete, get_credentials

    # Credenciais do .env ou prompt interativo
    username, password = get_credentials()

    # Autenticação completa
    result = authenticate_complete(username, password)

    if result['success']:
        print(f"Token Gestaoponto: {result['gestaoponto_token']}")

Dependências:
    - requests
    - python-dotenv

Para copiar para outros projetos:
    1. Copie este arquivo (senior_auth.py)
    2. Instale: pip install requests python-dotenv
    3. Configure .env (opcional) ou use prompt interativo
"""

import os
import json
import logging
import urllib.parse
import getpass
from datetime import datetime, timedelta
from typing import Dict, Optional, Any, List
from dataclasses import dataclass

import requests
from dotenv import load_dotenv

# Carregar variáveis de ambiente
load_dotenv()


# ============================================================================
# SISTEMA DE CREDENCIAIS
# ============================================================================

def get_credentials(prompt_if_missing: bool = True) -> tuple:
    """
    Obtém credenciais do .env ou solicita via prompt interativo

    Args:
        prompt_if_missing (bool): Se True, solicita credenciais caso não estejam no .env

    Returns:
        tuple: (username, password)

    Raises:
        ValueError: Se credenciais não encontradas e prompt_if_missing=False
    """
    username = os.getenv('SENIOR_USERNAME')
    password = os.getenv('SENIOR_PASSWORD')

    if not username or not password:
        if not prompt_if_missing:
            raise ValueError(
                "Credenciais não encontradas no .env. "
                "Configure SENIOR_USERNAME e SENIOR_PASSWORD no arquivo .env"
            )

        print("=" * 60)
        print("  Credenciais não encontradas no .env")
        print("  Por favor, insira suas credenciais Senior")
        print("=" * 60)

        if not username:
            username = input("Username (email): ").strip()

        if not password:
            password = getpass.getpass("Password (não será exibida): ").strip()

        print()

        if not username or not password:
            raise ValueError("Username e password são obrigatórios")

    return username, password


# ============================================================================
# EXCEÇÕES
# ============================================================================

class SeniorAuthError(Exception):
    """Exceção base para erros de autenticação Senior"""
    def __init__(self, message: str, status_code: int = None):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


class SeniorLoginError(SeniorAuthError):
    """Exceção para erros específicos de login"""
    pass


class SeniorNetworkError(SeniorAuthError):
    """Exceção para erros de rede/conexão"""
    pass


class SeniorTokenNotFoundError(SeniorAuthError):
    """Exceção quando o token não é encontrado na resposta"""
    def __init__(self, message: str = "Token com.senior.token não encontrado na resposta"):
        super().__init__(message)


# ============================================================================
# MODELOS DE DADOS
# ============================================================================

@dataclass
class AuthenticationResult:
    """
    Resultado de uma tentativa de autenticação na plataforma Senior

    Attributes:
        success (bool): Indica se a autenticação foi bem-sucedida
        senior_token (str, optional): Token principal da Senior (com.senior.token)
        cookies (Dict[str, str]): Todos os cookies retornados pela resposta
        status_code (int, optional): Código de status HTTP da resposta
        redirect_location (str, optional): URL de redirecionamento
        error_message (str, optional): Mensagem de erro, se houver
        response_headers (Dict[str, str], optional): Headers da resposta HTTP
    """
    success: bool
    cookies: Dict[str, str]
    senior_token: Optional[str] = None
    status_code: Optional[int] = None
    redirect_location: Optional[str] = None
    error_message: Optional[str] = None
    response_headers: Optional[Dict[str, str]] = None

    def __post_init__(self):
        """Pós-processamento após inicialização"""
        if self.success and not self.senior_token and 'com.senior.token' in self.cookies:
            self.senior_token = self.cookies['com.senior.token']

    @property
    def has_senior_token(self) -> bool:
        """Verifica se possui o token principal da Senior"""
        return self.senior_token is not None and len(self.senior_token) > 0

    @property
    def session_cookies(self) -> Dict[str, str]:
        """Retorna apenas os cookies de sessão importantes"""
        important_cookies = [
            'com.senior.token',
            'JSESSIONID',
            'com.senior.idp.state',
            'TS018608fa'
        ]
        return {k: v for k, v in self.cookies.items() if k in important_cookies}

    def get_cookie(self, name: str) -> Optional[str]:
        """Obtém um cookie específico pelo nome"""
        return self.cookies.get(name)

    def get_decoded_token(self) -> Optional[Dict]:
        """Retorna o token decodificado como dicionário"""
        if not self.has_senior_token:
            return None
        return SeniorTokenDecoder.decode_token(self.senior_token)

    def get_token_info(self) -> Dict:
        """Retorna informações estruturadas do token"""
        decoded = self.get_decoded_token()
        if not decoded:
            return {}
        return SeniorTokenDecoder.get_token_info(decoded)

    def __str__(self) -> str:
        """Representação string do resultado"""
        status = "SUCCESS" if self.success else "FAILED"
        token_status = "✓" if self.has_senior_token else "✗"
        return f"AuthenticationResult({status}, Token: {token_status}, Cookies: {len(self.cookies)})"


# ============================================================================
# CLIENTE HTTP
# ============================================================================

class RequestsHttpClient:
    """Cliente HTTP usando requests com retry automático"""

    def __init__(self, timeout: int = 30, verify_ssl: bool = True, max_retries: int = 3):
        """
        Inicializa cliente HTTP

        Args:
            timeout (int): Timeout para requisições
            verify_ssl (bool): Verificar certificados SSL
            max_retries (int): Número máximo de tentativas
        """
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.max_retries = max_retries
        self.session = self._create_session()
        self.logger = logging.getLogger(__name__)

    def _create_session(self) -> requests.Session:
        """Cria e configura sessão HTTP com retry"""
        session = requests.Session()

        if self.max_retries > 0:
            retry_strategy = requests.adapters.Retry(
                total=self.max_retries,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
            )

            adapter = requests.adapters.HTTPAdapter(max_retries=retry_strategy)
            session.mount("http://", adapter)
            session.mount("https://", adapter)

        return session

    def post(self, url: str, headers: Dict[str, str], data: Any = None, **kwargs) -> requests.Response:
        """Realiza requisição POST"""
        try:
            request_kwargs = {
                'headers': headers,
                'timeout': kwargs.get('timeout', self.timeout),
                'verify': kwargs.get('verify', self.verify_ssl),
                'allow_redirects': kwargs.get('allow_redirects', False)
            }

            if 'json' in kwargs:
                request_kwargs['json'] = kwargs['json']
            elif data is not None:
                request_kwargs['data'] = data

            for key, value in kwargs.items():
                if key not in ['timeout', 'verify', 'allow_redirects', 'json']:
                    request_kwargs[key] = value

            self.logger.debug(f"POST {url}")
            response = self.session.post(url, **request_kwargs)

            return response

        except requests.RequestException as e:
            error_msg = f"Erro de rede em POST {url}: {str(e)}"
            self.logger.error(error_msg)
            raise SeniorNetworkError(error_msg)

    def get(self, url: str, headers: Dict[str, str], **kwargs) -> requests.Response:
        """Realiza requisição GET"""
        try:
            request_kwargs = {
                'headers': headers,
                'timeout': kwargs.get('timeout', self.timeout),
                'verify': kwargs.get('verify', self.verify_ssl),
                'allow_redirects': kwargs.get('allow_redirects', True)
            }

            for key, value in kwargs.items():
                if key not in ['timeout', 'verify', 'allow_redirects']:
                    request_kwargs[key] = value

            self.logger.debug(f"GET {url}")
            response = self.session.get(url, **request_kwargs)

            return response

        except requests.RequestException as e:
            error_msg = f"Erro de rede em GET {url}: {str(e)}"
            self.logger.error(error_msg)
            raise SeniorNetworkError(error_msg)

    def close(self):
        """Fecha a sessão HTTP"""
        if self.session:
            self.session.close()

    def __enter__(self):
        """Suporte para context manager"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Cleanup automático"""
        self.close()


# ============================================================================
# DECODIFICADOR DE TOKEN
# ============================================================================

class SeniorTokenDecoder:
    """Classe para decodificação e análise de tokens Senior"""

    @staticmethod
    def decode_token(encoded_token: str) -> Optional[Dict]:
        """
        Decodifica um token Senior URL-encoded

        Args:
            encoded_token (str): Token em formato URL-encoded

        Returns:
            Dict or None: Token decodificado como dicionário ou None se erro
        """
        try:
            decoded_token = urllib.parse.unquote(encoded_token)
            token_data = json.loads(decoded_token)
            return token_data
        except (json.JSONDecodeError, Exception):
            return None

    @staticmethod
    def get_token_info(token_data: Dict) -> Dict:
        """
        Extrai informações estruturadas do token

        Args:
            token_data (Dict): Token decodificado

        Returns:
            Dict: Informações estruturadas do token
        """
        if not token_data:
            return {}

        expires_in = token_data.get('expires_in', 0)
        expiration_date = datetime.now() + timedelta(seconds=expires_in)

        return {
            'version': token_data.get('version'),
            'token_type': token_data.get('token_type'),
            'scope': token_data.get('scope'),
            'auth_type': token_data.get('type'),
            'expires_in_seconds': expires_in,
            'expires_in_hours': expires_in // 3600,
            'expiration_date': expiration_date.strftime('%Y-%m-%d %H:%M:%S'),
            'user_info': {
                'username': token_data.get('username'),
                'email': token_data.get('email'),
                'full_name': token_data.get('fullName', '').replace('+', ' '),
                'tenant_name': token_data.get('tenantName'),
                'locale': token_data.get('locale')
            },
            'tokens': {
                'access_token': token_data.get('access_token'),
                'refresh_token': token_data.get('refresh_token')
            },
            'device_id': SeniorTokenDecoder._extract_device_id(token_data.get('scope', ''))
        }

    @staticmethod
    def _extract_device_id(scope: str) -> Optional[str]:
        """Extrai o device ID do scope"""
        if 'device_' in scope:
            parts = scope.split('device_')
            if len(parts) > 1:
                return parts[1]
        return None

    @staticmethod
    def is_token_valid(token_data: Dict) -> bool:
        """Verifica se o token ainda é válido"""
        if not token_data:
            return False
        required_fields = ['access_token', 'expires_in', 'username']
        return all(field in token_data for field in required_fields)


# ============================================================================
# AUTENTICADOR PLATAFORMA SENIOR
# ============================================================================

class SeniorPlatformAuthenticator:
    """Autenticador para plataforma Senior"""

    # URLs configuráveis via variáveis de ambiente
    BASE_URL = os.getenv('SENIOR_BASE_URL', 'https://platform.senior.com.br')
    LOGIN_URL = f"{BASE_URL}/auth/LoginServlet"
    DEFAULT_REDIRECT_URL = (
        f"{BASE_URL}/senior-x/#/Gest%C3%A3o%20de%20Pessoas%20%7C%20HCM/1/"
        "res:%2F%2Fsenior.com.br%2Fmenu%2Frh%2Fponto%2Fgestaoponto%2Fgestor?"
        "category=frame&link=https:%2F%2Fwebp20.seniorcloud.com.br:31531%2F"
        "gestaoponto-frontend%2Fissues%2Fredirect%3Factiveview%3Dmanager%26portal%3Dg7"
        "&withCredentials=true&helpUrl=http:%2F%2Fdocumentacao.senior.com.br%2F"
        "gestao-de-pessoas-hcm%2F6.10.4%2F%23gestao-ponto%2Fnova-interface%2F"
        "apuracao-do-ponto%2Fgestor-rh%2Facertos-da-minha-equipe.htm&r=1"
    )

    def __init__(self, http_client: Optional[RequestsHttpClient] = None):
        """Inicializa autenticador Senior"""
        self.http_client = http_client or RequestsHttpClient()
        self.logger = logging.getLogger(__name__)

    def authenticate(self, username: str, password: str, **kwargs) -> AuthenticationResult:
        """
        Realiza autenticação na plataforma Senior

        Args:
            username (str): Email do usuário
            password (str): Senha do usuário
            **kwargs: redirect_to (opcional)

        Returns:
            AuthenticationResult: Resultado da autenticação
        """
        if not username or not password:
            raise SeniorLoginError("Username e password são obrigatórios")

        redirect_to = kwargs.get('redirect_to', self.DEFAULT_REDIRECT_URL)

        try:
            headers = self._build_headers(redirect_to)
            form_data = self._build_form_data(username, password, redirect_to)

            self.logger.info(f"Autenticando usuário: {username}")

            response = self.http_client.post(
                self.LOGIN_URL,
                headers=headers,
                data=form_data,
                allow_redirects=False
            )

            result = self._process_response(response)

            self.logger.info(f"Autenticação {'bem-sucedida' if result.success else 'falhou'}")
            return result

        except Exception as e:
            if not isinstance(e, SeniorAuthError):
                error_msg = f"Erro inesperado durante autenticação: {str(e)}"
                self.logger.error(error_msg)
                raise SeniorAuthError(error_msg)
            raise

    def _build_headers(self, redirect_to: str) -> Dict[str, str]:
        """Constrói headers para requisição"""
        return {
            'Host': 'platform.senior.com.br',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'pt-BR,pt;q=0.8,en-US;q=0.5,en;q=0.3',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': self.BASE_URL,
            'Connection': 'keep-alive',
            'Referer': f"{self.BASE_URL}/login/?redirectTo={redirect_to}",
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-User': '?1'
        }

    def _build_form_data(self, username: str, password: str, redirect_to: str) -> Dict[str, str]:
        """Constrói dados do formulário"""
        return {
            'redirectTo': redirect_to,
            'lng': '',
            'emailSuffix': '',
            'expirationRememberMe': '',
            'scope': '',
            'user': username,
            'password': password,
            'g-recaptcha-response': ''
        }

    def _process_response(self, response) -> AuthenticationResult:
        """Processa resposta da autenticação"""
        cookies = self._extract_cookies(response)
        success = self._is_login_successful(response, cookies)

        return AuthenticationResult(
            success=success,
            cookies=cookies,
            senior_token=cookies.get('com.senior.token'),
            status_code=response.status_code,
            redirect_location=response.headers.get('Location'),
            response_headers=dict(response.headers),
            error_message=None if success else self._extract_error_message(response)
        )

    def _extract_cookies(self, response) -> Dict[str, str]:
        """Extrai cookies da resposta"""
        cookies = {}

        for cookie in response.cookies:
            cookies[cookie.name] = cookie.value

        set_cookie_headers = response.headers.get('Set-Cookie', '').split(',')
        for header in set_cookie_headers:
            if '=' in header:
                cookie_part = header.split(';')[0].strip()
                if '=' in cookie_part:
                    name, value = cookie_part.split('=', 1)
                    cookies[name.strip()] = value.strip()

        return cookies

    def _is_login_successful(self, response, cookies: Dict[str, str]) -> bool:
        """Verifica sucesso do login"""
        if 'com.senior.token' in cookies and cookies['com.senior.token']:
            return True

        redirect_codes = [301, 302, 303, 307, 308]
        if (response.status_code in redirect_codes and
            'Location' in response.headers and
            not self._has_error_indicators(response)):
            return True

        return False

    def _has_error_indicators(self, response) -> bool:
        """Verifica indicadores de erro"""
        if not response.text:
            return False

        error_indicators = [
            'erro', 'error', 'inválid', 'incorrect', 'failed',
            'senha incorreta', 'usuário não encontrado', 'login failed'
        ]

        response_text = response.text.lower()
        return any(indicator in response_text for indicator in error_indicators)

    def _extract_error_message(self, response) -> str:
        """Extrai mensagem de erro"""
        if response.status_code == 401:
            return "Credenciais inválidas"
        elif response.status_code == 403:
            return "Acesso negado"
        elif response.status_code >= 500:
            return "Erro interno do servidor Senior"
        elif self._has_error_indicators(response):
            return "Falha na autenticação - verifique suas credenciais"

        return f"Erro HTTP {response.status_code}"

    def close(self):
        """Fecha recursos"""
        if self.http_client:
            self.http_client.close()


# ============================================================================
# PROVEDOR DE TOKEN GESTAOPONTO
# ============================================================================

class GestaopontoTokenProvider:
    """Provedor de token para API de Gestão de Ponto"""

    # URLs configuráveis
    BASE_URL = os.getenv('GESTAOPONTO_BASE_URL', 'https://webp20.seniorcloud.com.br:31531')
    AUTH_ENDPOINT = f"{BASE_URL}/gestaoponto-backend/api/senior/auth/g7"

    def __init__(self, http_client: Optional[RequestsHttpClient] = None):
        """Inicializa provedor de token"""
        self.http_client = http_client or RequestsHttpClient()
        self.logger = logging.getLogger(__name__)

    def get_token(self, auth_result: AuthenticationResult, **kwargs) -> Optional[str]:
        """
        Obtém token da API de Gestão de Ponto

        Args:
            auth_result (AuthenticationResult): Resultado da autenticação Senior
            **kwargs: Parâmetros adicionais

        Returns:
            str or None: Token obtido ou None se erro
        """
        if not auth_result.success or not auth_result.has_senior_token:
            raise SeniorAuthError("Resultado de autenticação inválido")

        token_info = auth_result.get_token_info()
        if not token_info or 'tokens' not in token_info:
            raise SeniorAuthError("Não foi possível extrair access token")

        access_token = token_info['tokens']['access_token']
        if not access_token:
            raise SeniorAuthError("Access token não encontrado")

        try:
            headers = self._build_headers(access_token)

            self.logger.info("Obtendo token da API de Gestão de Ponto...")

            response = self.http_client.post(
                self.AUTH_ENDPOINT,
                headers=headers,
                json={}
            )

            return self._extract_token_from_response(response)

        except Exception as e:
            if not isinstance(e, SeniorAuthError):
                error_msg = f"Erro ao obter token de Gestão de Ponto: {str(e)}"
                self.logger.error(error_msg)
                raise SeniorAuthError(error_msg)
            raise

    def _build_headers(self, access_token: str) -> Dict[str, str]:
        """Constrói headers para requisição"""
        return {
            'Host': 'webp20.seniorcloud.com.br:31531',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'pt-BR,pt;q=0.8,en-US;q=0.5,en;q=0.3',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Content-Type': 'application/json',
            'token': access_token,
            'expires': '604800',
            'Origin': 'https://webp20.seniorcloud.com.br:31531',
            'Connection': 'keep-alive',
            'Referer': 'https://webp20.seniorcloud.com.br:31531/gestaoponto-frontend/login-portal',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin'
        }

    def _extract_token_from_response(self, response) -> Optional[str]:
        """Extrai token da resposta"""
        self.logger.info(f"Status da resposta: {response.status_code}")

        if response.status_code != 200:
            self.logger.error(f"Erro na API: {response.status_code}")
            return None

        try:
            response_data = response.json()

            if isinstance(response_data, str):
                return response_data

            if isinstance(response_data, dict):
                token_fields = ['token', 'access_token', 'authToken', 'jwt', 'bearer']

                for field in token_fields:
                    if field in response_data and response_data[field]:
                        return response_data[field]

                for key, value in response_data.items():
                    if isinstance(value, str) and len(value) > 10:
                        self.logger.info(f"Token encontrado no campo '{key}'")
                        return value

            return None

        except json.JSONDecodeError:
            if response.text and len(response.text.strip()) > 10:
                return response.text.strip()

            return None

    def close(self):
        """Fecha recursos"""
        if self.http_client:
            self.http_client.close()


# ============================================================================
# FACADE PRINCIPAL - SENIOR AUTH
# ============================================================================

class SeniorAuth:
    """
    Classe principal para autenticação Senior - Facade Pattern

    Orquestra todo o processo de autenticação seguindo princípios SOLID
    """

    def __init__(self,
                 timeout: int = 30,
                 verify_ssl: bool = True,
                 max_retries: int = 3):
        """
        Inicializa o sistema de autenticação Senior

        Args:
            timeout (int): Timeout para requisições HTTP
            verify_ssl (bool): Verificar certificados SSL
            max_retries (int): Número máximo de tentativas em caso de falha
        """
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.max_retries = max_retries
        self.logger = logging.getLogger(__name__)

        self._setup_components()

    def _setup_components(self):
        """Configura componentes básicos"""
        self.http_client = RequestsHttpClient(
            timeout=self.timeout,
            verify_ssl=self.verify_ssl,
            max_retries=self.max_retries
        )

        self.senior_authenticator = SeniorPlatformAuthenticator(self.http_client)
        self.gestaoponto_provider = GestaopontoTokenProvider(self.http_client)

    def authenticate(self, username: str, password: str, **kwargs) -> AuthenticationResult:
        """
        Realiza apenas autenticação na plataforma Senior

        Args:
            username (str): Email do usuário
            password (str): Senha do usuário
            **kwargs: Parâmetros adicionais (redirect_to, etc.)

        Returns:
            AuthenticationResult: Resultado da autenticação Senior
        """
        try:
            self.logger.info(f"Iniciando autenticação para usuário: {username}")

            result = self.senior_authenticator.authenticate(username, password, **kwargs)

            if result.success:
                self.logger.info("Autenticação Senior concluída com sucesso")
            else:
                self.logger.warning(f"Falha na autenticação: {result.error_message}")

            return result

        except Exception as e:
            self.logger.error(f"Erro durante autenticação: {str(e)}")
            raise

    def get_gestaoponto_token(self, auth_result: AuthenticationResult) -> Optional[str]:
        """
        Obtém token da API de Gestão de Ponto

        Args:
            auth_result (AuthenticationResult): Resultado da autenticação Senior

        Returns:
            str or None: Token da Gestão de Ponto ou None se erro
        """
        try:
            self.logger.info("Obtendo token da Gestão de Ponto")

            token = self.gestaoponto_provider.get_token(auth_result)

            if token:
                self.logger.info("Token da Gestão de Ponto obtido com sucesso")
            else:
                self.logger.warning("Falha ao obter token da Gestão de Ponto")

            return token

        except Exception as e:
            self.logger.error(f"Erro ao obter token da Gestão de Ponto: {str(e)}")
            raise

    def authenticate_complete(self, username: str, password: str, **kwargs) -> Dict[str, Any]:
        """
        Realiza autenticação completa: Senior + Gestão de Ponto

        Args:
            username (str): Email do usuário
            password (str): Senha do usuário
            **kwargs: Parâmetros adicionais

        Returns:
            Dict: Resultado completo com todos os tokens

        Example:
            {
                'success': True,
                'senior_token': 'token_senior_aqui',
                'gestaoponto_token': 'token_gestaoponto_aqui',
                'decoded_senior_token': {...},
                'user_info': {...},
                'error': None
            }
        """
        result = {
            'success': False,
            'senior_token': None,
            'gestaoponto_token': None,
            'decoded_senior_token': None,
            'user_info': None,
            'error': None
        }

        try:
            self.logger.info(f"Iniciando autenticação completa para: {username}")

            senior_result = self.authenticate(username, password, **kwargs)

            if not senior_result.success:
                result['error'] = senior_result.error_message
                return result

            result['senior_token'] = senior_result.senior_token
            result['decoded_senior_token'] = senior_result.get_decoded_token()
            result['user_info'] = senior_result.get_token_info()

            try:
                gestaoponto_token = self.get_gestaoponto_token(senior_result)
                result['gestaoponto_token'] = gestaoponto_token
                result['success'] = True

                if gestaoponto_token:
                    self.logger.info("Autenticação completa realizada com sucesso")
                else:
                    self.logger.warning("Autenticação Senior OK, mas falha no token Gestão de Ponto")

            except Exception as e:
                result['success'] = True
                result['error'] = f"Erro no token Gestão de Ponto: {str(e)}"
                self.logger.warning(f"Falha parcial: {result['error']}")

            return result

        except Exception as e:
            error_msg = f"Erro durante autenticação completa: {str(e)}"
            self.logger.error(error_msg)
            result['error'] = error_msg
            return result

    def close(self):
        """Fecha todos os recursos"""
        try:
            if hasattr(self, 'senior_authenticator'):
                self.senior_authenticator.close()
            if hasattr(self, 'gestaoponto_provider'):
                self.gestaoponto_provider.close()
            if hasattr(self, 'http_client'):
                self.http_client.close()
        except Exception as e:
            self.logger.warning(f"Erro ao fechar recursos: {str(e)}")

    def __enter__(self):
        """Suporte para context manager"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Cleanup automático"""
        self.close()


# ============================================================================
# FUNÇÕES DE CONVENIÊNCIA
# ============================================================================

def authenticate_senior(username: str, password: str, **kwargs) -> AuthenticationResult:
    """
    Função de conveniência para autenticação apenas na plataforma Senior

    Args:
        username (str): Email do usuário
        password (str): Senha do usuário
        **kwargs: Parâmetros adicionais

    Returns:
        AuthenticationResult: Resultado da autenticação
    """
    with SeniorAuth() as auth:
        return auth.authenticate(username, password, **kwargs)


def authenticate_complete(username: str, password: str, **kwargs) -> Dict[str, Any]:
    """
    Função de conveniência para autenticação completa

    Args:
        username (str): Email do usuário
        password (str): Senha do usuário
        **kwargs: Parâmetros adicionais

    Returns:
        Dict: Resultado completo com todos os tokens
    """
    with SeniorAuth() as auth:
        return auth.authenticate_complete(username, password, **kwargs)


# ============================================================================
# CONFIGURAÇÃO DE LOGGING
# ============================================================================

def setup_logging(level=logging.INFO):
    """
    Configura logging para o módulo

    Args:
        level: Nível de logging (logging.DEBUG, logging.INFO, etc.)
    """
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


# Auto-configurar logging ao importar
if __name__ != '__main__':
    setup_logging(logging.WARNING)
