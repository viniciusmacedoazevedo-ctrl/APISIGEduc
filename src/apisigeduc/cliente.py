"""Sessão de navegador autenticada no SIGEduc (via Playwright)."""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Browser, Page, Playwright, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from . import seletores as S
from .config import Config
from .usuarios import Credencial

log = logging.getLogger(__name__)


def _duracao(segundos: int) -> str:
    return f"{segundos // 60} minuto(s)" if segundos >= 60 else f"{segundos} segundo(s)"


class ErroLogin(RuntimeError):
    pass


class SigeducCliente:
    """Uso:

        with SigeducCliente(config, credencial) as cliente:
            cliente.login()
            cliente.page.goto(...)
    """

    def __init__(
        self, config: Config, credencial: Credencial, pasta_capturas: Path = Path("screenshots")
    ):
        self.config = config
        self.credencial = credencial
        self.pasta_capturas = pasta_capturas
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self.page: Page

    def __enter__(self) -> "SigeducCliente":
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.config.headless,
            # português e sem a janela "traduzir esta página" do navegador
            args=["--lang=pt-BR", "--disable-features=Translate,TranslateUI"],
        )
        contexto = self._browser.new_context(locale="pt-BR")
        contexto.set_default_timeout(self.config.timeout_ms)
        self.page = contexto.new_page()
        return self

    def __exit__(self, tipo, valor, tb) -> None:
        if valor is not None:
            self.capturar_tela("erro")
            # com o navegador visível e alguém no terminal, deixa a tela aberta para ver o erro
            if not self.config.headless and sys.stdin and sys.stdin.isatty():
                try:
                    input(f"\nERRO: {valor}\nO navegador ficou aberto. Aperte Enter para fechar... ")
                except (EOFError, KeyboardInterrupt):
                    pass
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    def capturar_tela(self, prefixo: str) -> Path | None:
        """Salva imagem e HTML de cada aba aberta (o HTML mostra como a tela é montada)."""
        primeira = None
        try:
            self.pasta_capturas.mkdir(exist_ok=True)
            base = f"{prefixo}-{datetime.now():%Y%m%d-%H%M%S}"
            for i, pagina in enumerate(self.page.context.pages):
                nome = base if i == 0 else f"{base}-aba{i + 1}"
                imagem = self.pasta_capturas / f"{nome}.png"
                pagina.screenshot(path=str(imagem), full_page=True)
                (self.pasta_capturas / f"{nome}.html").write_text(
                    f"<!-- {pagina.url} -->\n{pagina.content()}", encoding="utf-8"
                )
                log.info("Captura de tela salva em %s (e .html)", imagem)
                primeira = primeira or imagem
        except Exception:  # captura é só diagnóstico; não deve mascarar o erro original
            log.debug("Falha ao capturar tela", exc_info=True)
        return primeira

    def login(self) -> None:
        log.info("Acessando %s", self.config.url)
        self.page.goto(self.config.url + S.LOGIN_PATH)
        self.page.fill(S.LOGIN_USUARIO, self.credencial.login)
        self.page.fill(S.LOGIN_SENHA, self.credencial.senha)

        if self._tem_verificacao() and not self.config.headless:
            # não adianta clicar em Entrar antes de a pessoa resolver a verificação
            self._esperar_pessoa()
        else:
            self._enviar_login()
            if self._na_tela_login() and not self.config.headless:
                self._esperar_pessoa()  # verificação apareceu só depois do clique

        if self._na_tela_login():
            raise ErroLogin(f"Não foi possível entrar no SIGEduc: {self._motivo_falha_login()}")
        log.info("Login realizado como %s (%s)", self.credencial.login, self.page.url)

    def _enviar_login(self) -> None:
        botao = self.page.locator(S.LOGIN_ENTRAR)
        # espera a resposta do SIGEduc: sem isso a página de login ainda estaria
        # na tela quando o resultado fosse conferido
        try:
            with self.page.expect_navigation(wait_until="load", timeout=min(self.config.timeout_ms, 15000)):
                if botao.count() > 0:
                    botao.first.click()
                else:
                    log.debug("Botão de login não encontrado; enviando o formulário diretamente")
                    self.page.locator(S.LOGIN_SENHA).evaluate("campo => campo.form.requestSubmit()")
        except PlaywrightTimeoutError:
            log.debug("O envio do login não mudou de página")
        self.esperar_carregar()

    def _na_tela_login(self) -> bool:
        return self.page.locator(S.LOGIN_SENHA).count() > 0 and self.page.locator(S.LOGADO).count() == 0

    def _tem_verificacao(self) -> bool:
        return self.page.locator(S.LOGIN_VERIFICACAO).count() > 0

    def _esperar_pessoa(self) -> None:
        """Espera alguém resolver a verificação humana e entrar, na janela do navegador."""
        aviso = (
            f"\n>>> O SIGEduc pediu uma verificação no login de {self.credencial.login}.\n"
            f">>> Na janela do navegador, resolva a verificação e clique em Entrar.\n"
            f">>> Aguardando até {_duracao(self.config.espera_verificacao_s)}...\n"
        )
        print(aviso, file=sys.stderr, flush=True)
        try:
            self.page.locator(S.LOGIN_SENHA).wait_for(
                state="detached", timeout=self.config.espera_verificacao_s * 1000
            )
        except PlaywrightTimeoutError:
            raise ErroLogin("A verificação do login não foi concluída a tempo.") from None
        self.esperar_carregar()
        log.info("Verificação concluída")

    def esperar_carregar(self) -> None:
        """Espera a rede acalmar, sem falhar em páginas que nunca ficam totalmente paradas."""
        try:
            self.page.wait_for_load_state("networkidle", timeout=min(self.config.timeout_ms, 10000))
        except PlaywrightTimeoutError:
            pass

    def _motivo_falha_login(self) -> str:
        if self._tem_verificacao() and self.config.headless:
            return (
                "o SIGEduc pediu uma verificação humana (\"não sou um robô\"). Rode com o "
                "navegador visível (SIGEDUC_HEADLESS=false) para resolvê-la na janela"
            )
        erro = self.page.locator(S.LOGIN_ERRO)
        if erro.count() and erro.first.inner_text().strip():
            return erro.first.inner_text().strip()
        texto = re.sub(r"\s+", " ", self.page.locator("body").inner_text())
        frase = re.search(r"[^.!:]*(inv[aá]lid|incorret|bloquead|captcha|expirad|n[aã]o confere)[^.!]*", texto, re.I)
        if frase:
            return frase.group(0).strip()
        return f"o SIGEduc continuou na tela de login ({self.page.url}, título: {self.page.title()!r})"

    def pausa(self) -> None:
        self.page.wait_for_timeout(self.config.pausa_ms)
