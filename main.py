#!/usr/bin/env python3
"""
Envio em Massa de Afastamentos para API Senior

Este script permite o envio em massa de afastamentos para a API de Gestao de Ponto
da Senior, utilizando dados de arquivos CSV.

Uso:
    python main.py
"""

import os
import sys
import csv
import time
import json
import threading
import requests
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from senior_auth import authenticate_complete, get_credentials

# Configuracoes
BASE_URL = "https://webp20.seniorcloud.com.br:31531"
EMPRESA_FILIAL = os.getenv("EMPRESA_FILIAL", "303-1")  # Prefixo empresa-filial para identificador do colaborador
DELAY_ENTRE_REQUISICOES = 0.5  # segundos (usado apenas com 1 thread)
MAX_THREADS = 20
INPUT_DIR = "input"
OUTPUT_DIR = "output"


def clear_screen():
    """Limpa a tela do terminal"""
    os.system('cls' if os.name == 'nt' else 'clear')


def print_header():
    """Exibe cabecalho do programa"""
    print("=" * 60)
    print("  ENVIO EM MASSA DE AFASTAMENTOS - SENIOR")
    print("=" * 60)
    print()


def print_menu():
    """Exibe menu principal"""
    print("\nOpcoes:")
    print("1 - Iniciar envio de afastamentos")
    print("2 - Reapurar afastamentos")
    print("3 - Criar arquivo de exemplo")
    print("0 - Sair")
    print()


def converter_data(data_str: str) -> str:
    """
    Converte data para formato YYYY-MM-DD

    Aceita formatos:
    - DD/MM/YYYY
    - YYYY-MM-DD
    """
    data_str = data_str.strip()

    # Ja esta no formato correto
    if len(data_str) == 10 and data_str[4] == '-':
        return data_str

    # Formato DD/MM/YYYY
    if '/' in data_str:
        partes = data_str.split('/')
        if len(partes) == 3:
            dia, mes, ano = partes
            return f"{ano}-{mes.zfill(2)}-{dia.zfill(2)}"

    raise ValueError(f"Formato de data invalido: {data_str}")


def listar_arquivos_csv() -> List[str]:
    """Lista arquivos CSV na pasta input"""
    if not os.path.exists(INPUT_DIR):
        os.makedirs(INPUT_DIR)
        return []

    arquivos = [f for f in os.listdir(INPUT_DIR) if f.endswith('.csv')]
    arquivos.sort()
    return arquivos


def selecionar_arquivo() -> Optional[str]:
    """Permite usuario selecionar arquivo CSV por numero"""
    arquivos = listar_arquivos_csv()

    if not arquivos:
        print(f"\nNenhum arquivo CSV encontrado na pasta '{INPUT_DIR}/'")
        print("Coloque seus arquivos CSV nessa pasta e tente novamente.")
        print("Ou use a opcao 2 para criar um arquivo de exemplo.")
        return None

    print(f"\nArquivos disponiveis na pasta '{INPUT_DIR}/':")
    print("-" * 40)

    for i, arquivo in enumerate(arquivos, 1):
        print(f"  {i} - {arquivo}")

    print()

    while True:
        try:
            opcao = input("Digite o numero do arquivo (0 para cancelar): ").strip()

            if opcao == '0':
                return None

            indice = int(opcao) - 1

            if 0 <= indice < len(arquivos):
                return os.path.join(INPUT_DIR, arquivos[indice])
            else:
                print("Numero invalido. Tente novamente.")
        except ValueError:
            print("Digite apenas numeros.")


