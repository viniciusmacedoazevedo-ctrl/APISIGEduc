"""Linha de comando: apisigeduc {gerar-chave,usuario,login,mapear,notas,frequencia}."""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
from pathlib import Path

from .config import Config, ConfigError
from .planilhas import converter, frequencia_de_linha, ler_linhas, nota_de_linha

log = logging.getLogger("apisigeduc")


def _carregar(arquivo: Path, conversor):
    resultado = converter(ler_linhas(arquivo), conversor)
    for erro in resultado.erros:
        log.error("%s: %s", arquivo.name, erro)
    log.info("%d registro(s) válido(s), %d com erro", len(resultado.registros), len(resultado.erros))
    return resultado


def _repositorio(config: Config):
    from .banco import criar_sessoes
    from .cripto import Cifra
    from .usuarios import RepositorioUsuarios

    return RepositorioUsuarios(criar_sessoes(config.banco_url), Cifra(config.chave))


def _pedir_senha() -> str:
    senha = getpass.getpass("Senha do SIGEduc: ")
    if senha != getpass.getpass("Repita a senha: "):
        raise ValueError("As senhas não conferem")
    return senha


def cmd_gerar_chave(args) -> int:
    from .cripto import gerar_chave

    print(gerar_chave())
    print("Copie a linha acima para o .env como SIGEDUC_CHAVE=... e guarde uma cópia em local seguro.",
          file=sys.stderr)
    return 0


def cmd_usuario(args) -> int:
    repo = _repositorio(Config.carregar())
    if args.acao == "adicionar":
        repo.adicionar(args.login, _pedir_senha(), nome=args.nome, escola=args.escola)
        print(f"Usuário {args.login} cadastrado.")
    elif args.acao == "senha":
        repo.alterar_senha(args.login, _pedir_senha())
        print(f"Senha de {args.login} alterada.")
    elif args.acao in ("ativar", "desativar"):
        repo.definir_ativo(args.login, args.acao == "ativar")
        print(f"Usuário {args.login} {args.acao.replace('ar', 'ado')}.")
    elif args.acao == "remover":
        repo.remover(args.login)
        print(f"Usuário {args.login} removido.")
    elif args.acao == "listar":
        usuarios = repo.listar()
        if not usuarios:
            print("Nenhum usuário cadastrado. Use: apisigeduc usuario adicionar LOGIN")
        for u in usuarios:
            situacao = "ativo" if u.ativo else "desativado"
            print(f"{u.login:<25} {situacao:<11} {u.nome:<30} {u.escola}")
    return 0


def _cliente(config: Config, login: str):
    from .cliente import SigeducCliente

    return SigeducCliente(config, _repositorio(config).credencial(login))


def cmd_login(args) -> int:
    with _cliente(Config.carregar(), args.usuario) as cliente:
        cliente.login()
    print("Login OK.")
    return 0


def cmd_mapear(args) -> int:
    """Abre o navegador logado com o Playwright Inspector para descobrir seletores."""
    from dataclasses import replace

    with _cliente(replace(Config.carregar(), headless=False), args.usuario) as cliente:
        cliente.login()
        cliente.page.pause()
    return 0


def _cmd_lancar(args, conversor, executar) -> int:
    resultado = _carregar(args.arquivo, conversor)
    if resultado.erros and not args.ignorar_erros:
        log.error("Corrija a planilha ou use --ignorar-erros para lançar apenas as linhas válidas.")
        return 1
    if args.simular:
        for r in resultado.registros:
            print(r)
        print(f"Simulação: {len(resultado.registros)} registro(s) seriam lançados.")
        return 0
    if not args.usuario:
        log.error("Informe o usuário do SIGEduc com --usuario LOGIN.")
        return 1

    with _cliente(Config.carregar(), args.usuario) as cliente:
        cliente.login()
        relatorio = executar(cliente, resultado.registros)
    for falha in relatorio.falhas:
        log.error(falha)
    print(relatorio.resumo())
    return 0 if not relatorio.falhas else 2


def cmd_notas(args) -> int:
    from .tarefas.notas import lancar_notas

    return _cmd_lancar(args, nota_de_linha, lancar_notas)


def cmd_frequencia(args) -> int:
    from .tarefas.frequencia import lancar_frequencia

    return _cmd_lancar(args, frequencia_de_linha, lancar_frequencia)


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apisigeduc", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="log detalhado")
    sub = parser.add_subparsers(dest="comando", required=True)

    sub.add_parser("gerar-chave", help="gera a chave que cifra as senhas (SIGEDUC_CHAVE)").set_defaults(
        func=cmd_gerar_chave
    )

    p = sub.add_parser("usuario", help="cadastro dos usuários do SIGEduc")
    acoes = p.add_subparsers(dest="acao", required=True)
    a = acoes.add_parser("adicionar", help="cadastra um usuário (a senha é pedida em seguida)")
    a.add_argument("login")
    a.add_argument("--nome", default="", help="nome da pessoa")
    a.add_argument("--escola", default="")
    acoes.add_parser("listar", help="lista os usuários cadastrados")
    for acao, ajuda in (
        ("senha", "altera a senha de um usuário"),
        ("ativar", "reativa um usuário"),
        ("desativar", "impede o uso do usuário sem apagá-lo"),
        ("remover", "apaga o usuário do banco"),
    ):
        acoes.add_parser(acao, help=ajuda).add_argument("login")
    p.set_defaults(func=cmd_usuario)

    for nome, func, ajuda in (
        ("login", cmd_login, "testa o login de um usuário cadastrado"),
        ("mapear", cmd_mapear, "abre o SIGEduc logado com o Playwright Inspector"),
    ):
        p = sub.add_parser(nome, help=ajuda)
        p.add_argument("--usuario", required=True, help="login do SIGEduc cadastrado no banco")
        p.set_defaults(func=func)

    for nome, func, ajuda in (
        ("notas", cmd_notas, "lança notas a partir de uma planilha"),
        ("frequencia", cmd_frequencia, "lança frequência a partir de uma planilha"),
    ):
        p = sub.add_parser(nome, help=ajuda)
        p.add_argument("arquivo", type=Path, help="planilha .csv ou .xlsx")
        p.add_argument("--usuario", help="login do SIGEduc cadastrado no banco")
        p.add_argument("--simular", action="store_true", help="só valida a planilha, sem acessar o SIGEduc")
        p.add_argument("--ignorar-erros", action="store_true", help="lança as linhas válidas mesmo se houver erros")
        p.set_defaults(func=func)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = criar_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        return args.func(args)
    except (ConfigError, LookupError, ValueError) as e:
        log.error(str(e))
        return 1
    except Exception as e:
        if args.verbose:
            raise
        log.error("%s", str(e).splitlines()[0])
        log.error("Veja a captura de tela em screenshots/ ou rode de novo com -v para detalhes.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
