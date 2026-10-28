"""Linha de comando do APISIGEduc.

  gerar-chave            gera SIGEDUC_CHAVE e APISIGEDUC_SEGREDO_SESSAO para o .env
  usuario ...            usuários da tela web (adicionar, listar, senha, ativar, desativar)
  credencial EMAIL       cadastra a senha do SIGEduc de um usuário
  web                    inicia a tela web (com o processamento da fila)
  worker                 processa a fila de envios separadamente
  login | mapear         testa o login / abre o SIGEduc com o Playwright Inspector
  notas | frequencia     lança uma planilha direto, sem passar pela fila
"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
from pathlib import Path

from .config import Config, ConfigError
from .planilhas import CONVERSORES, converter, ler_linhas

log = logging.getLogger("apisigeduc")


def _contexto():
    from .banco import criar_sessoes
    from .cripto import Cifra
    from .usuarios import RepositorioUsuarios

    config = Config.carregar()
    sessoes = criar_sessoes(config.banco_url)
    return config, sessoes, RepositorioUsuarios(sessoes, Cifra(config.chave))


def _pedir_senha(rotulo: str) -> str:
    senha = getpass.getpass(f"{rotulo}: ")
    if senha != getpass.getpass("Repita a senha: "):
        raise ValueError("As senhas não conferem")
    return senha


def cmd_gerar_chave(args) -> int:
    import secrets

    from .cripto import gerar_chave

    print(f"SIGEDUC_CHAVE={gerar_chave()}")
    print(f"APISIGEDUC_SEGREDO_SESSAO={secrets.token_urlsafe(48)}")
    print(
        "Copie para o .env SÓ as linhas que ainda não existem lá. Trocar uma SIGEDUC_CHAVE já em "
        "uso torna ilegíveis as senhas do SIGEduc cadastradas. Guarde uma cópia em local seguro.",
        file=sys.stderr,
    )
    return 0


def cmd_usuario(args) -> int:
    _, _, repo = _contexto()
    if args.acao == "adicionar":
        repo.criar(args.email, args.nome, _pedir_senha("Senha de acesso à tela web"), admin=args.admin)
        print(f"Usuário {args.email} criado{' (administrador)' if args.admin else ''}.")
    elif args.acao == "listar":
        usuarios = repo.listar()
        if not usuarios:
            print("Nenhum usuário. Crie o primeiro com: apisigeduc usuario adicionar EMAIL --admin")
        for u in usuarios:
            cred = repo.dados_credencial(u.id)
            papel = "admin" if u.admin else ""
            situacao = "ativo" if u.ativo else "desativado"
            sigeduc = cred.login if cred else "(sem senha do SIGEduc)"
            print(f"{u.email:<35} {u.nome:<25} {papel:<6} {situacao:<11} {sigeduc}")
    else:
        usuario = repo.por_email(args.email)
        if args.acao == "senha":
            repo.alterar_senha(usuario.id, _pedir_senha("Nova senha de acesso"))
            print(f"Senha de acesso de {usuario.email} alterada.")
        else:
            repo.definir_ativo(usuario.id, args.acao == "ativar")
            print(f"Usuário {usuario.email} {'ativado' if args.acao == 'ativar' else 'desativado'}.")
    return 0


def cmd_credencial(args) -> int:
    _, _, repo = _contexto()
    usuario = repo.por_email(args.email)
    repo.definir_credencial(usuario.id, args.login, _pedir_senha("Senha do SIGEduc"), args.escola)
    print(f"Senha do SIGEduc de {usuario.email} salva (criptografada).")
    return 0


def cmd_web(args) -> int:
    import uvicorn

    from .web.app import criar_app

    config, sessoes, repo = _contexto()
    app = criar_app(config, sessoes, repo, iniciar_worker=not args.sem_worker, cookie_seguro=args.https)
    print(f"Tela web em http://{args.host}:{args.porta}  (Ctrl+C para parar)")
    uvicorn.run(app, host=args.host, port=args.porta, log_level="info")
    return 0


def cmd_worker(args) -> int:
    import threading

    from .envios import rodar_worker

    config, sessoes, repo = _contexto()
    parar = threading.Event()
    try:
        rodar_worker(sessoes, repo, config, parar)
    except KeyboardInterrupt:
        parar.set()
    return 0


def _cliente(email: str, headless: bool | None = None):
    from dataclasses import replace

    from .cliente import SigeducCliente

    config, _, repo = _contexto()
    if headless is not None:
        config = replace(config, headless=headless)
    return SigeducCliente(config, repo.credencial(repo.por_email(email).id))


def cmd_login(args) -> int:
    with _cliente(args.usuario) as cliente:
        cliente.login()
    print("Login OK.")
    return 0


def cmd_mapear(args) -> int:
    """Abre o navegador logado com o Playwright Inspector para descobrir seletores."""
    with _cliente(args.usuario, headless=False) as cliente:
        cliente.login()
        cliente.page.pause()
    return 0


def cmd_lancar(args) -> int:
    from .envios import _executores

    resultado = converter(ler_linhas(args.arquivo), CONVERSORES[args.comando])
    for erro in resultado.erros:
        log.error("%s: %s", args.arquivo.name, erro)
    log.info("%d registro(s) válido(s), %d com erro", len(resultado.registros), len(resultado.erros))
    if resultado.erros and not args.ignorar_erros:
        log.error("Corrija a planilha ou use --ignorar-erros para lançar apenas as linhas válidas.")
        return 1
    if args.simular:
        for r in resultado.registros:
            print(r)
        print(f"Simulação: {len(resultado.registros)} registro(s) seriam lançados.")
        return 0
    if not args.usuario:
        log.error("Informe o e-mail do usuário com --usuario EMAIL.")
        return 1

    with _cliente(args.usuario) as cliente:
        cliente.login()
        relatorio = _executores()[args.comando](cliente, resultado.registros)
    for falha in relatorio.falhas:
        log.error(falha)
    print(relatorio.resumo())
    return 0 if not relatorio.falhas else 2


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apisigeduc", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log detalhado")
    sub = parser.add_subparsers(dest="comando", required=True)

    sub.add_parser("gerar-chave", help="gera as chaves do .env").set_defaults(func=cmd_gerar_chave)

    p = sub.add_parser("usuario", help="usuários da tela web")
    acoes = p.add_subparsers(dest="acao", required=True)
    a = acoes.add_parser("adicionar", help="cria um usuário (a senha é pedida em seguida)")
    a.add_argument("email")
    a.add_argument("--nome", default="")
    a.add_argument("--admin", action="store_true", help="pode criar e gerenciar usuários")
    acoes.add_parser("listar", help="lista os usuários")
    for acao, ajuda in (
        ("senha", "redefine a senha de acesso"),
        ("ativar", "reativa um usuário"),
        ("desativar", "bloqueia o acesso sem apagar o histórico"),
    ):
        acoes.add_parser(acao, help=ajuda).add_argument("email")
    p.set_defaults(func=cmd_usuario)

    p = sub.add_parser("credencial", help="cadastra a senha do SIGEduc de um usuário")
    p.add_argument("email", help="e-mail do usuário da tela web")
    p.add_argument("--login", required=True, help="login do SIGEduc")
    p.add_argument("--escola", default="")
    p.set_defaults(func=cmd_credencial)

    p = sub.add_parser("web", help="inicia a tela web")
    p.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 para aceitar acesso de outros computadores")
    p.add_argument("--porta", type=int, default=8000)
    p.add_argument("--sem-worker", action="store_true", help="não processar a fila neste processo")
    p.add_argument("--https", action="store_true", help="marcar o cookie como seguro (atrás de HTTPS)")
    p.set_defaults(func=cmd_web)

    sub.add_parser("worker", help="processa a fila de envios").set_defaults(func=cmd_worker)

    for nome, func, ajuda in (
        ("login", cmd_login, "testa o login no SIGEduc de um usuário"),
        ("mapear", cmd_mapear, "abre o SIGEduc logado com o Playwright Inspector"),
    ):
        p = sub.add_parser(nome, help=ajuda)
        p.add_argument("--usuario", required=True, help="e-mail do usuário da tela web")
        p.set_defaults(func=func)

    for nome, ajuda in (("notas", "lança notas de uma planilha"), ("frequencia", "lança frequência de uma planilha")):
        p = sub.add_parser(nome, help=ajuda)
        p.add_argument("arquivo", type=Path, help="planilha .csv ou .xlsx")
        p.add_argument("--usuario", help="e-mail do usuário da tela web")
        p.add_argument("--simular", action="store_true", help="só valida a planilha, sem acessar o SIGEduc")
        p.add_argument("--ignorar-erros", action="store_true", help="lança as linhas válidas mesmo se houver erros")
        p.set_defaults(func=cmd_lancar)
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
        log.error("%s", str(e).splitlines()[0] if str(e) else repr(e))
        log.error("Veja a captura de tela em screenshots/ ou rode de novo com -v para detalhes.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