def ler_csv(caminho: str) -> Tuple[List[Dict], List[str]]:
    """
    Le arquivo CSV e retorna lista de registros

    Returns:
        Tuple[List[Dict], List[str]]: (registros, erros_leitura)
    """
    registros = []
    erros = []

    with open(caminho, 'r', encoding='utf-8') as f:
        # Detectar delimitador
        amostra = f.read(1024)
        f.seek(0)

        if ';' in amostra:
            delimitador = ';'
        else:
            delimitador = ','

        reader = csv.DictReader(f, delimiter=delimitador)

        for i, linha in enumerate(reader, 2):  # 2 porque linha 1 e cabecalho
            try:
                # Normalizar nomes das colunas (remover espacos, lowercase)
                linha_normalizada = {k.strip().lower(): v.strip() for k, v in linha.items() if k}

                # Buscar campos com diferentes nomes possiveis
                matricula = linha_normalizada.get('matricula') or linha_normalizada.get('matrícula')
                data = linha_normalizada.get('data')
                codigo = linha_normalizada.get('codigo_situacao') or linha_normalizada.get('código_situacao') or linha_normalizada.get('codigo')

                if not matricula or not data or not codigo:
                    erros.append(f"Linha {i}: Campos obrigatorios faltando (matricula, data, codigo_situacao)")
                    continue

                # Converter data
                try:
                    data_formatada = converter_data(data)
                except ValueError as e:
                    erros.append(f"Linha {i}: {e}")
                    continue

                registros.append({
                    'matricula': matricula,
                    'data': data_formatada,
                    'codigo_situacao': int(codigo),
                    'linha_original': i
                })

            except Exception as e:
                erros.append(f"Linha {i}: Erro ao processar - {str(e)}")

    return registros, erros


def criar_payload(codigo_situacao: int, data: str) -> dict:
    """Cria payload para envio do afastamento"""
    return {
        "situacao": {"codigo": codigo_situacao},
        "dataInicial": data,
        "horaInicial": "00:00",
        "documentos": [],
        "dataFinal": data,
        "horaFinal": "00:00",
        "riscoNexo": None,
        "mesmoMotivo": False,
        "customizacao": []
    }


def enviar_afastamento(token: str, matricula: str, codigo_calculo: int, payload: dict) -> Tuple[bool, str]:
    """
    Envia afastamento para a API

    Returns:
        Tuple[bool, str]: (sucesso, mensagem)
    """
    url = f"{BASE_URL}/gestaoponto-backend/api/colaboradores/{EMPRESA_FILIAL}-{matricula}/historicos/afastamentos/"

    params = {
        "codigoCalculo": codigo_calculo,
        "forcarRecalculo": "true",
        "gestor": "S"
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:142.0) Gecko/20100101 Firefox/142.0",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "pt-BR,pt;q=0.8,en-US;q=0.5,en;q=0.3",
        "Content-Type": "application/json;charset=utf-8",
        "assertion": token,
        "Origin": BASE_URL,
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin"
    }

    try:
        response = requests.post(
            url,
            params=params,
            headers=headers,
            json=payload,
            timeout=30
        )

        if response.status_code == 200 or response.status_code == 201:
            return True, "OK"
        else:
            # Tentar extrair mensagem de erro da resposta
            try:
                erro_json = response.json()
                if isinstance(erro_json, dict):
                    mensagem = erro_json.get('message') or erro_json.get('error') or str(erro_json)
                else:
                    mensagem = str(erro_json)
            except:
                mensagem = response.text[:200] if response.text else f"HTTP {response.status_code}"

            return False, mensagem

    except requests.exceptions.Timeout:
        return False, "Timeout na requisicao"
    except requests.exceptions.ConnectionError:
        return False, "Erro de conexao"
    except Exception as e:
        return False, str(e)


def reapurar_afastamento(token: str, matricula: str, codigo_calculo: int, data: str, payload: dict) -> Tuple[bool, str]:
    """
    Reapura um afastamento na API Senior

    Returns:
        Tuple[bool, str]: (sucesso, mensagem)
    """
    # Identificador do afastamento: empresa-filial-matricula-data-hora
    afastamento_id = f"{EMPRESA_FILIAL}-{matricula}-{data}-00:00"

    url = f"{BASE_URL}/gestaoponto-backend/api/colaboradores/{EMPRESA_FILIAL}-{matricula}/historicos/afastamentos/{afastamento_id}"

    params = {
        "codigoCalculo": codigo_calculo,
        "dataAcerto": data,
        "forcarRecalculo": "false",
        "gestor": "S"
    }

    headers = {
        "Host": "webp20.seniorcloud.com.br:31531",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:145.0) Gecko/20100101 Firefox/145.0",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "pt-BR,pt;q=0.8,en-US;q=0.5,en;q=0.3",
        "Content-Type": "application/json;charset=utf-8",
        "assertion": token,
        "Origin": BASE_URL,
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin"
    }

    try:
        response = requests.put(
            url,
            params=params,
            headers=headers,
            json=payload,
            timeout=30
        )

        if response.status_code == 200 or response.status_code == 201:
            return True, "OK"
        else:
            # Tentar extrair mensagem de erro da resposta
            try:
                erro_json = response.json()
                if isinstance(erro_json, dict):
                    mensagem = erro_json.get('message') or erro_json.get('error') or str(erro_json)
                else:
                    mensagem = str(erro_json)
            except:
                mensagem = response.text[:200] if response.text else f"HTTP {response.status_code}"

            return False, mensagem

    except requests.exceptions.Timeout:
        return False, "Timeout na requisicao"
    except requests.exceptions.ConnectionError:
        return False, "Erro de conexao"
    except Exception as e:
        return False, str(e)


