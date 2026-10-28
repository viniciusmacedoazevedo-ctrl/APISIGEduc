"""Linha de comando: apisigeduc {login,mapear,notas,frequencia}."""

from __future__ import annotations

import argparse
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


def cmd_login(args) -> int:
    from .cliente import SigeducCliente

    with SigeducCliente(Config.carregar()) as cliente:
        cliente.login()
    print("Login OK.")
    return 0


def cmd_mapear(args) -> int:
    """Abre o navegador logado com o Playwright Inspector para descobrir seletores."""
    from dataclasses import replace

    from .cliente import SigeducCliente

    with SigeducCliente(replace(Config.carregar(), headless=False)) as cliente:
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

    from .cliente import SigeducCliente

    with SigeducCliente(Config.carregar()) as cliente:
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

    sub.add_parser("login", help="testa as credenciais do .env").set_defaults(func=cmd_login)
    sub.add_parser("mapear", help="abre o SIGEduc logado com o Playwright Inspector").set_defaults(
        func=cmd_mapear
    )

    for nome, func, ajuda in (
        ("notas", cmd_notas, "lança notas a partir de uma planilha"),
        ("frequencia", cmd_frequencia, "lança frequência a partir de uma planilha"),
    ):
        p = sub.add_parser(nome, help=ajuda)
        p.add_argument("arquivo", type=Path, help="planilha .csv ou .xlsx")
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
    except ConfigError as e:
        log.error(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