def salvar_erros(erros: List[Dict], nome_arquivo_origem: str):
    """Salva arquivo de erros"""
    if not erros:
        return None

    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome_base = os.path.splitext(os.path.basename(nome_arquivo_origem))[0]
    nome_arquivo = f"erros_{nome_base}_{timestamp}.csv"
    caminho = os.path.join(OUTPUT_DIR, nome_arquivo)

    with open(caminho, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['matricula', 'data', 'codigo_situacao', 'erro', 'timestamp'], delimiter=';')
        writer.writeheader()
        writer.writerows(erros)

    return caminho


def print_progress_bar(current: int, total: int, prefix: str = '', suffix: str = '', length: int = 40):
    """Exibe barra de progresso"""
    percent = 100 * (current / float(total))
    filled = int(length * current // total)
    bar = '#' * filled + '-' * (length - filled)
    print(f'\r{prefix} [{bar}] {percent:.1f}% {suffix}', end='', flush=True)


class ResultadosThreadSafe:
    """Classe para armazenar resultados de forma thread-safe"""

    def __init__(self, total: int, mostrar_logs: bool = True):
        self.lock = threading.Lock()
        self.sucessos = 0
        self.erros = []
        self.processados = 0
        self.total = total
        self.mostrar_logs = mostrar_logs

    def log(self, mensagem: str):
        """Exibe log de forma thread-safe"""
        if self.mostrar_logs:
            with self.lock:
                print(mensagem)

    def adicionar_sucesso(self, matricula: str, data: str):
        with self.lock:
            self.sucessos += 1
            self.processados += 1
            if self.mostrar_logs:
                timestamp = datetime.now().strftime("%H:%M:%S")
                print(f"[{timestamp}] OK     | {matricula} | {data}")

    def adicionar_erro(self, erro: Dict):
        with self.lock:
            self.erros.append(erro)
            self.processados += 1
            if self.mostrar_logs:
                timestamp = datetime.now().strftime("%H:%M:%S")
                print(f"[{timestamp}] ERRO   | {erro['matricula']} | {erro['data']} | {erro['erro']}")

    def get_processados(self) -> int:
        with self.lock:
            return self.processados


def processar_registro(registro: Dict, token: str, codigo_calculo: int, resultados: ResultadosThreadSafe):
    """Processa um unico registro (funcao worker para threads)"""
    matricula = registro['matricula']
    data = registro['data']
    codigo_situacao = registro['codigo_situacao']

    payload = criar_payload(codigo_situacao, data)
    sucesso, mensagem = enviar_afastamento(token, matricula, codigo_calculo, payload)

    if sucesso:
        resultados.adicionar_sucesso(matricula, data)
    else:
        resultados.adicionar_erro({
            'matricula': matricula,
            'data': data,
            'codigo_situacao': codigo_situacao,
            'erro': mensagem,
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })


def processar_registro_reapuracao(registro: Dict, token: str, codigo_calculo: int, resultados: ResultadosThreadSafe):
    """Processa um unico registro de reapuracao (funcao worker para threads)"""
    matricula = registro['matricula']
    data = registro['data']
    codigo_situacao = registro['codigo_situacao']

    payload = criar_payload(codigo_situacao, data)
    sucesso, mensagem = reapurar_afastamento(token, matricula, codigo_calculo, data, payload)

    if sucesso:
        resultados.adicionar_sucesso(matricula, data)
    else:
        resultados.adicionar_erro({
            'matricula': matricula,
            'data': data,
            'codigo_situacao': codigo_situacao,
            'erro': mensagem,
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })


def executar_envio(token: str):
    """Executa o fluxo de envio de afastamentos"""
    # Selecionar arquivo
    arquivo = selecionar_arquivo()
    if not arquivo:
        return

    # Solicitar codigo de calculo
    print()
    while True:
        try:
            codigo_calculo = input("Digite o codigo de calculo: ").strip()
            codigo_calculo = int(codigo_calculo)
            break
        except ValueError:
            print("Digite apenas numeros.")

    # Solicitar numero de threads
    print()
    while True:
        try:
            num_threads_str = input(f"Numero de threads (1-{MAX_THREADS}): ").strip()
            num_threads = int(num_threads_str)
            if 1 <= num_threads <= MAX_THREADS:
                break
            else:
                print(f"Digite um numero entre 1 e {MAX_THREADS}.")
        except ValueError:
            print("Digite apenas numeros.")

    # Ler CSV
    print(f"\nLendo arquivo: {arquivo}")
    registros, erros_leitura = ler_csv(arquivo)

    if erros_leitura:
        print(f"\nAvisos durante leitura do arquivo:")
        for erro in erros_leitura[:5]:  # Mostrar apenas os 5 primeiros
            print(f"  - {erro}")
        if len(erros_leitura) > 5:
            print(f"  ... e mais {len(erros_leitura) - 5} avisos")

    if not registros:
        print("\nNenhum registro valido encontrado no arquivo.")
        return

    print(f"\nRegistros validos encontrados: {len(registros)}")
    print(f"Threads a utilizar: {num_threads}")

    # Confirmacao
    confirma = input("\nDeseja iniciar o envio? (s/n): ").strip().lower()
    if confirma != 's':
        print("Operacao cancelada.")
        return

    # Processar registros
    print("\nIniciando envio...\n")
    print("-" * 70)
    print(f"{'HORA':<10} | {'STATUS':<6} | {'MATRICULA':<15} | {'DATA':<12} | DETALHE")
    print("-" * 70)

    resultados = ResultadosThreadSafe(len(registros), mostrar_logs=True)

    if num_threads == 1:
        # Modo sequencial com delay
        for i, registro in enumerate(registros, 1):
            processar_registro(registro, token, codigo_calculo, resultados)

            if i < len(registros):
                time.sleep(DELAY_ENTRE_REQUISICOES)
    else:
        # Modo multi-thread
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [
                executor.submit(processar_registro, registro, token, codigo_calculo, resultados)
                for registro in registros
            ]

            # Aguardar todas as threads terminarem
            for future in as_completed(futures):
                pass  # Os logs sao exibidos dentro de processar_registro

    print("-" * 70)

    # Resumo
    print("=" * 60)
    print("RESUMO DO PROCESSAMENTO")
    print("=" * 60)
    print(f"Total de registros: {len(registros)}")
    print(f"Enviados com sucesso: {resultados.sucessos}")
    print(f"Erros: {len(resultados.erros)}")

    # Salvar erros
    if resultados.erros:
        arquivo_erros = salvar_erros(resultados.erros, arquivo)
        print(f"\nArquivo de erros salvo em: {arquivo_erros}")

    print()


def executar_reapuracao(token: str):
    """Executa o fluxo de reapuracao de afastamentos"""
    # Selecionar arquivo
    arquivo = selecionar_arquivo()
    if not arquivo:
        return

    # Solicitar codigo de calculo
    print()
    while True:
        try:
            codigo_calculo = input("Digite o codigo de calculo: ").strip()
            codigo_calculo = int(codigo_calculo)
            break
        except ValueError:
            print("Digite apenas numeros.")

    # Solicitar numero de threads
    print()
    while True:
        try:
            num_threads_str = input(f"Numero de threads (1-{MAX_THREADS}): ").strip()
            num_threads = int(num_threads_str)
            if 1 <= num_threads <= MAX_THREADS:
                break
            else:
                print(f"Digite um numero entre 1 e {MAX_THREADS}.")
        except ValueError:
            print("Digite apenas numeros.")

    # Ler CSV
    print(f"\nLendo arquivo: {arquivo}")
    registros, erros_leitura = ler_csv(arquivo)

    if erros_leitura:
        print(f"\nAvisos durante leitura do arquivo:")
        for erro in erros_leitura[:5]:  # Mostrar apenas os 5 primeiros
            print(f"  - {erro}")
        if len(erros_leitura) > 5:
            print(f"  ... e mais {len(erros_leitura) - 5} avisos")

    if not registros:
        print("\nNenhum registro valido encontrado no arquivo.")
        return

    print(f"\nRegistros validos encontrados: {len(registros)}")
    print(f"Threads a utilizar: {num_threads}")

    # Confirmacao
    confirma = input("\nDeseja iniciar a reapuracao? (s/n): ").strip().lower()
    if confirma != 's':
        print("Operacao cancelada.")
        return

    # Processar registros
    print("\nIniciando reapuracao...\n")
    print("-" * 70)
    print(f"{'HORA':<10} | {'STATUS':<6} | {'MATRICULA':<15} | {'DATA':<12} | DETALHE")
    print("-" * 70)

    resultados = ResultadosThreadSafe(len(registros), mostrar_logs=True)

    if num_threads == 1:
        # Modo sequencial com delay
        for i, registro in enumerate(registros, 1):
            processar_registro_reapuracao(registro, token, codigo_calculo, resultados)

            if i < len(registros):
                time.sleep(DELAY_ENTRE_REQUISICOES)
    else:
        # Modo multi-thread
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [
                executor.submit(processar_registro_reapuracao, registro, token, codigo_calculo, resultados)
                for registro in registros
            ]

            # Aguardar todas as threads terminarem
            for future in as_completed(futures):
                pass  # Os logs sao exibidos dentro de processar_registro_reapuracao

    print("-" * 70)

    # Resumo
    print("=" * 60)
    print("RESUMO DO PROCESSAMENTO")
    print("=" * 60)
    print(f"Total de registros: {len(registros)}")
    print(f"Reapurados com sucesso: {resultados.sucessos}")
    print(f"Erros: {len(resultados.erros)}")

    # Salvar erros
    if resultados.erros:
        arquivo_erros = salvar_erros(resultados.erros, arquivo)
        print(f"\nArquivo de erros salvo em: {arquivo_erros}")

    print()


def criar_arquivo_exemplo():
    """Cria arquivo CSV de exemplo"""
    if not os.path.exists(INPUT_DIR):
        os.makedirs(INPUT_DIR)

    caminho = os.path.join(INPUT_DIR, "exemplo_afastamentos.csv")

    conteudo = """matricula;data;codigo_situacao
303-1-12345;01/01/2025;15
303-1-67890;02/01/2025;15
303-1-11111;03/01/2025;221
303-1-22222;2025-01-04;15"""

    with open(caminho, 'w', encoding='utf-8') as f:
        f.write(conteudo)

    print(f"\nArquivo de exemplo criado: {caminho}")
    print("\nFormato do arquivo:")
    print("  - Separador: ; (ponto e virgula)")
    print("  - Colunas: matricula, data, codigo_situacao")
    print("  - Data: DD/MM/YYYY ou YYYY-MM-DD")
    print()


def main():
    """Funcao principal"""
    clear_screen()
    print_header()

    # Autenticacao
    print("Iniciando autenticacao...\n")

    try:
        username, password = get_credentials()
        result = authenticate_complete(username, password)

        if not result['success']:
            print(f"\nErro na autenticacao: {result['error']}")
            sys.exit(1)

        if not result['gestaoponto_token']:
            print("\nErro: Nao foi possivel obter o token de Gestao de Ponto")
            sys.exit(1)

        token = result['gestaoponto_token']
        user_info = result['user_info']['user_info']

        print(f"\nAutenticado como: {user_info['full_name']} ({user_info['email']})")

    except KeyboardInterrupt:
        print("\n\nOperacao cancelada pelo usuario.")
        sys.exit(0)
    except Exception as e:
        print(f"\nErro durante autenticacao: {e}")
        sys.exit(1)

    # Menu principal
    while True:
        print_menu()
        opcao = input("Escolha uma opcao: ").strip()

        if opcao == '1':
            executar_envio(token)
        elif opcao == '2':
            executar_reapuracao(token)
        elif opcao == '3':
            criar_arquivo_exemplo()
        elif opcao == '0':
            print("\nAte logo!")
            break
        else:
            print("\nOpcao invalida. Tente novamente.")

        input("\nPressione ENTER para continuar...")
        clear_screen()
        print_header()
        print(f"Autenticado como: {user_info['full_name']}")


if __name__ == "__main__":
    main()
